-- =====================================================================
-- build_dimensions: upsert the five dimensions from staging.
-- Every dimension has an 'Unknown' member with surrogate key -1.
-- Upserts (INSERT ... ON CONFLICT DO UPDATE) keep surrogate keys stable across
-- runs, so re-running never duplicates rows. DimCustomer is SCD Type 1:
-- changed attributes overwrite the previous values.
-- =====================================================================

-- ------------------------------------------------------------ DimDate
INSERT INTO dw.dim_date (date_key, full_date, day_name, event_name, is_weekend, is_public_holiday,
    is_ramadan, is_eid_ul_fitr, is_eid_ul_adha, is_eid, is_independence_day, is_singles_day_1111,
    is_black_friday, is_festival_period, holiday_name)
VALUES (-1, NULL, 'Unknown', 'Unknown', FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, NULL)
ON CONFLICT (date_key) DO NOTHING;

WITH d AS (
    SELECT gs::date AS dt FROM generate_series(DATE '2023-01-01', DATE '2026-12-31', INTERVAL '1 day') gs
), cal AS (
    SELECT dt,
        dt BETWEEN DATE '2024-03-11' AND DATE '2024-04-09' OR dt BETWEEN DATE '2025-03-01' AND DATE '2025-03-30' AS ramadan,
        dt BETWEEN DATE '2024-04-10' AND DATE '2024-04-12' OR dt BETWEEN DATE '2025-03-31' AND DATE '2025-04-02' AS eid_fitr,
        dt BETWEEN DATE '2024-06-17' AND DATE '2024-06-19' OR dt BETWEEN DATE '2025-06-07' AND DATE '2025-06-09' AS eid_adha,
        (EXTRACT(MONTH FROM dt) = 8 AND EXTRACT(DAY FROM dt) = 14) AS indep,
        (EXTRACT(MONTH FROM dt) = 11 AND EXTRACT(DAY FROM dt) = 11) AS d1111,
        -- Black Friday: 29 Nov 2024 per brief; other years = 4th Friday of November
        (dt = DATE '2024-11-29' OR (EXTRACT(YEAR FROM dt) <> 2024 AND EXTRACT(MONTH FROM dt) = 11
            AND EXTRACT(ISODOW FROM dt) = 5 AND EXTRACT(DAY FROM dt) BETWEEN 22 AND 28)) AS bf
    FROM d
), hol AS (   -- static Pakistan public-holiday calendar (permitted by the rules)
    SELECT * FROM (VALUES
        (DATE '2024-02-05', 'Kashmir Solidarity Day'), (DATE '2025-02-05', 'Kashmir Solidarity Day'),
        (DATE '2024-03-23', 'Pakistan Day'),           (DATE '2025-03-23', 'Pakistan Day'),
        (DATE '2024-05-01', 'Labour Day'),             (DATE '2025-05-01', 'Labour Day'),
        (DATE '2024-05-28', 'Youm-e-Takbeer'),         (DATE '2025-05-28', 'Youm-e-Takbeer'),
        (DATE '2024-07-16', 'Ashura'), (DATE '2024-07-17', 'Ashura'),
        (DATE '2024-08-14', 'Independence Day'),       (DATE '2025-08-14', 'Independence Day'),
        (DATE '2024-09-17', 'Eid Milad-un-Nabi'),
        (DATE '2024-11-09', 'Iqbal Day'),
        (DATE '2024-12-25', 'Quaid-e-Azam Day'),
        (DATE '2024-04-10', 'Eid-ul-Fitr'), (DATE '2024-04-11', 'Eid-ul-Fitr'), (DATE '2024-04-12', 'Eid-ul-Fitr'),
        (DATE '2025-03-31', 'Eid-ul-Fitr'), (DATE '2025-04-01', 'Eid-ul-Fitr'), (DATE '2025-04-02', 'Eid-ul-Fitr'),
        (DATE '2024-06-17', 'Eid-ul-Adha'), (DATE '2024-06-18', 'Eid-ul-Adha'), (DATE '2024-06-19', 'Eid-ul-Adha'),
        (DATE '2025-06-07', 'Eid-ul-Adha'), (DATE '2025-06-08', 'Eid-ul-Adha'), (DATE '2025-06-09', 'Eid-ul-Adha')
    ) v(hdate, hname)
)
INSERT INTO dw.dim_date
SELECT
    TO_CHAR(c.dt, 'YYYYMMDD')::int,
    c.dt,
    EXTRACT(DAY FROM c.dt)::smallint,
    TRIM(TO_CHAR(c.dt, 'Day')),
    EXTRACT(ISODOW FROM c.dt)::smallint,
    EXTRACT(WEEK FROM c.dt)::smallint,
    DATE_TRUNC('week', c.dt)::date,
    EXTRACT(MONTH FROM c.dt)::smallint,
    TRIM(TO_CHAR(c.dt, 'Month')),
    TO_CHAR(c.dt, 'YYYY-MM'),
    EXTRACT(QUARTER FROM c.dt)::smallint,
    TO_CHAR(c.dt, 'YYYY') || '-Q' || EXTRACT(QUARTER FROM c.dt),
    EXTRACT(YEAR FROM c.dt)::smallint,
    EXTRACT(ISODOW FROM c.dt) IN (6, 7),
    h.hname IS NOT NULL,
    h.hname,
    c.ramadan, c.eid_fitr, c.eid_adha, (c.eid_fitr OR c.eid_adha), c.indep, c.d1111, c.bf,
    CASE WHEN c.eid_fitr THEN 'Eid-ul-Fitr' WHEN c.eid_adha THEN 'Eid-ul-Adha' WHEN c.ramadan THEN 'Ramadan'
         WHEN c.indep THEN '14 August' WHEN c.d1111 THEN '11.11' WHEN c.bf THEN 'Black Friday' ELSE 'Normal day' END,
    (c.ramadan OR c.eid_fitr OR c.eid_adha OR c.indep OR c.d1111 OR c.bf)
