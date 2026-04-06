#!/usr/bin/env bash
set -euo pipefail

# Apply schema then start the API. For Kubernetes, run `alembic upgrade head` in an
# initContainer and use a CMD that only starts uvicorn if you prefer migrations separate.

alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
