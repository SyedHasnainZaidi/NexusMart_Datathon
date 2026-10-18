#!/bin/bash
# Runs once on first start of the postgres container: creates the warehouse DB/user
# next to the Airflow metadata DB. Credentials come from .env.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE USER ${NEXUSMART_DB_USER} WITH PASSWORD '${NEXUSMART_DB_PASSWORD}';
    CREATE DATABASE ${NEXUSMART_DB_NAME} OWNER ${NEXUSMART_DB_USER};
EOSQL
