#!/bin/bash
set -e
# Run migrations first; if DB unreachable or timeout (60s), continue so app can start
timeout 60 alembic upgrade head || echo "Alembic skipped (DB unreachable or timeout)"
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
