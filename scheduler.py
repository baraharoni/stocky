"""
scheduler.py — Catalyst Alpha v1.0
Runs --morning, --squeeze at 15:00 and --eod at 23:45 (Asia/Jerusalem, Mon-Fri).
"""

import subprocess
import sys
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(level=logging.INFO, format="%(asctime)s [scheduler] %(message)s")
logger = logging.getLogger(__name__)


def run_cmd(*args):
    logger.info("Running: python main.py %s", " ".join(args))
    result = subprocess.run([sys.executable, "main.py", *args])
    if result.returncode != 0:
        logger.error("Command failed: python main.py %s (exit %d)", " ".join(args), result.returncode)


def start_scheduler() -> BackgroundScheduler:
    tz = "Asia/Jerusalem"
    sched = BackgroundScheduler(timezone=tz)

    sched.add_job(
        lambda: run_cmd("--morning"),
        CronTrigger(hour=15, minute=0, day_of_week="mon-fri", timezone=tz),
        id="morning",
        name="Morning scan",
    )
    sched.add_job(
        lambda: run_cmd("--squeeze"),
        CronTrigger(hour=15, minute=0, day_of_week="mon-fri", timezone=tz),
        id="squeeze",
        name="Squeeze scan",
    )
    sched.add_job(
        lambda: run_cmd("--eod"),
        CronTrigger(hour=23, minute=45, day_of_week="mon-fri", timezone=tz),
        id="eod",
        name="End-of-day scan",
    )

    sched.start()
    logger.info("Scheduler started. Jobs: %s", [j.name for j in sched.get_jobs()])
    return sched


if __name__ == "__main__":
    import time
    sched = start_scheduler()
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()
