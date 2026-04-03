# Передача на ревью: блок «Неоплаченные фолио» на дашборде

## Симптомы (подтверждено скриншотами и Network)

- Виджеты KPI с `GET /dashboard/summary` часто **200 / 304**, данные по отелю отображаются.
- Блок **«Неоплаченные фолио»** падает с ошибкой загрузки.
- В теле успешного summary **нет** поля `unpaid_folio` (или оно не приходит в актуальном JSON после парсинга).
- Цепочка fallback к `unpaid-folio-summary`:
  - **404** на корневый и/или `/dashboard/unpaid-folio-summary`;
  - **422** на `/bookings/unpaid-folio-summary` — характерно для **`GET /bookings/{booking_id}`**, который раньше перехватывал сегмент `unpaid-folio-summary` как UUID.

## Изменения в репозитории (целевое поведение)

- В **`DashboardSummaryRead`** добавлено поле **`unpaid_folio`**; **`get_dashboard_summary`** заполняет его через **`list_unpaid_folio_summary_for_property`**.
- Отдельные эндпоинты: **`GET /unpaid-folio-summary`** (корень), **`GET /bookings/unpaid-folio-summary`** (объявлен **до** `/{booking_id}`), **`GET /dashboard/unpaid-folio-summary`**.
- На ответы summary/unpaid вешается **`Cache-Control: private, no-store`**, чтобы снизить риск устаревшего тела после **304** в браузере.
- **CORS**: в `allow_headers` добавлены **`Cache-Control`**, **`Pragma`**. С клиента на **GET** эти заголовки **не отправляются** — иначе запрос становится «непростым», браузер шлёт **OPTIONS**, при неполном списке разрешённых заголовков preflight давал **400** и визуально **CORS error** на `summary` / unpaid.

## Что пробовали по ходу отладки

1. Встраивание **`unpaid_folio`** в **`GET /dashboard/summary`** + фронт **`useBookingsUnpaidFolio`** (предпочтение полю summary, иначе fallback URL).
2. Логирование (NDJSON / ingest): по логам подтвердилось **`ufKind: undefined`** и исчерпание fallback с **404/422** на стороне **того хоста**, на который реально ходит фронт (`localhost:8000`).
3. Гипотеза **кэш / 304**: серверные **`no-store`**; клиентские cache-headers **откатили** из‑за CORS (см. выше).
4. Усиление текста ошибки в UI для пользователя — **не устраняет** расхождение версий API/фронта.

## Почему у пользователя симптом не исчез только кодом фронта

Инструменты разработчика показывают, что **процесс на порту 8000 отвечает как старая или иная сборка**: нет поля в summary и нет корректных маршрутов unpaid (паттерн 404 + 422). Актуальный код в **git** эти маршруты и поле содержит.

## Рекомендуемые шаги опытному разработчику / DevOps

1. С тем же **JWT** и **`property_id`**, что в UI, вызвать **`GET /dashboard/summary`** на инстансе за **`VITE_API_BASE_URL`** и убедиться, что в JSON есть **`unpaid_folio`** (массив).
2. Проверить **`GET /openapi.json`** — есть ли пути **`/unpaid-folio-summary`**, **`/dashboard/unpaid-folio-summary`**, **`/bookings/unpaid-folio-summary`**.
3. **Пересобрать и перезапустить** сервис API из текущего дерева (например `docker compose build --no-cache api` и `up -d`), убедиться что фронт указывает на **этот** инстанс.

## Ключевые файлы

| Backend | Frontend (отдельный репозиторий) |
|---------|----------------------------------|
| `app/schemas/dashboard.py` | `src/types/api.ts` |
| `app/services/dashboard_service.py` | `src/hooks/useBookingsUnpaidFolio.ts` |
| `app/api/routes/dashboard.py` | `src/api/bookings.ts`, `src/api/dashboard.ts` |
| `app/api/routes/bookings.py` | `src/pages/DashboardPage.tsx` |
| `app/api/routes/unpaid_folio_summary.py` | |
| `app/main.py` (CORS, подключение роутеров) | |
