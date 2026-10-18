"""Parsing helpers and the data-quality log / reject store."""
import json
import re
from datetime import datetime

import numpy as np
import pandas as pd

from .mappings import NUMBER_WORDS


# ------------------------------------------------------------------ parsing
_ISO = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")
_ISO_TIME = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}[ T]\d{1,2}:\d{2}(:\d{2})?")
_SLASH = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
_DASH_DMY = re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$")
_DOT_DMY = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")
_MON_D_Y = re.compile(r"^[A-Za-z]{3,9}\.? \d{1,2},? \d{4}$")
_D_MON_Y = re.compile(r"^\d{1,2}[ -][A-Za-z]{3,9}[ -]\d{4}$")
_YMD_SLASH = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$")


def parse_dates(series: pd.Series) -> pd.Series:
    """Parse a mixed-format date column.

    Supported: YYYY-MM-DD (optionally with time), DD/MM/YYYY (brief: slashes are
    day/month/year), DD-MM-YYYY, DD.MM.YYYY, YYYY/MM/DD, 'Mon D YYYY',
    'D Mon YYYY'. Impossible dates (2024-02-30, month 13, 00/00/2024) -> NaT.
    """
    s = series.fillna("").astype(str).str.strip()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")

    def apply(mask, fmt, transform=None):
        if mask.any():
            vals = s[mask] if transform is None else transform(s[mask])
            out.loc[mask] = pd.to_datetime(vals, format=fmt, errors="coerce")

    apply(s.str.match(_ISO), "%Y-%m-%d")
    apply(s.str.match(_ISO_TIME), "%Y-%m-%d", lambda x: x.str.slice(0, 10).str.strip())
    apply(s.str.match(_SLASH), "%d/%m/%Y")
    apply(s.str.match(_DASH_DMY), "%d-%m-%Y")
    apply(s.str.match(_DOT_DMY), "%d.%m.%Y")
    apply(s.str.match(_YMD_SLASH), "%Y/%m/%d")
    m = s.str.match(_MON_D_Y)
    if m.any():
        cleaned = s[m].str.replace(",", "", regex=False).str.replace(".", "", regex=False)
        a = pd.to_datetime(cleaned, format="%b %d %Y", errors="coerce")
        b = pd.to_datetime(cleaned, format="%B %d %Y", errors="coerce")
        out.loc[m] = a.fillna(b)
    m = s.str.match(_D_MON_Y)
    if m.any():
        cleaned = s[m].str.replace("-", " ", regex=False)
        a = pd.to_datetime(cleaned, format="%d %b %Y", errors="coerce")
        b = pd.to_datetime(cleaned, format="%d %B %Y", errors="coerce")
        out.loc[m] = a.fillna(b)
    return out


def parse_money(series: pd.Series) -> pd.Series:
    """'Rs. 2,290' / 'PKR 1,000' / ' 450 ' / '-120' -> float. Non-numeric -> NaN."""
    s = series.fillna("").astype(str).str.strip()
    s = s.str.replace(r"(?i)^(rs\.?|pkr|rupees?)\s*", "", regex=True)
    s = s.str.replace(r"(?i)\s*(rs\.?|pkr|/-)$", "", regex=True)
    s = s.str.replace(",", "", regex=False).str.replace(" ", "", regex=False)
    return pd.to_numeric(s, errors="coerce")


def parse_quantity(series: pd.Series) -> pd.Series:
    s = series.fillna("").astype(str).str.strip().str.lower()
    s = s.map(lambda v: str(NUMBER_WORDS[v]) if v in NUMBER_WORDS else v)
    return pd.to_numeric(s, errors="coerce")


def clean_text(series: pd.Series) -> pd.Series:
    s = series.fillna("").astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
    return s.replace("", np.nan)


def title_case(series: pd.Series) -> pd.Series:
    """Title-case only values written entirely in upper or lower case."""
    s = clean_text(series)

    def fix(v):
        if not isinstance(v, str):
            return v
        if v.isupper() or v.islower():
            return " ".join(w[:1].upper() + w[1:].lower() for w in v.split(" "))
        return v
    return s.map(fix)


# ------------------------------------------------------------- DQ logging
class DQLog:
    """Collects (a) a rule-by-rule log with before/after counts and (b) rejected
    records with a reason code. Nothing is ever silently dropped: every row that
    leaves a table goes to the reject store with a reason."""

    def __init__(self):
        self.rules = []
        self.rejects = []
        self.counts = {}

    def rule(self, table, column, rule_code, description, affected, action, before=None, after=None):
        self.rules.append({
            "table": table, "column": column, "rule_code": rule_code, "description": description,
            "rows_affected": int(affected), "action": action,
            "rows_before": None if before is None else int(before),
            "rows_after": None if after is None else int(after),
        })

    def reject(self, table, df: pd.DataFrame, id_col, reason_code, detail):
        if df is None or len(df) == 0:
            return
        raw = df.drop(columns=[c for c in df.columns if c.startswith("_")], errors="ignore")
        recs = raw.astype(object).where(raw.notna(), None).to_dict(orient="records")
        for rec in recs:
            self.rejects.append({
                "source_table": table,
                "source_id": None if id_col is None else str(rec.get(id_col)),
                "reason_code": reason_code,
                "reason_detail": detail,
                "raw_record": json.dumps(rec, default=str),
            })

    def set_count(self, table, stage, n):
        self.counts.setdefault(table, {})[stage] = int(n)

    def rules_frame(self):
        return pd.DataFrame(self.rules)

    def rejects_frame(self):
        cols = ["source_table", "source_id", "reason_code", "reason_detail", "raw_record"]
        return pd.DataFrame(self.rejects, columns=cols)

    def counts_frame(self):
        rows = []
        for t, d in self.counts.items():
            rows.append({"table": t, **d})
        return pd.DataFrame(rows)


def now_iso():
    return datetime.now().isoformat(timespec="seconds")
