
WITH touch AS (
  SELECT DISTINCT tenant_id, booking_id
  FROM booking_lines
  WHERE tenant_id = CAST(:tenant_id AS uuid)
    AND date >= :start_date AND date <= :end_date
),
line_agg AS (
  SELECT
    bl.tenant_id,
    bl.booking_id,
    MIN(bl.date) AS check_in_date,
    (MAX(bl.date) + INTERVAL '1 day')::date AS check_out_date,
    COUNT(DISTINCT bl.room_type_id) AS rt_cnt,
    MIN(bl.room_type_id::text)::uuid AS rt_min,
    COUNT(DISTINCT bl.room_id) FILTER (WHERE bl.room_id IS NOT NULL) AS rm_cnt,
    (MAX(bl.room_id::text) FILTER (WHERE bl.room_id IS NOT NULL))::uuid AS rm_val
  FROM booking_lines bl
  INNER JOIN touch t
    ON t.tenant_id = bl.tenant_id AND t.booking_id = bl.booking_id
  GROUP BY bl.tenant_id, bl.booking_id
)
