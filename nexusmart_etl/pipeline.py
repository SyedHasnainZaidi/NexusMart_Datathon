"""Task functions used by the Airflow DAG (and by scripts/run_pipeline.py).

Tasks hand data to each other through files in WORK_DIR, so each Airflow task
can run in its own process and be retried on its own:

  extract          raw CSV -> work/raw_snapshot.pkl
  validate_raw     structure + profile -> dq/raw_profile.json   (fails on structural errors)
  clean            -> processed/*.csv, rejects/reject_records.csv, dq/rule_log.csv
  load_staging     schema DDL + COPY processed files into staging.*, dq.*
  build_dimensions sql/02_dimensions.sql
  build_fact       sql/03_fact.sql (+03b in full-load mode)
  quality_checks   sql/04_quality_checks.sql + reject-rate thresholds (fails on breach)
"""
import json
import logging
import os
import pickle
from datetime import datetime

import pandas as pd

from . import config
from .clean import clean_all
from .db import Database
from .extract import extract as _extract
from .extract import save_profile, validate_raw as _validate
from .utils import DQLog

log = logging.getLogger("nexusmart")

def _snapshot():
    return config.WORK_DIR / "work" / "raw_snapshot.pkl"

STAGING_TABLES = {
    "locations": "staging.locations", "sellers": "staging.sellers", "products": "staging.products",
    "customers": "staging.customers", "orders": "staging.orders", "order_items": "staging.order_items",
    "payments": "staging.payments", "shipping": "staging.shipping", "returns": "staging.returns",
    "customer_id_map": "dq.customer_id_map", "product_id_map": "dq.product_id_map",
}
# expected rejects that are de-duplication, not data loss
DEDUP_REASONS = {"EXACT_DUPLICATE", "MERGED_DUPLICATE"}


def _run_id(run_id=None):
    return run_id or os.getenv("NEXUSMART_RUN_ID") or datetime.now().strftime("manual_%Y%m%dT%H%M%S")


def _ensure_dirs():
    for d in [config.PROCESSED_DIR, config.REJECT_DIR, config.DQ_DIR, _snapshot().parent]:
        d.mkdir(parents=True, exist_ok=True)


def _log_run(db, run_id, task, table, read=None, rejected=None, loaded=None):
    log.info("[%s] %s %s read=%s rejected=%s loaded=%s", run_id, task, table, read, rejected, loaded)
    if db is None:
        return
    def v(x):
        return "NULL" if x is None else str(int(x))
    db.execute(f"INSERT INTO dq.run_log (run_id, task, table_name, rows_read, rows_rejected, rows_loaded) "
               f"VALUES ('{run_id}', '{task}', '{table}', {v(read)}, {v(rejected)}, {v(loaded)})")


# ------------------------------------------------------------------ tasks
def task_extract(run_id=None, **_):
    _ensure_dirs()
    frames = _extract(config.RAW_DIR)
    with open(_snapshot(), "wb") as fh:
        pickle.dump(frames, fh)
    counts = {k: len(v) for k, v in frames.items()}
    for k, n in counts.items():
        _log_run(None, _run_id(run_id), "extract", k, read=n)
    return counts


def task_validate_raw(run_id=None, **_):
    with open(_snapshot(), "rb") as fh:
        frames = pickle.load(fh)
    profile = _validate(frames)                 # raises on structural problems
    save_profile(profile, config.DQ_DIR / "raw_profile.json")
    with open(_snapshot(), "wb") as fh:            # optional columns may have been added
        pickle.dump(frames, fh)
    return {k: {"rows": v["rows"], "exact_duplicate_rows": v["exact_duplicate_rows"]} for k, v in profile.items()}


