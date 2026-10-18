"""Runtime configuration. Everything comes from environment variables (which the
Airflow DAG populates from Airflow Variables / Connections), with defaults that
work for a local run from the repository root."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _paths():
    """Resolved on every access so values set at task run-time (e.g. from Airflow
    Variables / Connections) are always honoured."""
    home = Path(os.getenv("NEXUSMART_HOME", ROOT))
    work = Path(os.getenv("NEXUSMART_WORK_DIR", home / "data"))
    return {
        "RAW_DIR": Path(os.getenv("NEXUSMART_RAW_DIR", home / "data" / "raw")),
        "WORK_DIR": work,
        "PROCESSED_DIR": work / "processed",
        "REJECT_DIR": work / "rejects",
        "DQ_DIR": work / "dq",
        "SQL_DIR": Path(os.getenv("NEXUSMART_SQL_DIR", home / "sql")),
        "DB": {
            "host": os.getenv("NEXUSMART_DB_HOST", "localhost"),
            "port": int(os.getenv("NEXUSMART_DB_PORT", "5432")),
            "dbname": os.getenv("NEXUSMART_DB_NAME", "nexusmart"),
            "user": os.getenv("NEXUSMART_DB_USER", "nexusmart"),
            "password": os.getenv("NEXUSMART_DB_PASSWORD", ""),  # never hard-coded: set in .env / Airflow Connection
        },
    }


def __getattr__(name):  # PEP 562: config.RAW_DIR, config.DB, ... evaluated lazily
    values = _paths()
    if name in values:
        return values[name]
    raise AttributeError(name)


# Business rules from the brief
VALID_ORDER_START = "2024-01-01"
VALID_ORDER_END = "2025-06-30"
SENTINEL_VALUES = {9999999, 99999999, 999999}

# Quality thresholds: the pipeline FAILS when any of these is violated
THRESHOLDS = {
    "max_reject_rate": float(os.getenv("NEXUSMART_MAX_REJECT_RATE", "0.02")),          # per table
    "max_unknown_fk_rate": float(os.getenv("NEXUSMART_MAX_UNKNOWN_FK_RATE", "0.05")),  # per FK in FactSales
    "max_order_total_mismatch_rate": float(os.getenv("NEXUSMART_MAX_TOTAL_MISMATCH", "0.03")),
    "min_fact_rows": int(os.getenv("NEXUSMART_MIN_FACT_ROWS", "1000")),
}

EXPECTED_FILES = {
    "customers": ["customer_id", "full_name", "email", "phone", "gender", "registration_date", "city_id", "birth_year"],
    "products": ["product_id", "product_name", "category", "sub_category", "brand", "list_price", "cost_price", "weight_kg", "seller_id"],
    "sellers": ["seller_id", "seller_name", "join_date", "seller_tier", "city_id", "rating"],
    "locations": ["city_id", "city", "province", "region", "latitude", "longitude", "population_tier"],
    "orders": ["order_id", "customer_id", "order_date", "order_status", "ship_city_id", "order_total", "discount_code", "channel"],
    "order_items": ["order_item_id", "order_id", "product_id", "seller_id", "quantity", "unit_price", "discount_amount"],
    "payments": ["payment_id", "order_id", "payment_method", "payment_status", "amount", "payment_date"],
    "shipping": ["shipment_id", "order_id", "courier", "shipped_date", "delivered_date", "shipping_fee", "delivery_status"],
    "returns": ["return_id", "order_item_id", "return_date", "return_reason", "return_status", "refund_amount"],
}

# Optional columns: if a later drop omits them we fill with blanks instead of failing
OPTIONAL_COLUMNS = {
    "customers": {"email", "phone", "gender", "birth_year", "city_id"},
    "products": {"sub_category", "brand", "weight_kg", "cost_price"},
    "sellers": {"join_date", "rating", "seller_tier"},
    "locations": {"latitude", "longitude", "population_tier"},
    "orders": {"discount_code", "channel", "ship_city_id"},
    "order_items": {"discount_amount"},
    "payments": {"payment_date"},
    "shipping": {"shipped_date", "delivered_date", "shipping_fee"},
    "returns": {"return_reason", "return_date"},
}
