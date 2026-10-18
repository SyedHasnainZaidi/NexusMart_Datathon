"""NexusMart end-to-end ETL DAG.

extract -> validate_raw -> clean -> load_staging -> build_dimensions -> build_fact -> quality_checks

Configuration (nothing secret is stored in code):
  * Airflow Connection `nexusmart_dw` (Postgres) -> warehouse host/port/db/user/password
    (docker-compose defines it through AIRFLOW_CONN_NEXUSMART_DW from .env)
  * Airflow Variables (optional, defaults in brackets):
      nexusmart_raw_dir  [/opt/nexusmart/data/raw]
      nexusmart_work_dir [/opt/nexusmart/data]
      nexusmart_load_mode [full]   full = warehouse mirrors the raw snapshot, incremental = upsert only
Idempotent: staging is truncated and reloaded, dimensions and fact are upserted on
natural keys, DQ logs are keyed by run_id - re-running a DAG run never duplicates rows.
"""
import os
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator

PROJECT_HOME = os.getenv("NEXUSMART_HOME", "/opt/nexusmart")
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)


def _configure_env(context):
    """Copy Airflow Connection / Variables into the env vars read by nexusmart_etl.config."""
    from airflow.hooks.base import BaseHook

    conn = BaseHook.get_connection("nexusmart_dw")
    os.environ.update({
        "NEXUSMART_DB_HOST": conn.host or "warehouse",
        "NEXUSMART_DB_PORT": str(conn.port or 5432),
        "NEXUSMART_DB_NAME": conn.schema or "nexusmart",
        "NEXUSMART_DB_USER": conn.login or "",
        "NEXUSMART_DB_PASSWORD": conn.password or "",
        "NEXUSMART_HOME": PROJECT_HOME,
        "NEXUSMART_RAW_DIR": Variable.get("nexusmart_raw_dir", default_var=f"{PROJECT_HOME}/data/raw"),
        "NEXUSMART_WORK_DIR": Variable.get("nexusmart_work_dir", default_var=f"{PROJECT_HOME}/data"),
        "NEXUSMART_LOAD_MODE": Variable.get("nexusmart_load_mode", default_var="full"),
        "NEXUSMART_RUN_ID": context["run_id"].replace(":", "_").replace("+", "_"),
    })


def _runner(task_name):
    def _run(**context):
        _configure_env(context)
        from nexusmart_etl import pipeline  # imported at run time, after env is set

        fn = dict(pipeline.TASKS)[task_name]
        result = fn(run_id=os.environ["NEXUSMART_RUN_ID"])
        print(f"{task_name} result: {result}")
        return result
    return _run


default_args = {
    "owner": "nexusmart-data",
    "retries": 2,                              # transient DB / file-system errors
    "retry_delay": timedelta(minutes=1),
    "retry_exponential_backoff": True,
    "execution_timeout": timedelta(minutes=30),
}

with DAG(
    dag_id="nexusmart_etl",
    description="NexusMart raw CSV -> cleaned staging -> star schema -> quality checks",
    start_date=datetime(2025, 7, 1),
    schedule=None,                             # trigger manually (or set '@daily')
    catchup=False,
    max_active_runs=1,                         # never two loads at once
    default_args=default_args,
    tags=["nexusmart", "datathon"],
) as dag:
    tasks = {}
    for name in ["extract", "validate_raw", "clean", "load_staging", "build_dimensions", "build_fact", "quality_checks"]:
        tasks[name] = PythonOperator(
            task_id=name,
            python_callable=_runner(name),
            # a structural or quality failure will not fix itself: don't retry those
            retries=0 if name in ("validate_raw", "quality_checks") else default_args["retries"],
        )
    (tasks["extract"] >> tasks["validate_raw"] >> tasks["clean"] >> tasks["load_staging"]
     >> tasks["build_dimensions"] >> tasks["build_fact"] >> tasks["quality_checks"])
