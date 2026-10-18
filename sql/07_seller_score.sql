-- =====================================================================
-- Seller quality score (stored on dw.dim_seller so the dashboard can rank
-- sellers instantly). Weights: 30% net revenue (percentile of log revenue),
-- 25% low return rate, 25% on-time delivery rate, 20% low cancellation rate.
-- Only sellers with >= 100 delivered lines are scored (smaller samples are noisy).
-- Each component is a percentile (cume_dist) across scored sellers, 0..1.
-- =====================================================================
ALTER TABLE dw.dim_seller ADD COLUMN IF NOT EXISTS quality_score NUMERIC(6,2);
ALTER TABLE dw.dim_seller ADD COLUMN IF NOT EXISTS quality_rank INT;
ALTER TABLE dw.dim_seller ADD COLUMN IF NOT EXISTS delivered_lines INT;

WITH s AS (
    SELECT seller_key,
           SUM(net_amount) FILTER (WHERE NOT is_cancelled) - SUM(returned_amount)                       AS net_revenue,
           COUNT(*) FILTER (WHERE is_delivered_line)                                                    AS delivered,
           COUNT(*) FILTER (WHERE is_returned)::numeric / NULLIF(COUNT(*) FILTER (WHERE is_delivered_line), 0) AS return_rate,
           AVG(is_on_time::int) FILTER (WHERE is_delivered_line AND is_on_time IS NOT NULL)            AS on_time_rate,
           COUNT(DISTINCT order_id) FILTER (WHERE is_cancelled)::numeric / COUNT(DISTINCT order_id)     AS cancel_rate
    FROM dw.fact_sales WHERE seller_key <> -1 GROUP BY seller_key
), e AS (
    SELECT s.*,
           CUME_DIST() OVER (ORDER BY LN(GREATEST(net_revenue, 1)))  AS p_rev,
           CUME_DIST() OVER (ORDER BY return_rate DESC)              AS p_ret,
           CUME_DIST() OVER (ORDER BY on_time_rate)                  AS p_ot,
           CUME_DIST() OVER (ORDER BY cancel_rate DESC)              AS p_can
    FROM s WHERE delivered >= 100
), sc AS (
    SELECT seller_key, delivered,
           ROUND((100 * (0.30 * p_rev + 0.25 * p_ret + 0.25 * p_ot + 0.20 * p_can))::numeric, 2) AS score
    FROM e
)
UPDATE dw.dim_seller d
SET quality_score = sc.score,
    quality_rank = r.rnk,
    delivered_lines = sc.delivered
FROM sc JOIN (SELECT seller_key, RANK() OVER (ORDER BY score DESC) rnk FROM sc) r USING (seller_key)
WHERE d.seller_key = sc.seller_key;