def task_clean(run_id=None, **_):
    _ensure_dirs()
    with open(_snapshot(), "rb") as fh:
        frames = pickle.load(fh)
    dq = DQLog()
    out, price_model = clean_all(frames, dq)
    for name, df in out.items():
        df.to_csv(config.PROCESSED_DIR / f"{name}.csv", index=False, date_format="%Y-%m-%d")
    dq.rejects_frame().to_csv(config.REJECT_DIR / "reject_records.csv", index=False)
    dq.rules_frame().to_csv(config.DQ_DIR / "rule_log.csv", index=False)
    dq.counts_frame().to_csv(config.DQ_DIR / "table_counts.csv", index=False)
    (config.DQ_DIR / "price_model.json").write_text(json.dumps(price_model, indent=2))
    summary = dq.counts_frame().set_index("table").to_dict(orient="index")
    rej = dq.rejects_frame().groupby("source_table").size().to_dict()
    for t, c in summary.items():
        _log_run(None, _run_id(run_id), "clean", t, read=c.get("raw"), rejected=rej.get(t, 0), loaded=c.get("clean"))
    return summary


def _to_int_str(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
        return None
    return str(int(float(v)))


def _read_processed(name):
    df = pd.read_csv(config.PROCESSED_DIR / f"{name}.csv", dtype=str, keep_default_na=False)
    return df.replace("", None)


def task_load_staging(run_id=None, **_):
    run_id = _run_id(run_id)
    db = Database()
    db.execute_file(config.SQL_DIR / "01_schema.sql")
    loaded = {}
    for name, table in STAGING_TABLES.items():
        df = _read_processed(name)
        cols = db.query(
            f"SELECT column_name FROM information_schema.columns WHERE table_schema||'.'||table_name = '{table}' "
            f"ORDER BY ordinal_position").column_name.tolist()
        df = df[[c for c in cols if c in df.columns]]
        for c in df.columns:                       # '12.0' -> '12' for integer columns
            if c in {"city_id", "ship_city_id", "birth_year", "quantity", "discount_pct", "payment_count",
                     "shipping_days", "order_to_delivery_days"}:
                df[c] = df[c].map(_to_int_str)
        db.copy_dataframe(table, df, truncate=True)
        loaded[table] = len(df)
        _log_run(db, run_id, "load_staging", table, read=len(df), rejected=0, loaded=len(df))
    # DQ artefacts: reject table (append per run, idempotent per run_id) + rule log
    rej = pd.read_csv(config.REJECT_DIR / "reject_records.csv", dtype=str, keep_default_na=False).replace("", None)
    rej.insert(0, "run_id", run_id)
    db.execute(f"DELETE FROM dq.reject_records WHERE run_id = '{run_id}'")
    db.copy_dataframe("dq.reject_records", rej[["run_id", "source_table", "source_id", "reason_code",
                                                 "reason_detail", "raw_record"]], truncate=False)
    rules = pd.read_csv(config.DQ_DIR / "rule_log.csv", dtype=str, keep_default_na=False).replace("", None)
    rules.insert(0, "run_id", run_id)
    rules = rules.rename(columns={"table": "table_name", "column": "column_name"})
    for c in ["rows_affected", "rows_before", "rows_after"]:
        rules[c] = rules[c].map(_to_int_str)
    db.execute(f"DELETE FROM dq.rule_log WHERE run_id = '{run_id}'")
    db.copy_dataframe("dq.rule_log", rules, truncate=False)
    _log_run(db, run_id, "load_staging", "dq.reject_records", read=len(rej), rejected=0, loaded=len(rej))
    db.close()
    return loaded


def task_build_dimensions(run_id=None, **_):
    run_id = _run_id(run_id)
    db = Database()
    db.execute_file(config.SQL_DIR / "02_dimensions.sql")
    counts = {}
    for t in ["dim_date", "dim_location", "dim_seller", "dim_product", "dim_customer"]:
        n = int(db.query(f"SELECT COUNT(*) AS n FROM dw.{t}").n.iloc[0])
        counts[t] = n
        _log_run(db, run_id, "build_dimensions", f"dw.{t}", loaded=n)
    db.close()
    return counts


def task_build_fact(run_id=None, **_):
    run_id = _run_id(run_id)
    db = Database()
    db.execute_file(config.SQL_DIR / "03_fact.sql")
    if os.getenv("NEXUSMART_LOAD_MODE", "full") == "full":
        db.execute_file(config.SQL_DIR / "03b_fact_sync_full_load.sql")
    db.execute_file(config.SQL_DIR / "07_seller_score.sql")
    if (config.SQL_DIR / "05_views.sql").exists():
        db.execute_file(config.SQL_DIR / "05_views.sql")
    n = int(db.query("SELECT COUNT(*) AS n FROM dw.fact_sales").n.iloc[0])
    src = int(db.query("SELECT COUNT(*) AS n FROM staging.order_items").n.iloc[0])
    _log_run(db, run_id, "build_fact", "dw.fact_sales", read=src, rejected=src - n, loaded=n)
    db.close()
    return {"fact_sales": n}


def task_quality_checks(run_id=None, **_):
    run_id = _run_id(run_id)
    db = Database()
    res = db.query((config.SQL_DIR / "04_quality_checks.sql").read_text())
    res["passed"] = res.passed.astype(str).str.lower().isin(["true", "t", "1"])
    # reject-rate thresholds (excluding expected de-duplication)
    counts = pd.read_csv(config.DQ_DIR / "table_counts.csv")
    rej = pd.read_csv(config.REJECT_DIR / "reject_records.csv", usecols=["source_table", "reason_code"])
    loss = rej[~rej.reason_code.isin(DEDUP_REASONS)].groupby("source_table").size()
    extra = []
    for _, r in counts.iterrows():
        rate = loss.get(r["table"], 0) / max(r["raw"], 1)
        thr = config.THRESHOLDS["max_reject_rate"]
        extra.append({"check_name": f"reject_rate_{r['table']}", "severity": "CRITICAL", "observed": round(rate, 6),
                      "threshold": thr, "passed": rate <= thr, "detail": "Quarantined (non-duplicate) rows / raw rows"})
    mism = pd.read_csv(config.PROCESSED_DIR / "order_items.csv", usecols=["order_id", "order_total_mismatch"])
    rate = mism.groupby("order_id").order_total_mismatch.max().mean()
    thr = config.THRESHOLDS["max_order_total_mismatch_rate"]
    extra.append({"check_name": "order_total_reconciliation", "severity": "CRITICAL", "observed": round(float(rate), 6),
                  "threshold": thr, "passed": rate <= thr, "detail": "Orders whose cleaned lines differ from order_total by >1%"})
    n_fact = int(db.query("SELECT COUNT(*) AS n FROM dw.fact_sales").n.iloc[0])
    extra.append({"check_name": "fact_min_rows_threshold", "severity": "CRITICAL", "observed": n_fact,
                  "threshold": config.THRESHOLDS["min_fact_rows"], "passed": n_fact >= config.THRESHOLDS["min_fact_rows"],
                  "detail": "Minimum expected fact rows"})
    res = pd.concat([res, pd.DataFrame(extra)], ignore_index=True)
    res.insert(0, "run_id", run_id)
    db.execute(f"DELETE FROM dq.quality_check_results WHERE run_id = '{run_id}'")
    out = res[["run_id", "check_name", "severity", "observed", "threshold", "passed", "detail"]].copy()
    db.copy_dataframe("dq.quality_check_results", out, truncate=False)
    out.to_csv(config.DQ_DIR / "quality_checks.csv", index=False)
    db.close()
    failed = res[(res.severity == "CRITICAL") & ~res.passed]
    for _, r in res.iterrows():
        log.info("check %-40s %-8s observed=%s threshold=%s %s", r.check_name, r.severity, r.observed, r.threshold,
                 "PASS" if r.passed else "FAIL")
    if len(failed):
        raise ValueError("Quality checks failed: " + ", ".join(failed.check_name))
    return {"checks": len(res), "failed": 0}


TASKS = [
    ("extract", task_extract), ("validate_raw", task_validate_raw), ("clean", task_clean),
    ("load_staging", task_load_staging), ("build_dimensions", task_build_dimensions),
    ("build_fact", task_build_fact), ("quality_checks", task_quality_checks),
]
