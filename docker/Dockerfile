FROM apache/airflow:2.10.5-python3.11
USER root
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
USER airflow
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir "pandas>=2.1,<3" "numpy>=1.26" "psycopg2-binary>=2.9" "matplotlib>=3.8" "pytest>=8"
