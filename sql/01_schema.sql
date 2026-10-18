-- =====================================================================
-- NexusMart warehouse - schema DDL (PostgreSQL 13+)
-- Idempotent: safe to run on every pipeline run.
--   staging : cleaned records from the Python step (truncated each run)
--   dq      : data-quality logs, reject/quarantine table, ID mapping tables
--   dw      : star schema (1 fact + 5 dimensions; dimensions never reference each other)
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS dq;
CREATE SCHEMA IF NOT EXISTS dw;

-- ------------------------------------------------------------ staging
CREATE TABLE IF NOT EXISTS staging.locations (
    city_id INT PRIMARY KEY, city TEXT, province TEXT, region TEXT,
    latitude NUMERIC(9,4), longitude NUMERIC(9,4), population_tier TEXT, province_imputed BOOLEAN);

CREATE TABLE IF NOT EXISTS staging.sellers (
    seller_id TEXT PRIMARY KEY, seller_name TEXT, join_date DATE, seller_tier TEXT, city_id INT, rating NUMERIC(3,1));

CREATE TABLE IF NOT EXISTS staging.products (
    product_id TEXT PRIMARY KEY, product_name TEXT, category TEXT, sub_category TEXT, brand TEXT,
    list_price NUMERIC(14,2), cost_price NUMERIC(14,2), weight_kg NUMERIC(8,2), seller_id TEXT,
    list_price_repaired BOOLEAN, cost_imputed BOOLEAN, weight_imputed BOOLEAN);

CREATE TABLE IF NOT EXISTS staging.customers (
    customer_id TEXT PRIMARY KEY, full_name TEXT, email TEXT, phone TEXT, gender TEXT,
    registration_date DATE, city_id INT, birth_year INT);

CREATE TABLE IF NOT EXISTS staging.orders (
    order_id TEXT PRIMARY KEY, customer_id TEXT, customer_id_raw TEXT, order_date DATE, order_date_repaired BOOLEAN,
    order_status TEXT, ship_city_id INT, ship_city_imputed BOOLEAN, order_total NUMERIC(14,2), order_total_flag TEXT,
    discount_code TEXT, discount_pct INT, channel TEXT, payment_method TEXT, payment_count INT);

CREATE TABLE IF NOT EXISTS staging.order_items (
    order_item_id TEXT PRIMARY KEY, order_id TEXT, product_id TEXT, product_id_raw TEXT, seller_id TEXT,
    quantity INT, unit_price NUMERIC(14,2), discount_amount NUMERIC(14,2), gross_amount NUMERIC(14,2),
    net_amount NUMERIC(14,2), qty_imputed BOOLEAN, price_imputed BOOLEAN, discount_repaired BOOLEAN,
    price_class TEXT, order_total_mismatch BOOLEAN);

CREATE TABLE IF NOT EXISTS staging.payments (
    payment_id TEXT PRIMARY KEY, order_id TEXT, payment_method TEXT, payment_status TEXT,
    amount NUMERIC(14,2), payment_date DATE);

CREATE TABLE IF NOT EXISTS staging.shipping (
    shipment_id TEXT PRIMARY KEY, order_id TEXT UNIQUE, courier TEXT, shipped_date DATE, delivered_date DATE,
    shipping_fee NUMERIC(14,2), delivery_status TEXT, shipping_days INT, order_to_delivery_days INT,
    delivery_date_valid BOOLEAN, fee_imputed BOOLEAN);

CREATE TABLE IF NOT EXISTS staging.returns (
    return_id TEXT PRIMARY KEY, order_item_id TEXT, return_date DATE, return_reason TEXT, return_status TEXT,
    refund_amount NUMERIC(14,2), is_valid BOOLEAN, refund_capped BOOLEAN);

