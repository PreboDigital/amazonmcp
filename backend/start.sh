#!/bin/bash
set -e
# Run migrations before starting the app
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
