#!/bin/bash
set -e

# Ensure SQLite schema exists on the persistent volume before Streamlit / scheduler touch the DB.
echo "[start.sh] initializing database schema (${DB_PATH:-catalyst_alpha.db})"
python -c "from database import init_db; init_db()"

(
  while true; do
    echo "[start.sh] launching scheduler.py"
    python scheduler.py || echo "[start.sh] scheduler exited rc=$?"
    sleep 5
  done
) &

exec streamlit run app.py \
    --server.port "$PORT" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false
