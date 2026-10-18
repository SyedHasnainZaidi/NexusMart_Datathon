-- =====================================================================
-- quality_checks: each row = one check. severity CRITICAL fails the DAG.
-- Columns: check_name, severity, observed, threshold, passed, detail
-- =====================================================================
WITH
f AS (SELECT * FROM dw.fact_sales),
c AS (
  SELECT
    (SELECT COUNT(*) FROM f)                                             AS fact_rows,
    (SELECT COUNT(*) FROM staging.order_items)                          AS stg_lines,
    (SELECT COUNT(*) - COUNT(DISTINCT order_item_id) FROM f)            AS dup_lines,
    (SELECT COALESCE(SUM(net_amount),0) FROM f)                         AS fact_net,
    (SELECT COALESCE(SUM(net_amount),0) FROM staging.order_items)       AS stg_net,
    (SELECT AVG((date_key = -1)::int) FROM f)                           AS unk_date,
    (SELECT AVG((customer_key = -1)::int) FROM f)                       AS unk_cust,
    (SELECT AVG((product_key = -1)::int) FROM f)                        AS unk_prod,
    (SELECT AVG((seller_key = -1)::int) FROM f)                         AS unk_sell,
    (SELECT AVG((location_key = -1)::int) FROM f)                       AS unk_loc,
    (SELECT COUNT(*) FROM f WHERE net_amount <> gross_amount - discount_amount) AS bad_net,
    (SELECT COUNT(*) FROM f WHERE discount_amount > gross_amount OR discount_amount < 0
                                OR gross_amount < 0 OR quantity <= 0 OR unit_price <= 0) AS bad_amounts,
    (SELECT COUNT(*) FROM f WHERE returned_amount > net_amount + 0.01)  AS bad_refund,
    (SELECT COUNT(*) FROM f WHERE is_returned AND is_cancelled)         AS returned_cancelled,
    (SELECT COUNT(*) FROM (
        SELECT f.order_id, SUM(f.shipping_fee) alloc, MAX(s.shipping_fee) fee
        FROM f JOIN staging.shipping s ON s.order_id = f.order_id GROUP BY f.order_id
        HAVING ABS(SUM(f.shipping_fee) - MAX(s.shipping_fee)) > 0.05) x) AS bad_alloc,
    (SELECT COUNT(*) FROM f fs JOIN dw.dim_date d ON d.date_key = fs.date_key
        WHERE d.full_date NOT BETWEEN DATE '2024-01-01' AND DATE '2025-06-30') AS out_of_range_dates,
    (SELECT COUNT(*) FROM staging.orders WHERE order_date IS NULL)      AS null_order_dates,
    (SELECT COUNT(*) FROM f WHERE dq_flags IS NOT NULL)                 AS flagged_lines,
    (SELECT COUNT(*) FROM dw.dim_customer)                              AS dim_customer_rows,
    (SELECT COUNT(*) FROM staging.customers) + 1                        AS stg_customers_plus_unknown,
    (SELECT COUNT(*) FROM dw.dim_product)                               AS dim_product_rows,
    (SELECT COUNT(*) FROM staging.products) + 1                         AS stg_products_plus_unknown,
    (SELECT COUNT(*) FROM dw.dim_date WHERE date_key = -1)
      + (SELECT COUNT(*) FROM dw.dim_customer WHERE customer_key = -1)
      + (SELECT COUNT(*) FROM dw.dim_product WHERE product_key = -1)
      + (SELECT COUNT(*) FROM dw.dim_seller WHERE seller_key = -1)
      + (SELECT COUNT(*) FROM dw.dim_location WHERE location_key = -1) AS unknown_members
)
SELECT * FROM (VALUES
  ('fact_rowcount_matches_staging', 'CRITICAL', (SELECT fact_rows FROM c)::numeric, (SELECT stg_lines FROM c)::numeric,
      (SELECT fact_rows = stg_lines FROM c), 'FactSales rows = cleaned order lines'),
  ('fact_min_rows', 'CRITICAL', (SELECT fact_rows FROM c)::numeric, NULL::numeric, (SELECT fact_rows > 0 FROM c), 'Fact is not empty'),
  ('fact_no_duplicate_lines', 'CRITICAL', (SELECT dup_lines FROM c)::numeric, 0::numeric, (SELECT dup_lines = 0 FROM c), 'Grain = one row per order line'),
  ('fact_net_reconciles_to_staging', 'CRITICAL', (SELECT fact_net - stg_net FROM c), 0.01::numeric,
      (SELECT ABS(fact_net - stg_net) < 0.01 FROM c), 'SUM(net_amount) fact vs staging'),
  ('unknown_date_share', 'CRITICAL', (SELECT unk_date FROM c), 0.0::numeric, (SELECT unk_date = 0 FROM c), 'Every line has a valid order date'),
  ('unknown_customer_share', 'CRITICAL', (SELECT unk_cust FROM c), 0.05::numeric, (SELECT unk_cust <= 0.05 FROM c), 'Lines mapped to Unknown customer'),
  ('unknown_product_share', 'CRITICAL', (SELECT unk_prod FROM c), 0.05::numeric, (SELECT unk_prod <= 0.05 FROM c), 'Lines mapped to Unknown product'),
  ('unknown_seller_share', 'CRITICAL', (SELECT unk_sell FROM c), 0.05::numeric, (SELECT unk_sell <= 0.05 FROM c), 'Lines mapped to Unknown seller'),
  ('unknown_location_share', 'CRITICAL', (SELECT unk_loc FROM c), 0.05::numeric, (SELECT unk_loc <= 0.05 FROM c), 'Lines mapped to Unknown location'),
  ('net_equals_gross_minus_discount', 'CRITICAL', (SELECT bad_net FROM c)::numeric, 0::numeric, (SELECT bad_net = 0 FROM c), 'Arithmetic integrity'),
  ('no_negative_or_invalid_amounts', 'CRITICAL', (SELECT bad_amounts FROM c)::numeric, 0::numeric, (SELECT bad_amounts = 0 FROM c), 'qty>0, price>0, 0<=discount<=gross'),
  ('refund_not_above_line_net', 'CRITICAL', (SELECT bad_refund FROM c)::numeric, 0::numeric, (SELECT bad_refund = 0 FROM c), 'ReturnedAmount <= NetAmount'),
  ('no_returns_on_cancelled_lines', 'CRITICAL', (SELECT returned_cancelled FROM c)::numeric, 0::numeric, (SELECT returned_cancelled = 0 FROM c), 'Cancelled lines cannot be returned'),
  ('shipping_allocation_sums_to_order_fee', 'CRITICAL', (SELECT bad_alloc FROM c)::numeric, 0::numeric, (SELECT bad_alloc = 0 FROM c), 'No double counting of courier fees'),
  ('order_dates_within_valid_period', 'CRITICAL', (SELECT out_of_range_dates FROM c)::numeric, 0::numeric, (SELECT out_of_range_dates = 0 FROM c), '2024-01-01..2025-06-30'),
  ('no_null_order_dates', 'CRITICAL', (SELECT null_order_dates FROM c)::numeric, 0::numeric, (SELECT null_order_dates = 0 FROM c), 'Staging orders all dated'),
  ('unknown_members_present', 'CRITICAL', (SELECT unknown_members FROM c)::numeric, 5::numeric, (SELECT unknown_members = 5 FROM c), 'Each dimension has key -1'),
  ('dim_customer_complete', 'CRITICAL', (SELECT dim_customer_rows FROM c)::numeric, (SELECT stg_customers_plus_unknown FROM c)::numeric,
      (SELECT dim_customer_rows >= stg_customers_plus_unknown FROM c), 'All master customers loaded'),
  ('dim_product_complete', 'CRITICAL', (SELECT dim_product_rows FROM c)::numeric, (SELECT stg_products_plus_unknown FROM c)::numeric,
      (SELECT dim_product_rows >= stg_products_plus_unknown FROM c), 'All master products loaded'),
  ('flagged_line_share', 'WARNING', (SELECT flagged_lines::numeric / NULLIF(fact_rows, 0) FROM c), 0.30::numeric,
      (SELECT flagged_lines::numeric / NULLIF(fact_rows, 0) <= 0.30 FROM c), 'Lines carrying at least one repair flag')
) AS t(check_name, severity, observed, threshold, passed, detail);
