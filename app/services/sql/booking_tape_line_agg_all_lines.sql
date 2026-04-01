
WITH line_agg AS (
  SELECT
    tenant_id,
    booking_id,
    MIN(date) AS check_in_date,
    (MAX(date) + INTERVAL '1 day')::date AS check_out_date,
    COUNT(DISTINCT room_type_id) AS rt_cnt,
    MIN(room_type_id::text)::uuid AS rt_min,
    COUNT(DISTINCT room_id) FILTER (WHERE room_id IS NOT NULL) AS rm_cnt,
    (MAX(room_id::text) FILTER (WHERE room_id IS NOT NULL))::uuid AS rm_val
  FROM booking_lines
  GROUP BY tenant_id, booking_id
)
