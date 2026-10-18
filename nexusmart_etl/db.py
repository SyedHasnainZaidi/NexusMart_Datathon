"""Thin PostgreSQL adapter.

Uses psycopg2 when installed (Docker/Airflow image). Falls back to the `psql`
command-line client so the pipeline can also run on a machine that only has the
PostgreSQL client tools. Credentials come from config (environment variables /
Airflow Connection) - never hard-coded.
"""
import io
import logging
import os
import subprocess
from pathlib import Path

import pandas as pd

from . import config

log = logging.getLogger(__name__)

try:
    import psycopg2  # type: ignore
except ImportError:  # pragma: no cover
    psycopg2 = None


class Database:
    def __init__(self, params: dict = None):
        self.p = dict(params or config.DB)
        self.backend = "psycopg2" if psycopg2 else "psql"
        self._conn = None

    # --------------------------------------------------------- psycopg2
    def _connect(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(**self.p)
        return self._conn

    # --------------------------------------------------------- psql
    def _psql(self, args, stdin=None):
        env = dict(os.environ, PGPASSWORD=self.p["password"])
        cmd = ["psql", "-h", self.p["host"], "-p", str(self.p["port"]), "-U", self.p["user"],
               "-d", self.p["dbname"], "-v", "ON_ERROR_STOP=1", "-X", "-q"] + args
        res = subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env)
        if res.returncode != 0:
            raise RuntimeError(f"psql failed: {res.stderr.strip()[:2000]}")
        return res.stdout

    # --------------------------------------------------------- API
    def execute(self, sql: str):
        if self.backend == "psycopg2":
            conn = self._connect()
            with conn, conn.cursor() as cur:
                cur.execute(sql)
        else:
            self._psql(["-1"], stdin=sql)   # single transaction

    def execute_file(self, path: Path):
        self.execute(Path(path).read_text())

    def query(self, sql: str) -> pd.DataFrame:
        if self.backend == "psycopg2":
            conn = self._connect()
            with conn, conn.cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                return pd.DataFrame(cur.fetchall(), columns=cols)
        out = self._psql(["--csv", "-c", sql.strip().rstrip(";")])
        return pd.read_csv(io.StringIO(out)) if out.strip() else pd.DataFrame()

    def copy_dataframe(self, table: str, df: pd.DataFrame, truncate: bool = True):
        """Bulk-load a DataFrame with COPY (fast, transactional)."""
        buf = io.StringIO()
        df.to_csv(buf, index=False, header=False, na_rep="\\N", date_format="%Y-%m-%d")
        data = buf.getvalue()
        cols = ",".join(df.columns)
        copy_sql = f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '\\N')"
        if self.backend == "psycopg2":
            conn = self._connect()
            with conn, conn.cursor() as cur:
                if truncate:
                    cur.execute(f"TRUNCATE {table}")
                cur.copy_expert(copy_sql, io.StringIO(data))
        else:
            pre = f"TRUNCATE {table};\n" if truncate else ""
            # psql: \copy reads from stdin after the command
            script = pre + f"\\copy {table} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '\\N')\n" + data + "\\.\n"
            self._psql(["-1"], stdin=script)
        log.info("loaded %d rows into %s", len(df), table)

    def close(self):
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