FROM cal c LEFT JOIN hol h ON h.hdate = c.dt
ON CONFLICT (date_key) DO UPDATE SET
    is_public_holiday = EXCLUDED.is_public_holiday, holiday_name = EXCLUDED.holiday_name,
    is_ramadan = EXCLUDED.is_ramadan, is_eid_ul_fitr = EXCLUDED.is_eid_ul_fitr, is_eid_ul_adha = EXCLUDED.is_eid_ul_adha,
    is_eid = EXCLUDED.is_eid, is_independence_day = EXCLUDED.is_independence_day,
    is_singles_day_1111 = EXCLUDED.is_singles_day_1111, is_black_friday = EXCLUDED.is_black_friday,
    event_name = EXCLUDED.event_name, is_festival_period = EXCLUDED.is_festival_period;

-- ------------------------------------------------------------ DimLocation
INSERT INTO dw.dim_location (location_key, city_id, city, province, region, population_tier)
OVERRIDING SYSTEM VALUE VALUES (-1, -1, 'Unknown', 'Unknown', 'Unknown', 'Unknown')
ON CONFLICT (location_key) DO NOTHING;

INSERT INTO dw.dim_location (city_id, city, province, region, population_tier, latitude, longitude, province_imputed)
SELECT city_id, city, province, region, population_tier, latitude, longitude, province_imputed
FROM staging.locations
ON CONFLICT (city_id) DO UPDATE SET
    city = EXCLUDED.city, province = EXCLUDED.province, region = EXCLUDED.region,
    population_tier = EXCLUDED.population_tier, latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude,
    province_imputed = EXCLUDED.province_imputed, updated_at = now();

-- ------------------------------------------------------------ DimSeller
INSERT INTO dw.dim_seller (seller_key, seller_id, seller_name, seller_tier, city, province, region)
OVERRIDING SYSTEM VALUE VALUES (-1, 'UNKNOWN', 'Unknown seller', 'Unknown', 'Unknown', 'Unknown', 'Unknown')
ON CONFLICT (seller_key) DO NOTHING;

INSERT INTO dw.dim_seller (seller_id, seller_name, join_date, seller_tier, rating, city, province, region)
SELECT s.seller_id, s.seller_name, s.join_date, s.seller_tier, s.rating,
       COALESCE(l.city, 'Unknown'), COALESCE(l.province, 'Unknown'), COALESCE(l.region, 'Unknown')
