#!/bin/bash
set -e

# Run the scheduler in the background
python scheduler.py &
SCHEDULER_PID=$!
echo "Scheduler started (PID $SCHEDULER_PID)"

# Run Streamlit in the foreground (keeps the container alive)
streamlit run app.py \
    --server.port "$PORT" \
    --server.address 0.0.0.0 \
    --server.headless true

# If Streamlit exits, also kill the scheduler
kill $SCHEDULER_PID 2>/dev/null || true
