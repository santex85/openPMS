# OpenPMS — common workflows (Docker, tests, migrations).
# Requires: Docker Compose v2, make. Optional: .venv for local `make test`.

.PHONY: help install up down build logs ps restart \
	test test-docker test-docker-cov alembic-upgrade alembic-revision load-test-overbooking shell-api shell-db

COMPOSE := docker compose
PYTEST_ARGS ?=
API_SERVICE := api
DB_SERVICE := db

# Local pytest (needs DATABASE_URL / TEST_DATABASE_URL and JWT_SECRET in env)
PYTHON ?= python3
PIP ?= pip

help:
	@echo "OpenPMS targets:"
	@echo "  make install              — pip install -r requirements.txt (use .venv first)"
	@echo "  make up                   — docker compose up -d (db + api)"
	@echo "  make down                 — docker compose down"
	@echo "  make build                — docker compose build $(API_SERVICE)"
	@echo "  make logs                 — follow api logs"
	@echo "  make ps                   — compose ps"
	@echo "  make restart              — restart api"
	@echo "  make test                 — pytest locally ($(PYTEST_ARGS))"
	@echo "  make test-docker          — pytest inside compose (same DB as api)"
	@echo "  make test-docker-cov      — pytest + coverage (pyproject.toml, fail-under 80%)"
	@echo "  make alembic-upgrade      — alembic upgrade head (one-off api container)"
	@echo "  make alembic-revision MSG=... — create empty revision (set MSG)"
	@echo "  make load-test-overbooking — 100 concurrent POST /bookings (needs up)"
	@echo "  make shell-api            — sh in api container"
	@echo "  make shell-db             — psql as openpms"

install:
	$(PIP) install -r requirements.txt

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

build:
	$(COMPOSE) build $(API_SERVICE)

logs:
	$(COMPOSE) logs -f $(API_SERVICE)

ps:
	$(COMPOSE) ps

restart:
	$(COMPOSE) restart $(API_SERVICE)

test:
	$(PYTHON) -m pytest tests/ $(PYTEST_ARGS)

test-docker: build
	$(COMPOSE) run --rm $(API_SERVICE) sh -c "alembic upgrade head && pytest tests/ -v $(PYTEST_ARGS)"

test-docker-cov: build
	$(COMPOSE) run --rm $(API_SERVICE) sh -c "alembic upgrade head && pytest tests/ -v --cov=app --cov-config=pyproject.toml --cov-report=term-missing $(PYTEST_ARGS)"

alembic-upgrade: build
	$(COMPOSE) run --rm $(API_SERVICE) alembic upgrade head

alembic-revision: build
	@test -n "$(MSG)" || (echo 'Usage: make alembic-revision MSG="your message"' && exit 1)
	$(COMPOSE) run --rm $(API_SERVICE) alembic revision -m "$(MSG)"

# Inherits DATABASE_URL / JWT_SECRET from compose + project .env (same as running api).
# Needs headroom for 100 concurrent DB sessions (api pool) and Postgres max_connections (compose db).
load-test-overbooking: build
	@DB_POOL_SIZE=$${DB_POOL_SIZE:-60} DB_MAX_OVERFLOW=$${DB_MAX_OVERFLOW:-40} \
		$(COMPOSE) up -d --force-recreate $(API_SERVICE)
	@sleep 12
	$(COMPOSE) run --rm -e PYTHONPATH=/app $(API_SERVICE) \
		python scripts/load_test_overbooking.py --base-url http://$(API_SERVICE):8000 \
		$$(test -n "$$LOAD_TEST_CONCURRENCY" && echo --concurrency $$LOAD_TEST_CONCURRENCY)

shell-api:
	$(COMPOSE) exec $(API_SERVICE) sh

shell-db:
	$(COMPOSE) exec $(DB_SERVICE) psql -U $${POSTGRES_USER:-openpms} -d $${POSTGRES_DB:-openpms}
