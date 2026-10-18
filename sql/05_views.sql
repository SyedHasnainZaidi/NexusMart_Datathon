-- =====================================================================
-- Reporting views (re-created every run). Power BI imports the star schema
-- tables directly; these views are convenience layers for SQL analysis and
-- for the DQ page of the dashboard. No cleaning happens here.
-- =====================================================================

DROP VIEW IF EXISTS dw.vw_orders, dw.vw_kpi_summary, dw.vw_monthly, dq.vw_latest_rules, dq.vw_latest_rejects, dq.vw_latest_checks;

-- One row per order (order-level metrics for AOV / cancellation rate / payment analysis)
CREATE OR REPLACE VIEW dw.vw_orders AS
SELECT f.order_id,
       MIN(f.date_key)            AS date_key,
       MIN(f.customer_key)        AS customer_key,
       MIN(f.location_key)        AS location_key,
       MIN(f.order_status)        AS order_status,
       MIN(f.payment_method)      AS payment_method,
       MIN(f.channel)             AS channel,
       MIN(f.discount_pct)        AS discount_pct,
       MIN(f.courier)             AS courier,
       BOOL_OR(f.is_cancelled)    AS is_cancelled,
       BOOL_OR(f.is_in_transit)   AS is_in_transit,
       BOOL_OR(f.is_returned)     AS has_valid_return,
       BOOL_OR(f.is_new_customer_order) AS is_new_customer_order,
       COUNT(*)                   AS lines,
       SUM(f.quantity)            AS units,
       SUM(f.gross_amount)        AS gross_amount,
       SUM(f.discount_amount)     AS discount_amount,
       SUM(f.net_amount)          AS net_amount,
       SUM(f.returned_amount)     AS returned_amount,
       SUM(f.cost_amount) FILTER (WHERE f.is_kept_line) AS kept_cost_amount,
       SUM(f.shipping_fee)        AS shipping_fee,
       MAX(f.order_to_delivery_days) AS order_to_delivery_days,
       BOOL_AND(f.is_on_time)     AS is_on_time
FROM dw.fact_sales f
GROUP BY f.order_id;

-- Standard business metrics exactly as defined in the brief (section 9)
CREATE OR REPLACE VIEW dw.vw_kpi_summary AS
SELECT
    SUM(gross_amount)                                                        AS gross_sales,
    SUM(net_amount) FILTER (WHERE NOT is_cancelled)                          AS nmv,
    SUM(net_amount) FILTER (WHERE NOT is_cancelled) - SUM(returned_amount)   AS net_revenue,
    SUM(cost_amount) FILTER (WHERE is_kept_line)                             AS cost_of_goods,
    SUM(net_amount) FILTER (WHERE NOT is_cancelled) - SUM(returned_amount)
        - SUM(cost_amount) FILTER (WHERE is_kept_line)                       AS margin,
    COUNT(DISTINCT order_id)                                                 AS orders_all,
    COUNT(DISTINCT order_id) FILTER (WHERE NOT is_cancelled)                 AS orders_non_cancelled,
    SUM(net_amount) FILTER (WHERE NOT is_cancelled)
        / NULLIF(COUNT(DISTINCT order_id) FILTER (WHERE NOT is_cancelled), 0) AS aov,
    SUM(quantity) FILTER (WHERE NOT is_cancelled)                            AS units,
    COUNT(*) FILTER (WHERE is_returned)::numeric
        / NULLIF(COUNT(*) FILTER (WHERE is_delivered_line), 0)               AS return_rate,
    COUNT(DISTINCT order_id) FILTER (WHERE is_cancelled)::numeric
        / NULLIF(COUNT(DISTINCT order_id), 0)                                AS cancellation_rate,
    COUNT(DISTINCT customer_key) FILTER (WHERE NOT is_cancelled AND customer_key <> -1) AS active_customers,
    SUM(shipping_fee)                                                        AS shipping_cost
FROM dw.fact_sales;

-- Monthly trend with MoM growth
CREATE OR REPLACE VIEW dw.vw_monthly AS
WITH m AS (
    SELECT d.year_month,
           SUM(f.gross_amount) AS gross_sales,
           SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) AS nmv,
           SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) - SUM(f.returned_amount) AS net_revenue,
           SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) - SUM(f.returned_amount)
               - SUM(f.cost_amount) FILTER (WHERE f.is_kept_line) AS margin,
           SUM(f.shipping_fee) AS shipping_cost,
           COUNT(DISTINCT f.order_id) AS orders,
           COUNT(DISTINCT f.order_id) FILTER (WHERE f.is_cancelled)::numeric / COUNT(DISTINCT f.order_id) AS cancellation_rate,
           COUNT(*) FILTER (WHERE f.is_returned)::numeric / NULLIF(COUNT(*) FILTER (WHERE f.is_delivered_line), 0) AS return_rate,
           COUNT(DISTINCT f.customer_key) FILTER (WHERE NOT f.is_cancelled AND f.customer_key <> -1) AS active_customers
    FROM dw.fact_sales f JOIN dw.dim_date d ON d.date_key = f.date_key
    GROUP BY d.year_month
)
SELECT m.*,
       net_revenue / NULLIF(LAG(net_revenue) OVER (ORDER BY year_month), 0) - 1 AS net_revenue_mom,
       AVG(net_revenue) OVER (ORDER BY year_month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS net_revenue_3m_avg,
       margin / NULLIF(net_revenue, 0) AS margin_pct
FROM m;

-- Latest-run data-quality views for the dashboard DQ page
CREATE OR REPLACE VIEW dq.vw_latest_rules AS
SELECT * FROM dq.rule_log WHERE run_id = (SELECT run_id FROM dq.reject_records ORDER BY rejected_at DESC LIMIT 1);
CREATE OR REPLACE VIEW dq.vw_latest_rejects AS
SELECT source_table, reason_code, COUNT(*) AS records
FROM dq.reject_records WHERE run_id = (SELECT run_id FROM dq.reject_records ORDER BY rejected_at DESC LIMIT 1)
GROUP BY 1, 2;

CREATE OR REPLACE VIEW dq.vw_latest_checks AS
SELECT check_name, severity, observed, threshold, passed, detail
FROM dq.quality_check_results
WHERE run_id = (SELECT run_id FROM dq.quality_check_results ORDER BY checked_at DESC LIMIT 1);
