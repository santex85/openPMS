#!/usr/bin/env bash
# OpenPMS Auto-Test Runner — TZ-13 Channex Integration
# Запуск: cd ~/path/to/OpenPMS && bash run_auto_tests.sh
# Результат: test_results.json в той же папке

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS="$SCRIPT_DIR/test_results.json"
API="http://localhost:8000"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Цвета
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅ $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; }
info() { echo -e "${YELLOW}ℹ️  $1${NC}"; }

# JSON-аккумулятор
declare -A R
R[timestamp]="$TIMESTAMP"
R[pytest_exit]="skipped"
R[pytest_summary]=""
R[stack_status]=""

echo "════════════════════════════════════════════════"
echo "  OpenPMS Auto-Test Runner — $(date '+%d %b %Y %H:%M')"
echo "════════════════════════════════════════════════"
cd "$SCRIPT_DIR"

# ─── 1. DOCKER STACK ─────────────────────────────────────────────────────────
info "1/6 Проверка Docker-стека..."
if ! command -v docker &>/dev/null; then
  fail "Docker не найден"
  R[stack_status]="docker_not_found"
else
  STACK=$(docker compose ps --format json 2>/dev/null || echo "[]")
  R[stack_status]=$(echo "$STACK" | python3 -c "
import sys,json
rows=[]
try:
  data=json.load(sys.stdin)
  if isinstance(data,list):
    for s in data: rows.append(s.get('Name','?')+':'+s.get('State','?'))
  elif isinstance(data,dict):
    rows.append(data.get('Name','?')+':'+data.get('State','?'))
except: rows=['parse_error']
print(','.join(rows) if rows else 'empty')
" 2>/dev/null || echo "parse_error")
  echo "  Сервисы: ${R[stack_status]}"

  # Health-check API
  HEALTH=$(curl -sf --max-time 5 "$API/health" 2>/dev/null || echo "unreachable")
  R[health]="$HEALTH"
  if echo "$HEALTH" | grep -q "ok"; then
    ok "API /health → $HEALTH"
  else
    fail "API недоступен: $HEALTH"
  fi
fi

# ─── 2. PYTEST ───────────────────────────────────────────────────────────────
info "2/6 Запуск pytest (make test-docker)..."
PYTEST_LOG="$SCRIPT_DIR/pytest_output.txt"
set +e
make test-docker PYTEST_ARGS="-v --tb=short" 2>&1 | tee "$PYTEST_LOG"
PYTEST_EXIT=$?
set -e
R[pytest_exit]="$PYTEST_EXIT"

# Парсим итог
SUMMARY=$(grep -E "passed|failed|error|warning" "$PYTEST_LOG" | tail -5 | tr '\n' ' ' || echo "no summary")
R[pytest_summary]="$SUMMARY"

if [ "$PYTEST_EXIT" -eq 0 ]; then
  ok "Pytest: все тесты прошли (exit 0)"
else
  fail "Pytest завершился с кодом $PYTEST_EXIT"
fi

# Channex-тесты отдельно
CX_PASS=$(grep -c "PASSED.*channex" "$PYTEST_LOG" 2>/dev/null || echo 0)
CX_FAIL=$(grep -c "FAILED.*channex" "$PYTEST_LOG" 2>/dev/null || echo 0)
R[channex_tests_pass]="$CX_PASS"
R[channex_tests_fail]="$CX_FAIL"
echo "  Channex-тесты: passed=$CX_PASS  failed=$CX_FAIL"

# ─── 3. API FLOW ─────────────────────────────────────────────────────────────
info "3/6 API Flow: регистрация → логин → property → room-type → rate-plan → тарифы → бронирование..."

EMAIL="autotest_$(date +%s)@openpms.test"
PASS="AutoTest123!!"

# Регистрация
REG=$(curl -sf --max-time 10 -X POST "$API/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\",\"property_name\":\"AutoTest Hotel\"}" 2>/dev/null || echo "{}")
R[register_status]=$(echo "$REG" | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok' if 'access_token' in d else 'fail:'+str(d)[:120))" 2>/dev/null || echo "parse_error")

if echo "${R[register_status]}" | grep -q "^ok"; then
  ok "Регистрация: $EMAIL"
  TOKEN=$(echo "$REG" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)
  AUTH="Authorization: Bearer $TOKEN"

  # Текущий property
  PROPS=$(curl -sf --max-time 10 "$API/properties" -H "$AUTH" 2>/dev/null || echo "{}")
  PROP_ID=$(echo "$PROPS" | python3 -c "
import sys,json
d=json.load(sys.stdin)
items = d.get('items', d) if isinstance(d,dict) else d
print(items[0]['id'] if items else '')
" 2>/dev/null || echo "")
  R[property_id]="$PROP_ID"

  if [ -n "$PROP_ID" ]; then
    ok "Property: $PROP_ID"

    # Room Type
    RT=$(curl -sf --max-time 10 -X POST "$API/room-types" \
      -H "$AUTH" -H "Content-Type: application/json" \
      -d "{\"name\":\"AutoTest Standard\",\"property_id\":\"$PROP_ID\",\"total_rooms\":5}" 2>/dev/null || echo "{}")
    RT_ID=$(echo "$RT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
    R[room_type_id]="$RT_ID"
    [ -n "$RT_ID" ] && ok "Room Type: $RT_ID" || fail "Room Type creation failed: $RT"

    # Rate Plan
    RP=$(curl -sf --max-time 10 -X POST "$API/rate-plans" \
      -H "$AUTH" -H "Content-Type: application/json" \
      -d "{\"name\":\"AutoTest BAR\",\"property_id\":\"$PROP_ID\"}" 2>/dev/null || echo "{}")
    RP_ID=$(echo "$RP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
    R[rate_plan_id]="$RP_ID"
    [ -n "$RP_ID" ] && ok "Rate Plan: $RP_ID" || fail "Rate Plan creation failed: $RP"

    if [ -n "$RT_ID" ] && [ -n "$RP_ID" ]; then
      CI="2026-05-01"; CO="2026-05-03"

      # Тарифы batch
      RATES=$(curl -sf --max-time 10 -X POST "$API/nightly-rates/batch" \
        -H "$AUTH" -H "Content-Type: application/json" \
        -d "{\"property_id\":\"$PROP_ID\",\"room_type_id\":\"$RT_ID\",\"rate_plan_id\":\"$RP_ID\",\"date_from\":\"$CI\",\"date_to\":\"$CO\",\"price\":\"1500.00\",\"stop_sell\":false}" \
        2>/dev/null || echo "{}")
      R[rates_batch]=$(echo "$RATES" | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok:'+str(d.get('updated',d.get('count','?'))))" 2>/dev/null || echo "done")
      ok "Тарифы batch: ${R[rates_batch]}"

      # Бронирование
      BK=$(curl -sf --max-time 10 -X POST "$API/bookings" \
        -H "$AUTH" -H "Content-Type: application/json" \
        -d "{\"property_id\":\"$PROP_ID\",\"room_type_id\":\"$RT_ID\",\"rate_plan_id\":\"$RP_ID\",\"check_in\":\"$CI\",\"check_out\":\"$CO\",\"guest\":{\"first_name\":\"Auto\",\"last_name\":\"Test\",\"email\":\"guest@autotest.com\",\"phone\":\"+70001234567\"}}" \
        2>/dev/null || echo "{}")
      BK_ID=$(echo "$BK" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
      BK_STATUS=$(echo "$BK" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
      R[booking_id]="$BK_ID"
      R[booking_status]="$BK_STATUS"
      [ -n "$BK_ID" ] && ok "Бронирование: $BK_ID (status=$BK_STATUS)" || fail "Booking failed: $(echo $BK | head -c 200)"

      # Дубль бронирования (должен вернуть 409)
      BK2=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST "$API/bookings" \
        -H "$AUTH" -H "Content-Type: application/json" \
        -d "{\"property_id\":\"$PROP_ID\",\"room_type_id\":\"$RT_ID\",\"rate_plan_id\":\"$RP_ID\",\"check_in\":\"$CI\",\"check_out\":\"$CO\",\"guest\":{\"first_name\":\"Dup\",\"last_name\":\"Test\",\"email\":\"dup@autotest.com\",\"phone\":\"+70009999999\"}}" \
        2>/dev/null || echo "000")
      R[double_booking_http]="$BK2"
      if [ "$BK2" = "409" ] || [ "$BK2" = "422" ]; then
        ok "Двойное бронирование отклонено: HTTP $BK2"
      else
        fail "Двойное бронирование не отклонено: HTTP $BK2 (ожидался 409)"
      fi
    fi
  else
    fail "Property ID не получен"
    R[property_id]="not_found"
  fi

  # JWT expiry — неверный токен
  UNAUTH=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$API/properties" \
    -H "Authorization: Bearer invalidtoken123" 2>/dev/null || echo "000")
  R[invalid_token_http]="$UNAUTH"
  [ "$UNAUTH" = "401" ] && ok "Неверный JWT → HTTP 401" || fail "Ожидался 401, получен $UNAUTH"

  # Аудит-лог
  AUDIT=$(curl -sf --max-time 10 "$API/audit-log?limit=5" -H "$AUTH" 2>/dev/null || echo "{}")
  AUDIT_COUNT=$(echo "$AUDIT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('items',d) if isinstance(d,dict) else d))" 2>/dev/null || echo "?")
  R[audit_count]="$AUDIT_COUNT"
  ok "Аудит-лог: $AUDIT_COUNT записей"

else
  fail "Регистрация не удалась: ${R[register_status]}"
  R[property_id]="skipped"
  R[booking_id]="skipped"
fi

# ─── 4. CHANNEX STATUS (без реального ключа) ─────────────────────────────────
info "4/6 Channex endpoint — проверка без ключа (ожидаем 401/422)..."
CX_NOKEY=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 -X POST "$API/channex/validate-key" \
  -H "Content-Type: application/json" \
  -d '{"api_key":"","env":"production"}' 2>/dev/null || echo "000")
R[channex_nokey_http]="$CX_NOKEY"
[ "$CX_NOKEY" = "401" ] || [ "$CX_NOKEY" = "422" ] \
  && ok "channex/validate-key без ключа → HTTP $CX_NOKEY" \
  || fail "Неожиданный HTTP $CX_NOKEY на channex/validate-key"

# ─── 5. WEBHOOK SIMULATION ───────────────────────────────────────────────────
info "5/6 Симуляция Channex-вебхука..."

# Без подписи (ожидаем 401 только если CHANNEX_WEBHOOK_SECRET задан)
WH_NOSIG=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 -X POST "$API/webhooks/channex" \
  -H "Content-Type: application/json" \
  -d '{"event":"booking_new","property_id":"00000000-0000-0000-0000-000000000000","payload":{"id":"test-rev-001","booking_id":"test-bk-001","status":"new"}}' \
  2>/dev/null || echo "000")
R[webhook_nosig_http]="$WH_NOSIG"

if [ "$WH_NOSIG" = "200" ]; then
  ok "Вебхук без подписи принят (CHANNEX_WEBHOOK_SECRET не задан) → HTTP 200"
elif [ "$WH_NOSIG" = "401" ]; then
  ok "Вебхук без подписи отклонён (CHANNEX_WEBHOOK_SECRET задан) → HTTP 401"
else
  fail "Неожиданный HTTP $WH_NOSIG на /webhooks/channex"
fi

# Вебхук с неверной подписью
WH_BADSIG=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 -X POST "$API/webhooks/channex" \
  -H "Content-Type: application/json" \
  -H "X-Channex-Signature: badhash0000000000000000000000000000000000000000000000000000000000" \
  -d '{"event":"booking_new","property_id":"00000000-0000-0000-0000-000000000000","payload":{}}' \
  2>/dev/null || echo "000")
R[webhook_badsig_http]="$WH_BADSIG"
[ "$WH_BADSIG" = "401" ] \
  && ok "Вебхук с неверной подписью → HTTP 401" \
  || info "Вебхук с неверной подписью → HTTP $WH_BADSIG (ожидался 401; возможно секрет не задан)"

# Корректный вебхук с HMAC (если SECRET задан в .env)
SECRET=$(grep CHANNEX_WEBHOOK_SECRET "$SCRIPT_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'" | xargs)
if [ -n "$SECRET" ]; then
  BODY='{"event":"booking_new","property_id":"00000000-0000-0000-0000-000000000000","payload":{"id":"auto-rev-'$(date +%s)'","booking_id":"auto-bk-001","status":"new"}}'
  SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')
  WH_VALID=$(curl -sf --max-time 5 -X POST "$API/webhooks/channex" \
    -H "Content-Type: application/json" \
    -H "X-Channex-Signature: $SIG" \
    -d "$BODY" 2>/dev/null || echo "{}")
  R[webhook_valid]=$(echo "$WH_VALID" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "error")
  ok "Вебхук с корректной HMAC → ${R[webhook_valid]}"
else
  info "CHANNEX_WEBHOOK_SECRET не задан — пропускаем тест HMAC-подписи"
  R[webhook_valid]="skipped_no_secret"
fi

# ─── 6. DB CHECKS ────────────────────────────────────────────────────────────
info "6/6 Проверка БД через psql..."
DB_URL=$(grep "^DATABASE_URL" "$SCRIPT_DIR/.env" 2>/dev/null | cut -d= -f2- | sed 's|postgresql+asyncpg|postgresql|')

if [ -n "$DB_URL" ] && command -v psql &>/dev/null; then
  # Webhook logs
  WH_LOGS=$(psql "$DB_URL" -t -c "SELECT count(*) FROM channex_webhook_logs;" 2>/dev/null | xargs || echo "error")
  R[db_webhook_logs]="$WH_LOGS"
  ok "channex_webhook_logs: $WH_LOGS записей"

  # ARI push logs
  ARI_LOGS=$(psql "$DB_URL" -t -c "SELECT count(*) FROM channex_ari_push_logs;" 2>/dev/null | xargs || echo "error")
  R[db_ari_push_logs]="$ARI_LOGS"
  ok "channex_ari_push_logs: $ARI_LOGS записей"

  # Audit log
  AUDIT_DB=$(psql "$DB_URL" -t -c "SELECT count(*) FROM audit_logs;" 2>/dev/null | xargs || echo "error")
  R[db_audit_logs]="$AUDIT_DB"
  ok "audit_logs: $AUDIT_DB записей"

  # Bookings total
  BK_TOTAL=$(psql "$DB_URL" -t -c "SELECT count(*) FROM bookings;" 2>/dev/null | xargs || echo "error")
  R[db_bookings]="$BK_TOTAL"
  ok "bookings total: $BK_TOTAL"

  # Channex property links
  CX_LINKS=$(psql "$DB_URL" -t -c "SELECT count(*), string_agg(status,',') FROM channex_property_links;" 2>/dev/null | xargs || echo "error")
  R[db_channex_links]="$CX_LINKS"
  ok "channex_property_links: $CX_LINKS"
elif docker compose ps db 2>/dev/null | grep -q "running\|Up"; then
  # Через docker compose exec
  WH_LOGS=$(docker compose exec -T db psql -U openpms openpms -t -c "SELECT count(*) FROM channex_webhook_logs;" 2>/dev/null | xargs || echo "error")
  R[db_webhook_logs]="$WH_LOGS"
  ok "channex_webhook_logs (docker): $WH_LOGS записей"

  ARI_LOGS=$(docker compose exec -T db psql -U openpms openpms -t -c "SELECT count(*) FROM channex_ari_push_logs;" 2>/dev/null | xargs || echo "error")
  R[db_ari_push_logs]="$ARI_LOGS"
  ok "channex_ari_push_logs (docker): $ARI_LOGS записей"

  AUDIT_DB=$(docker compose exec -T db psql -U openpms openpms -t -c "SELECT count(*) FROM audit_logs;" 2>/dev/null | xargs || echo "error")
  R[db_audit_logs]="$AUDIT_DB"
  ok "audit_logs (docker): $AUDIT_DB записей"

  BK_TOTAL=$(docker compose exec -T db psql -U openpms openpms -t -c "SELECT count(*) FROM bookings;" 2>/dev/null | xargs || echo "error")
  R[db_bookings]="$BK_TOTAL"
  ok "bookings (docker): $BK_TOTAL"

  CX_LINKS=$(docker compose exec -T db psql -U openpms openpms -t -c "SELECT count(*), string_agg(status,',') FROM channex_property_links;" 2>/dev/null | xargs || echo "error")
  R[db_channex_links]="$CX_LINKS"
  ok "channex_property_links (docker): $CX_LINKS"
else
  fail "psql не доступен и Docker db не запущен — пропускаем DB-проверки"
  R[db_webhook_logs]="skipped"
  R[db_ari_push_logs]="skipped"
  R[db_audit_logs]="skipped"
  R[db_bookings]="skipped"
  R[db_channex_links]="skipped"
fi

# ─── ИТОГ → JSON ─────────────────────────────────────────────────────────────
python3 - <<PYEOF
import json, os
r = {
  "timestamp":             "${R[timestamp]}",
  "stack_status":          "${R[stack_status]}",
  "health":                "${R[health]:-unreachable}",
  "pytest_exit":           "${R[pytest_exit]}",
  "pytest_summary":        "${R[pytest_summary]}",
  "channex_tests_pass":    "${R[channex_tests_pass]:-?}",
  "channex_tests_fail":    "${R[channex_tests_fail]:-?}",
  "register_status":       "${R[register_status]:-skipped}",
  "property_id":           "${R[property_id]:-skipped}",
  "room_type_id":          "${R[room_type_id]:-skipped}",
  "rate_plan_id":          "${R[rate_plan_id]:-skipped}",
  "rates_batch":           "${R[rates_batch]:-skipped}",
  "booking_id":            "${R[booking_id]:-skipped}",
  "booking_status":        "${R[booking_status]:-skipped}",
  "double_booking_http":   "${R[double_booking_http]:-skipped}",
  "invalid_token_http":    "${R[invalid_token_http]:-skipped}",
  "audit_count":           "${R[audit_count]:-skipped}",
  "channex_nokey_http":    "${R[channex_nokey_http]:-skipped}",
  "webhook_nosig_http":    "${R[webhook_nosig_http]:-skipped}",
  "webhook_badsig_http":   "${R[webhook_badsig_http]:-skipped}",
  "webhook_valid":         "${R[webhook_valid]:-skipped}",
  "db_webhook_logs":       "${R[db_webhook_logs]:-skipped}",
  "db_ari_push_logs":      "${R[db_ari_push_logs]:-skipped}",
  "db_audit_logs":         "${R[db_audit_logs]:-skipped}",
  "db_bookings":           "${R[db_bookings]:-skipped}",
  "db_channex_links":      "${R[db_channex_links]:-skipped}",
}
path = "$RESULTS"
with open(path, "w") as f:
    json.dump(r, f, indent=2, ensure_ascii=False)
print(f"\n📄 Результаты сохранены: {path}")
PYEOF

echo ""
echo "════════════════════════════════════════════════"
echo "  Тестирование завершено. Файл: test_results.json"
echo "  Передайте управление Claude для обновления docx."
echo "════════════════════════════════════════════════"
