
SELECT
  b.id,
  b.tenant_id,
  b.property_id,
  b.guest_id,
  b.external_booking_id AS external_booking_id,
  b.status,
  b.source,
  b.total_amount,
  b.notes AS notes,
  g.id AS g_id,
  g.first_name,
  g.last_name,
  la.check_in_date,
  la.check_out_date,
  CASE WHEN la.rt_cnt = 1 THEN la.rt_min ELSE NULL END AS room_type_id,
  CASE WHEN la.rm_cnt = 1 THEN la.rm_val ELSE NULL END AS room_id
FROM bookings b
JOIN line_agg la ON la.booking_id = b.id AND la.tenant_id = b.tenant_id
JOIN guests g ON g.tenant_id = b.tenant_id AND g.id = b.guest_id