-- ------------------------------------------------------------ dq
CREATE TABLE IF NOT EXISTS dq.customer_id_map (
    customer_id TEXT PRIMARY KEY, master_customer_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dq.product_id_map (
    product_id TEXT PRIMARY KEY, master_product_id TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS dq.reject_records (
    reject_id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL, source_table TEXT NOT NULL, source_id TEXT,
    reason_code TEXT NOT NULL, reason_detail TEXT, raw_record JSONB, rejected_at TIMESTAMP DEFAULT now());

CREATE TABLE IF NOT EXISTS dq.rule_log (
    run_id TEXT NOT NULL, table_name TEXT, column_name TEXT, rule_code TEXT, description TEXT,
    rows_affected INT, action TEXT, rows_before INT, rows_after INT);

CREATE TABLE IF NOT EXISTS dq.run_log (
    run_id TEXT NOT NULL, task TEXT NOT NULL, table_name TEXT, rows_read BIGINT, rows_rejected BIGINT,
    rows_loaded BIGINT, logged_at TIMESTAMP DEFAULT now());

CREATE TABLE IF NOT EXISTS dq.quality_check_results (
    run_id TEXT NOT NULL, check_name TEXT, severity TEXT, observed NUMERIC, threshold NUMERIC,
    passed BOOLEAN, detail TEXT, checked_at TIMESTAMP DEFAULT now());

-- ------------------------------------------------------------ dw: dimensions
CREATE TABLE IF NOT EXISTS dw.dim_date (
    date_key INT PRIMARY KEY,               -- yyyymmdd, -1 = Unknown
    full_date DATE UNIQUE,
    day_of_month SMALLINT, day_name TEXT, day_of_week SMALLINT,   -- 1 = Monday
    week_of_year SMALLINT, week_start_date DATE,
    month_number SMALLINT, month_name TEXT, year_month TEXT,
    quarter SMALLINT, year_quarter TEXT, year SMALLINT,
    is_weekend BOOLEAN, is_public_holiday BOOLEAN, holiday_name TEXT,
    is_ramadan BOOLEAN, is_eid_ul_fitr BOOLEAN, is_eid_ul_adha BOOLEAN, is_eid BOOLEAN,
    is_independence_day BOOLEAN, is_singles_day_1111 BOOLEAN, is_black_friday BOOLEAN,
    event_name TEXT,                         -- 'Ramadan', 'Eid-ul-Fitr', ..., 'Normal day'
    is_festival_period BOOLEAN);

CREATE TABLE IF NOT EXISTS dw.dim_location (
    location_key INT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    city_id INT UNIQUE,                      -- natural key; NULL only for Unknown (-1)
    city TEXT, province TEXT, region TEXT, population_tier TEXT,
    latitude NUMERIC(9,4), longitude NUMERIC(9,4), province_imputed BOOLEAN,
    updated_at TIMESTAMP DEFAULT now());

CREATE TABLE IF NOT EXISTS dw.dim_customer (
    customer_key INT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    customer_id TEXT UNIQUE,                 -- master (deduplicated) customer id
    full_name TEXT, email TEXT, phone TEXT, gender TEXT, birth_year INT, age_band TEXT,
    registration_date DATE, registration_month TEXT,
    city TEXT, province TEXT, region TEXT,   -- denormalised city details (SCD1, no FK to dim_location)
    first_order_date DATE, cohort_month TEXT, last_order_date DATE,
    order_count INT, lifetime_net_revenue NUMERIC(16,2),
    rfm_recency_score SMALLINT, rfm_frequency_score SMALLINT, rfm_monetary_score SMALLINT,
    rfm_score TEXT, customer_segment TEXT, is_repeat_customer BOOLEAN,
    duplicate_ids_merged INT,
    updated_at TIMESTAMP DEFAULT now());

CREATE TABLE IF NOT EXISTS dw.dim_product (
    product_key INT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    product_id TEXT UNIQUE, product_name TEXT, category TEXT, sub_category TEXT, brand TEXT,
    list_price NUMERIC(14,2), cost_price NUMERIC(14,2), weight_kg NUMERIC(8,2), price_band TEXT,
    cost_imputed BOOLEAN, duplicate_ids_merged INT,
    updated_at TIMESTAMP DEFAULT now());

CREATE TABLE IF NOT EXISTS dw.dim_seller (
    seller_key INT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    seller_id TEXT UNIQUE, seller_name TEXT, join_date DATE, seller_tier TEXT, rating NUMERIC(3,1),
    city TEXT, province TEXT, region TEXT,   -- denormalised city details
    updated_at TIMESTAMP DEFAULT now());

-- ------------------------------------------------------------ dw: fact (grain = one order line)
CREATE TABLE IF NOT EXISTS dw.fact_sales (
    sales_key BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    date_key INT NOT NULL REFERENCES dw.dim_date(date_key),
    customer_key INT NOT NULL REFERENCES dw.dim_customer(customer_key),
    product_key INT NOT NULL REFERENCES dw.dim_product(product_key),
    seller_key INT NOT NULL REFERENCES dw.dim_seller(seller_key),
    location_key INT NOT NULL REFERENCES dw.dim_location(location_key),
    order_id TEXT NOT NULL,
    order_item_id TEXT NOT NULL UNIQUE,
    quantity INT NOT NULL,
    unit_price NUMERIC(14,2) NOT NULL,
    gross_amount NUMERIC(16,2) NOT NULL,       -- quantity x unit_price
    discount_amount NUMERIC(16,2) NOT NULL,
    net_amount NUMERIC(16,2) NOT NULL,         -- gross - discount
    cost_amount NUMERIC(16,2) NOT NULL,        -- cost_price x quantity
    shipping_fee NUMERIC(16,4) NOT NULL,       -- order fee allocated by line gross share
    shipping_days INT,                         -- delivered_date - shipped_date
    order_to_delivery_days INT,                -- delivered_date - order_date
    is_on_time BOOLEAN,                        -- order_to_delivery_days <= on-time threshold
    is_cancelled BOOLEAN NOT NULL,
    is_in_transit BOOLEAN NOT NULL,
    is_delivered_line BOOLEAN NOT NULL,        -- not cancelled and order not In Transit
    is_returned BOOLEAN NOT NULL,              -- has a valid (non-Rejected) return
    is_kept_line BOOLEAN NOT NULL,             -- not cancelled and no valid return
    returned_amount NUMERIC(16,2) NOT NULL,    -- valid refunds, capped at line net
    return_date_key INT NOT NULL REFERENCES dw.dim_date(date_key),
    return_reason TEXT, return_status TEXT,
    order_status TEXT NOT NULL, payment_method TEXT, channel TEXT,
    discount_code TEXT, discount_pct INT, courier TEXT, delivery_status TEXT,
    is_new_customer_order BOOLEAN,             -- the customer's first non-cancelled order
    dq_flags TEXT,                             -- comma list of repairs applied to the line
    loaded_at TIMESTAMP DEFAULT now());

CREATE INDEX IF NOT EXISTS ix_fact_date ON dw.fact_sales(date_key);
CREATE INDEX IF NOT EXISTS ix_fact_customer ON dw.fact_sales(customer_key);
CREATE INDEX IF NOT EXISTS ix_fact_product ON dw.fact_sales(product_key);
CREATE INDEX IF NOT EXISTS ix_fact_seller ON dw.fact_sales(seller_key);
CREATE INDEX IF NOT EXISTS ix_fact_location ON dw.fact_sales(location_key);
CREATE INDEX IF NOT EXISTS ix_fact_order ON dw.fact_sales(order_id);
