-- =====================================================================
-- Business questions answered in SQL on the star schema.
-- Run with:  psql -h localhost -p 5433 -U nexusmart -d nexusmart -f sql/06_business_questions.sql
-- (the deeper analyses - RFM revenue, seller score, festival windows - are in scripts/analysis.py)
-- =====================================================================

-- 0. Headline KPIs (brief section 9)
SELECT * FROM dw.vw_kpi_summary;

-- 1. Revenue and margin by category
SELECT p.category,
       SUM(f.gross_amount)                                                   AS gross_sales,
       SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled)                   AS nmv,
       SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) - SUM(f.returned_amount) AS net_revenue,
       SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) - SUM(f.returned_amount)
         - SUM(f.cost_amount) FILTER (WHERE f.is_kept_line)                  AS margin,
       ROUND(100 * (SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) - SUM(f.returned_amount)
         - SUM(f.cost_amount) FILTER (WHERE f.is_kept_line))
         / NULLIF(SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) - SUM(f.returned_amount), 0), 1) AS margin_pct,
       ROUND(100 * (1 - (SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) - SUM(f.returned_amount))
         / SUM(f.gross_amount)), 1)                                          AS leakage_pct
FROM dw.fact_sales f JOIN dw.dim_product p ON p.product_key = f.product_key
GROUP BY p.category ORDER BY net_revenue DESC;

-- 2. Provinces and cities; top-5 city share
WITH c AS (
    SELECT l.city, l.province,
           SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) - SUM(f.returned_amount) AS net_revenue
    FROM dw.fact_sales f JOIN dw.dim_location l ON l.location_key = f.location_key
    GROUP BY l.city, l.province)
SELECT city, province, net_revenue,
       ROUND(100 * net_revenue / SUM(net_revenue) OVER (), 2) AS share_pct,
       RANK() OVER (ORDER BY net_revenue DESC) AS rnk
FROM c ORDER BY net_revenue DESC LIMIT 10;

SELECT ROUND(100 * SUM(net_revenue) FILTER (WHERE rnk <= 5) / SUM(net_revenue), 2) AS top5_city_share_pct
FROM (SELECT l.city, SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) - SUM(f.returned_amount) AS net_revenue,
             RANK() OVER (ORDER BY (l.city = 'Unknown'),   -- Unknown never counts as a top city, but stays in the denominator
                          SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) - SUM(f.returned_amount) DESC) rnk
      FROM dw.fact_sales f JOIN dw.dim_location l ON l.location_key = f.location_key
      GROUP BY l.city) x;

SELECT l.province,
       SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) - SUM(f.returned_amount) AS net_revenue,
       ROUND(AVG(f.order_to_delivery_days), 2) AS avg_delivery_days,
       ROUND(100.0 * COUNT(DISTINCT f.order_id) FILTER (WHERE f.is_cancelled) / COUNT(DISTINCT f.order_id), 2) AS cancellation_rate_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE f.is_returned) / NULLIF(COUNT(*) FILTER (WHERE f.is_delivered_line), 0), 2) AS return_rate_pct
FROM dw.fact_sales f JOIN dw.dim_location l ON l.location_key = f.location_key
GROUP BY l.province ORDER BY net_revenue DESC;

-- 3/4. Monthly revenue, MoM growth, return & cancellation rates
SELECT year_month, ROUND(net_revenue) AS net_revenue, ROUND(100 * net_revenue_mom, 1) AS mom_pct,
       ROUND(100 * return_rate, 2) AS return_rate_pct, ROUND(100 * cancellation_rate, 2) AS cancellation_rate_pct,
       ROUND(100 * margin_pct, 1) AS margin_pct
FROM dw.vw_monthly ORDER BY year_month;

-- 7. RFM segments
SELECT c.customer_segment, COUNT(DISTINCT c.customer_key) AS customers,
       SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) - SUM(f.returned_amount) AS net_revenue
FROM dw.fact_sales f JOIN dw.dim_customer c ON c.customer_key = f.customer_key
WHERE c.customer_key <> -1
GROUP BY c.customer_segment ORDER BY net_revenue DESC;

-- 9. On-time delivery vs returns
SELECT is_on_time, COUNT(*) AS delivered_lines,
       ROUND(100.0 * AVG(is_returned::int), 2) AS return_rate_pct
FROM dw.fact_sales WHERE is_delivered_line AND is_on_time IS NOT NULL GROUP BY is_on_time;

-- 11. Festival / event comparison (per day)
SELECT d.event_name, COUNT(DISTINCT d.full_date) AS days,
       ROUND((SUM(f.net_amount) FILTER (WHERE NOT f.is_cancelled) - SUM(f.returned_amount)) / COUNT(DISTINCT d.full_date)) AS net_revenue_per_day,
       ROUND(100.0 * COUNT(DISTINCT f.order_id) FILTER (WHERE f.is_cancelled) / COUNT(DISTINCT f.order_id), 2) AS cancellation_rate_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE f.is_returned) / NULLIF(COUNT(*) FILTER (WHERE f.is_delivered_line), 0), 2) AS return_rate_pct
FROM dw.fact_sales f JOIN dw.dim_date d ON d.date_key = f.date_key
GROUP BY d.event_name ORDER BY net_revenue_per_day DESC;

-- 12. Discount levels
SELECT discount_pct, COUNT(DISTINCT order_id) AS orders,
       ROUND(SUM(net_amount) FILTER (WHERE NOT is_cancelled) / NULLIF(COUNT(DISTINCT order_id) FILTER (WHERE NOT is_cancelled), 0)) AS aov,
       ROUND(100 * (SUM(net_amount) FILTER (WHERE NOT is_cancelled) - SUM(returned_amount) - SUM(cost_amount) FILTER (WHERE is_kept_line))
             / NULLIF(SUM(net_amount) FILTER (WHERE NOT is_cancelled) - SUM(returned_amount), 0), 1) AS margin_pct,
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_returned) / NULLIF(COUNT(*) FILTER (WHERE is_delivered_line), 0), 2) AS return_rate_pct,
       ROUND(100.0 * COUNT(DISTINCT order_id) FILTER (WHERE is_cancelled) / COUNT(DISTINCT order_id), 2) AS cancellation_rate_pct
FROM dw.fact_sales GROUP BY discount_pct ORDER BY discount_pct;

-- 14. Payment methods
SELECT payment_method, COUNT(*) AS orders,
       ROUND(100.0 * AVG(is_cancelled::int), 2) AS cancellation_rate_pct
FROM dw.vw_orders GROUP BY payment_method ORDER BY cancellation_rate_pct DESC;
