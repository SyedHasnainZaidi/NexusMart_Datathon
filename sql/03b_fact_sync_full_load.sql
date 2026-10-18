-- Run only when NEXUSMART_LOAD_MODE=full (default): the raw files are a full
-- snapshot, so fact rows whose order line no longer exists in the cleaned
-- snapshot (e.g. now quarantined) are removed to keep the warehouse in sync.
-- With NEXUSMART_LOAD_MODE=incremental this step is skipped and the fact is
-- only upserted.
DELETE FROM dw.fact_sales f
WHERE NOT EXISTS (SELECT 1 FROM staging.order_items s WHERE s.order_item_id = f.order_item_id);