FROM staging.sellers s LEFT JOIN staging.locations l ON l.city_id = s.city_id
ON CONFLICT (seller_id) DO UPDATE SET
    seller_name = EXCLUDED.seller_name, join_date = EXCLUDED.join_date, seller_tier = EXCLUDED.seller_tier,
    rating = EXCLUDED.rating, city = EXCLUDED.city, province = EXCLUDED.province, region = EXCLUDED.region,
    updated_at = now();

-- ------------------------------------------------------------ DimProduct
INSERT INTO dw.dim_product (product_key, product_id, product_name, category, sub_category, brand, price_band)
OVERRIDING SYSTEM VALUE VALUES (-1, 'UNKNOWN', 'Unknown product', 'Unknown', 'Unknown', 'Unknown', 'Unknown')
ON CONFLICT (product_key) DO NOTHING;

INSERT INTO dw.dim_product (product_id, product_name, category, sub_category, brand, list_price, cost_price,
                            weight_kg, price_band, cost_imputed, duplicate_ids_merged)
SELECT p.product_id, p.product_name, p.category, p.sub_category, p.brand, p.list_price, p.cost_price, p.weight_kg,
       CASE WHEN p.list_price < 1000 THEN '< 1k' WHEN p.list_price < 5000 THEN '1k-5k'
            WHEN p.list_price < 20000 THEN '5k-20k' ELSE '20k+' END,
       p.cost_imputed,
       COALESCE(m.n - 1, 0)
FROM staging.products p
LEFT JOIN (SELECT master_product_id, COUNT(*) n FROM dq.product_id_map GROUP BY 1) m
       ON m.master_product_id = p.product_id
ON CONFLICT (product_id) DO UPDATE SET
    product_name = EXCLUDED.product_name, category = EXCLUDED.category, sub_category = EXCLUDED.sub_category,
    brand = EXCLUDED.brand, list_price = EXCLUDED.list_price, cost_price = EXCLUDED.cost_price,
    weight_kg = EXCLUDED.weight_kg, price_band = EXCLUDED.price_band, cost_imputed = EXCLUDED.cost_imputed,
    duplicate_ids_merged = EXCLUDED.duplicate_ids_merged, updated_at = now();

-- ------------------------------------------------------------ DimCustomer (SCD Type 1)
-- RFM is calculated from non-cancelled orders, net of valid refunds, as of the
-- day after the last order date in the data. Scores are quintiles (5 = best).
INSERT INTO dw.dim_customer (customer_key, customer_id, full_name, gender, age_band, city, province, region,
                             customer_segment, rfm_score)
OVERRIDING SYSTEM VALUE VALUES (-1, 'UNKNOWN', 'Unknown customer', 'Unknown', 'Unknown', 'Unknown', 'Unknown',
                                'Unknown', 'Unknown', 'Unknown')
ON CONFLICT (customer_key) DO NOTHING;

WITH line_rev AS (
    SELECT oi.order_id, oi.net_amount - COALESCE(r.refund, 0) AS net_rev
    FROM staging.order_items oi
    LEFT JOIN (SELECT order_item_id, SUM(refund_amount) refund FROM staging.returns WHERE is_valid GROUP BY 1) r
           ON r.order_item_id = oi.order_item_id
), ord AS (
    SELECT o.customer_id, o.order_id, o.order_date, SUM(l.net_rev) AS net_rev
    FROM staging.orders o JOIN line_rev l ON l.order_id = o.order_id
    WHERE o.order_status <> 'Cancelled' AND o.customer_id IS NOT NULL
    GROUP BY 1, 2, 3
), cust AS (
    SELECT customer_id, MIN(order_date) first_order, MAX(order_date) last_order,
           COUNT(*) freq, SUM(net_rev) monetary
    FROM ord GROUP BY 1
), asof AS (SELECT MAX(order_date) + 1 AS d FROM staging.orders),
scored AS (
    SELECT c.*, (SELECT d FROM asof) - c.last_order AS recency_days,
           NTILE(5) OVER (ORDER BY c.last_order ASC) AS r_score,
           -- frequency has heavy ties (most customers order 1-4 times): use fixed bands
           CASE WHEN freq >= 8 THEN 5 WHEN freq >= 6 THEN 4 WHEN freq >= 4 THEN 3 WHEN freq >= 2 THEN 2 ELSE 1 END AS f_score,
           NTILE(5) OVER (ORDER BY c.monetary ASC) AS m_score
    FROM cust c
), seg AS (
    SELECT s.*,
        CASE
            WHEN r_score >= 4 AND f_score >= 4 AND m_score >= 4 THEN 'Champions'
            WHEN r_score >= 3 AND f_score >= 3 THEN 'Loyal Customers'
            WHEN r_score >= 4 AND f_score <= 2 AND m_score >= 3 THEN 'Potential Loyalists'
            WHEN r_score >= 4 AND f_score = 1 THEN 'New Customers'
            WHEN r_score <= 2 AND f_score >= 3 THEN 'At Risk'
            WHEN r_score <= 2 AND f_score <= 2 AND m_score >= 4 THEN 'Cannot Lose Them'
            WHEN r_score = 3 THEN 'Need Attention'
            WHEN r_score <= 2 AND f_score <= 2 THEN 'Hibernating'
            ELSE 'Others'
        END AS segment
    FROM scored s
)
INSERT INTO dw.dim_customer (customer_id, full_name, email, phone, gender, birth_year, age_band,
    registration_date, registration_month, city, province, region, first_order_date, cohort_month,
    last_order_date, order_count, lifetime_net_revenue, rfm_recency_score, rfm_frequency_score,
    rfm_monetary_score, rfm_score, customer_segment, is_repeat_customer, duplicate_ids_merged)
