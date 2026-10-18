-- =====================================================================
-- build_fact: populate dw.fact_sales (grain: one row per order line).
-- * Returns are aggregated per order line BEFORE joining (valid returns only),
--   so the grain is never violated.
-- * shipping_fee is one courier charge per ORDER: it is allocated to lines by
--   their share of order gross amount, so SUM(line fees) = order fee exactly.
-- * On-time threshold: delivered within 5 days of the order date
--   (median order-to-delivery is 4 days; 5 days covers ~76% of deliveries and
--   matches the 3-5 working-day promise of national couriers).
-- Upsert on order_item_id -> idempotent.
-- =====================================================================
WITH ret AS (
    SELECT r.order_item_id,
           SUM(r.refund_amount)                                     AS refund,
           MAX(r.return_date)                                       AS return_date,
           (ARRAY_AGG(r.return_reason ORDER BY r.return_date DESC NULLS LAST))[1] AS return_reason,
           (ARRAY_AGG(r.return_status ORDER BY r.return_date DESC NULLS LAST))[1] AS return_status
    FROM staging.returns r
    WHERE r.is_valid
    GROUP BY r.order_item_id
), lines AS (
    SELECT oi.*,
           SUM(oi.gross_amount) OVER (PARTITION BY oi.order_id) AS order_gross,
           COUNT(*)            OVER (PARTITION BY oi.order_id) AS order_lines
    FROM staging.order_items oi
), first_order AS (
    SELECT DISTINCT ON (customer_id) customer_id, order_id
    FROM staging.orders
    WHERE order_status <> 'Cancelled' AND customer_id IS NOT NULL
    ORDER BY customer_id, order_date, order_id
)
INSERT INTO dw.fact_sales (
    date_key, customer_key, product_key, seller_key, location_key, order_id, order_item_id,
    quantity, unit_price, gross_amount, discount_amount, net_amount, cost_amount, shipping_fee,
    shipping_days, order_to_delivery_days, is_on_time,
    is_cancelled, is_in_transit, is_delivered_line, is_returned, is_kept_line, returned_amount,
    return_date_key, return_reason, return_status, order_status, payment_method, channel,
    discount_code, discount_pct, courier, delivery_status, is_new_customer_order, dq_flags)
SELECT
    COALESCE(dd.date_key, -1),
    COALESCE(dc.customer_key, -1),
    COALESCE(dp.product_key, -1),
    COALESCE(ds.seller_key, -1),
    COALESCE(dl.location_key, -1),
    l.order_id, l.order_item_id,
    l.quantity, l.unit_price, l.gross_amount, l.discount_amount, l.net_amount,
    -- unknown product: cost estimated at the catalogue median cost/list ratio (0.6) of the selling price
    ROUND(l.quantity * COALESCE(p.cost_price, l.unit_price * 0.60), 2),
    COALESCE(sh.shipping_fee, 0) * CASE WHEN l.order_gross > 0 THEN l.gross_amount / l.order_gross
                                        ELSE 1.0 / l.order_lines END,
    sh.shipping_days, sh.order_to_delivery_days,
    CASE WHEN sh.order_to_delivery_days IS NULL THEN NULL ELSE sh.order_to_delivery_days <= 5 END,
    o.order_status = 'Cancelled',
    o.order_status = 'In Transit',
    o.order_status NOT IN ('Cancelled', 'In Transit'),
    (r.order_item_id IS NOT NULL AND o.order_status <> 'Cancelled'),
    (o.order_status <> 'Cancelled' AND r.order_item_id IS NULL),
    CASE WHEN o.order_status = 'Cancelled' OR r.order_item_id IS NULL THEN 0
         ELSE LEAST(r.refund, l.net_amount) END,
    COALESCE(rd.date_key, -1),
    r.return_reason, r.return_status,
    o.order_status, o.payment_method, o.channel, o.discount_code, o.discount_pct,
    sh.courier, sh.delivery_status,
    (fo.order_id IS NOT NULL),
    NULLIF(CONCAT_WS(',',
        CASE WHEN l.qty_imputed THEN 'QTY_REPAIRED' END,
        CASE WHEN l.price_imputed OR l.price_class NOT IN ('OK', 'OK_UNVALIDATED') THEN 'PRICE_REPAIRED' END,
        CASE WHEN l.discount_repaired THEN 'DISCOUNT_REPAIRED' END,
        CASE WHEN o.order_date_repaired THEN 'ORDER_DATE_REPAIRED' END,
        CASE WHEN o.ship_city_imputed THEN 'SHIP_CITY_FROM_CUSTOMER' END,
        CASE WHEN sh.fee_imputed THEN 'SHIP_FEE_IMPUTED' END,
        CASE WHEN p.product_id IS NULL THEN 'UNKNOWN_PRODUCT_COST_ESTIMATED'
             WHEN p.cost_imputed THEN 'COST_IMPUTED' END,
        CASE WHEN l.order_total_mismatch THEN 'ORDER_TOTAL_MISMATCH' END), '')
FROM lines l
JOIN staging.orders o           ON o.order_id = l.order_id
LEFT JOIN staging.products p    ON p.product_id = l.product_id
LEFT JOIN staging.shipping sh   ON sh.order_id = l.order_id
LEFT JOIN ret r                 ON r.order_item_id = l.order_item_id
LEFT JOIN first_order fo        ON fo.order_id = l.order_id
LEFT JOIN dw.dim_date dd        ON dd.full_date = o.order_date
LEFT JOIN dw.dim_date rd        ON rd.full_date = r.return_date
LEFT JOIN dw.dim_customer dc    ON dc.customer_id = o.customer_id
LEFT JOIN dw.dim_product dp     ON dp.product_id = l.product_id
LEFT JOIN dw.dim_seller ds      ON ds.seller_id = l.seller_id
LEFT JOIN dw.dim_location dl    ON dl.city_id = o.ship_city_id
ON CONFLICT (order_item_id) DO UPDATE SET
    date_key = EXCLUDED.date_key, customer_key = EXCLUDED.customer_key, product_key = EXCLUDED.product_key,
    seller_key = EXCLUDED.seller_key, location_key = EXCLUDED.location_key, order_id = EXCLUDED.order_id,
    quantity = EXCLUDED.quantity, unit_price = EXCLUDED.unit_price, gross_amount = EXCLUDED.gross_amount,
    discount_amount = EXCLUDED.discount_amount, net_amount = EXCLUDED.net_amount,
    cost_amount = EXCLUDED.cost_amount, shipping_fee = EXCLUDED.shipping_fee,
    shipping_days = EXCLUDED.shipping_days, order_to_delivery_days = EXCLUDED.order_to_delivery_days,
    is_on_time = EXCLUDED.is_on_time, is_cancelled = EXCLUDED.is_cancelled, is_in_transit = EXCLUDED.is_in_transit,
    is_delivered_line = EXCLUDED.is_delivered_line, is_returned = EXCLUDED.is_returned,
    is_kept_line = EXCLUDED.is_kept_line, returned_amount = EXCLUDED.returned_amount,
    return_date_key = EXCLUDED.return_date_key, return_reason = EXCLUDED.return_reason,
    return_status = EXCLUDED.return_status, order_status = EXCLUDED.order_status,
    payment_method = EXCLUDED.payment_method, channel = EXCLUDED.channel,
    discount_code = EXCLUDED.discount_code, discount_pct = EXCLUDED.discount_pct,
    courier = EXCLUDED.courier, delivery_status = EXCLUDED.delivery_status,
    is_new_customer_order = EXCLUDED.is_new_customer_order, dq_flags = EXCLUDED.dq_flags,
    loaded_at = now();
