# Диагностика 500 на `GET /bookings`

Runbook для продакшена и локального Docker. Чаще всего достаточно **п.1 + п.2**.

## Карта вызова в коде

- Роут: `app/api/routes/bookings.py` — `get_bookings`: проверки housekeeper, диапазон дат, затем `list_bookings_enriched`, ответ `BookingTapePage`.
- Сервис: `app/services/booking_service.py` — `list_bookings_enriched`: SQL `COUNT`, затем CTE `_LINE_AGG_CTE_IN_WINDOW` + `_BOOKING_TAPE_SELECT`, затем `_booking_tape_from_mapping` на каждую строку.

## Чеклист

1. **Логи и traceback**  
   `docker compose logs api --tail=200` (или `make logs`). Искать падение на `session.execute` (count или основной запрос), в `_booking_tape_from_mapping`, или при сериализации ответа.

2. **Тело ответа 500**  
   Часто `{"detail":"Internal Server Error"}` без traceback; полный traceback — в логах процесса API.

3. **Query-параметры**  
   Обязательны: `property_id`, `start_date`, `end_date`; опционально `status`, `limit`, `offset`. Отсутствие параметров → **422**, не 500. `start_date > end_date` → **422**.

4. **Роль housekeeper**  
   Окно должно быть только «сегодня» в UTC (`datetime.now(UTC).date()`). Иначе **403**, не 500.

5. **БД и миграции**  
   Чужой `property_id` при RLS обычно даёт пустой список (`items: []`, `total: 0`). 500 чаще от обрыва соединения, таймаута, рассинхрона схемы. Проверить `DATABASE_URL`, `alembic current`.

6. **Сериализация**  
   Ответ: `items`, `total`, `limit`, `offset`. Битые типы или отсутствие полей после SQL → ошибки при сборке `BookingTapeRead` / валидации.

7. **Окружение контейнера**  
   Connection refused, timeout, пул соединений — смотреть логи и health БД.

## Производительность

CTE `touch` в `list_bookings_enriched` ограничен по `tenant_id` и диапазону дат, чтобы не сканировать строки всех тенантов. При таймаутах смотреть план запроса и индекс `ix_booking_lines_tenant_date` (миграция `h7i8j9k0l1m2`).

## Улучшения на будущее (не обязательно)

Структурированные логи с `request_id`, глобальный handler с `exc_info`, детали ошибки только в режиме отладки.