SELECT c.customer_id, c.full_name, c.email, c.phone, c.gender, c.birth_year,
       CASE WHEN c.birth_year IS NULL THEN 'Unknown'
            WHEN 2025 - c.birth_year < 25 THEN '18-24' WHEN 2025 - c.birth_year < 35 THEN '25-34'
            WHEN 2025 - c.birth_year < 45 THEN '35-44' WHEN 2025 - c.birth_year < 55 THEN '45-54' ELSE '55+' END,
       c.registration_date, TO_CHAR(c.registration_date, 'YYYY-MM'),
       COALESCE(l.city, 'Unknown'), COALESCE(l.province, 'Unknown'), COALESCE(l.region, 'Unknown'),
       s.first_order, TO_CHAR(s.first_order, 'YYYY-MM'), s.last_order,
       COALESCE(s.freq, 0), COALESCE(s.monetary, 0),
       s.r_score, s.f_score, s.m_score,
       CASE WHEN s.customer_id IS NULL THEN 'No purchase' ELSE s.r_score::text || s.f_score::text || s.m_score::text END,
       COALESCE(s.segment, 'No Purchase'),
       COALESCE(s.freq, 0) >= 2,
       COALESCE(m.n - 1, 0)
FROM staging.customers c
LEFT JOIN (SELECT master_customer_id, COUNT(*) n FROM dq.customer_id_map GROUP BY 1) m
       ON m.master_customer_id = c.customer_id
LEFT JOIN staging.locations l ON l.city_id = c.city_id
LEFT JOIN seg s ON s.customer_id = c.customer_id
ON CONFLICT (customer_id) DO UPDATE SET            -- SCD Type 1: overwrite
    full_name = EXCLUDED.full_name, email = EXCLUDED.email, phone = EXCLUDED.phone, gender = EXCLUDED.gender,
    birth_year = EXCLUDED.birth_year, age_band = EXCLUDED.age_band,
    registration_date = EXCLUDED.registration_date, registration_month = EXCLUDED.registration_month,
    city = EXCLUDED.city, province = EXCLUDED.province, region = EXCLUDED.region,
    first_order_date = EXCLUDED.first_order_date, cohort_month = EXCLUDED.cohort_month,
    last_order_date = EXCLUDED.last_order_date, order_count = EXCLUDED.order_count,
    lifetime_net_revenue = EXCLUDED.lifetime_net_revenue, rfm_recency_score = EXCLUDED.rfm_recency_score,
    rfm_frequency_score = EXCLUDED.rfm_frequency_score, rfm_monetary_score = EXCLUDED.rfm_monetary_score,
    rfm_score = EXCLUDED.rfm_score, customer_segment = EXCLUDED.customer_segment,
    is_repeat_customer = EXCLUDED.is_repeat_customer, duplicate_ids_merged = EXCLUDED.duplicate_ids_merged,
    updated_at = now();
