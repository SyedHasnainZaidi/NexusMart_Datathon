"""extract + validate_raw tasks."""
import json
import logging
import re
from pathlib import Path

import pandas as pd

from . import config

log = logging.getLogger(__name__)

# tolerated header aliases for later data drops (normalised header -> expected name)
HEADER_ALIASES = {
    "customerid": "customer_id", "productid": "product_id", "sellerid": "seller_id", "cityid": "city_id",
    "orderid": "order_id", "orderitemid": "order_item_id", "paymentid": "payment_id",
    "shipmentid": "shipment_id", "returnid": "return_id", "name": "full_name",
    "shipcityid": "ship_city_id", "ordertotal": "order_total", "discountcode": "discount_code",
    "unitprice": "unit_price", "discountamount": "discount_amount", "qty": "quantity",
    "paymentmethod": "payment_method", "paymentstatus": "payment_status", "paymentdate": "payment_date",
    "shippeddate": "shipped_date", "delivereddate": "delivered_date", "shippingfee": "shipping_fee",
    "deliverystatus": "delivery_status", "returndate": "return_date", "returnreason": "return_reason",
    "returnstatus": "return_status", "refundamount": "refund_amount", "listprice": "list_price",
    "costprice": "cost_price", "weight": "weight_kg", "weightkg": "weight_kg", "subcategory": "sub_category",
    "productname": "product_name", "sellername": "seller_name", "jointdate": "join_date", "joindate": "join_date",
    "sellertier": "seller_tier", "populationtier": "population_tier", "registrationdate": "registration_date",
    "birthyear": "birth_year", "orderdate": "order_date", "orderstatus": "order_status",
}


def _normalise_header(col: str) -> str:
    c = col.strip().lower().lstrip("﻿")
    c = re.sub(r"[^a-z0-9]+", "_", c).strip("_")
    return HEADER_ALIASES.get(c.replace("_", ""), c)


def find_file(raw_dir: Path, name: str):
    """Exact name first, then case-insensitive / prefixed variants (e.g. orders_v2.csv)."""
    exact = raw_dir / f"{name}.csv"
    if exact.exists():
        return exact
    for p in sorted(raw_dir.glob("*.csv")):
        stem = p.stem.lower()
        if stem == name or re.fullmatch(rf"{name}[_\-]?(v?\d+|new|drop\d*|hour\d*)?", stem):
            return p
    return None


def extract(raw_dir: Path = None) -> dict:
    """Read every raw CSV as text (no type coercion, no NA guessing) so that the
    cleaning step sees the data exactly as exported."""
    raw_dir = Path(raw_dir or config.RAW_DIR)
    frames = {}
    for name in config.EXPECTED_FILES:
        path = find_file(raw_dir, name)
        if path is None:
            raise FileNotFoundError(f"Required raw file missing: {name}.csv in {raw_dir}")
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        df.columns = [_normalise_header(c) for c in df.columns]
        frames[name] = df
        log.info("extract: %s -> %d rows, %d cols (%s)", name, len(df), df.shape[1], path.name)
    return frames


def validate_raw(frames: dict) -> dict:
    """Structural + initial quality checks. Raises on structural problems that make
    cleaning impossible (missing required columns, empty file). Returns a profile
    that is saved as JSON for the data-quality report."""
    profile = {}
    errors = []
    for name, expected in config.EXPECTED_FILES.items():
        df = frames.get(name)
        if df is None:
            errors.append(f"{name}: file not loaded")
            continue
        missing = [c for c in expected if c not in df.columns]
        optional_missing = [c for c in missing if c in config.OPTIONAL_COLUMNS.get(name, set())]
        hard_missing = [c for c in missing if c not in optional_missing]
        for c in optional_missing:
            df[c] = ""                          # tolerated: filled blank, logged
        if hard_missing:
            errors.append(f"{name}: missing required columns {hard_missing}")
        if len(df) == 0:
            errors.append(f"{name}: no rows")
        extra = [c for c in df.columns if c not in expected]
        blanks = {c: int((df[c].astype(str).str.strip() == "").sum()) for c in df.columns}
        profile[name] = {
            "rows": int(len(df)),
            "columns": list(df.columns),
            "missing_optional_columns_filled": optional_missing,
            "extra_columns_ignored": extra,
            "exact_duplicate_rows": int(df.duplicated().sum()),
            "duplicate_primary_keys": int(df[expected[0]].duplicated().sum()) if expected[0] in df else None,
            "blank_values": blanks,
            "distinct_values": {c: int(df[c].nunique()) for c in df.columns},
        }
    if errors:
        raise ValueError("validate_raw failed: " + "; ".join(errors))
    return profile


def save_profile(profile: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2))
