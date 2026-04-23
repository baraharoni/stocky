"""
main.py - Catalyst Alpha v1.0
Orchestrator and CLI entry-point.

Usage:
  python main.py --morning        Run Alpha morning pipeline
  python main.py --eod            Run EOD review (Alpha strategy)
  python main.py --squeeze        Run Short Squeeze & Float Rotation pipeline
  python main.py --track-history  Update T+3/T+7/T+30 returns for all predictions
  python main.py --demo           Seed the DB with sample data for UI testing
  python main.py --init-db        Initialize / reset the database schema
  python main.py --dashboard      Launch the Streamlit dashboard
"""

import argparse
import subprocess
import sys

from dotenv import load_dotenv

from database import init_db, seed_demo_data, update_historical_returns

load_dotenv()


# --- Pipeline functions -------------------------------------------------------

def run_morning_prep() -> str:
    """
    Morning pipeline:
      DataAgent ("The Eyes")            - pre-market scan
      FintechPMAgent ("The Supervisor") - institutional filters
      AnalystAgent ("The Brain")        - 3 high-prob picks
      ReporterAgent ("The Mouth")       - save to DB
    """
    from agents import build_morning_crew   # deferred to avoid slow import at --help

    print()
    print("=" * 65)
    print("  CATALYST ALPHA v1.0  --  MORNING PREP")
    print("=" * 65)
    print()

    init_db()
    crew   = build_morning_crew()
    result = crew.kickoff()

    print()
    print("=" * 65)
    print("  [OK] Morning prep complete. Predictions saved to DB.")
    print("=" * 65)
    print()
    print(result)
    return str(result)


def run_eod_review() -> str:
    """
    EOD pipeline:
      ManagerAgent ("The Evaluator") - fetch actuals, calculate delta, write feedback.
      After the LLM review, automatically runs the Alpha Decay tracker so that
      T+3 / T+7 / T+30 returns are filled in for any window that has elapsed.
    """
    from agents import build_eod_crew

    print()
    print("=" * 65)
    print("  CATALYST ALPHA v1.0  --  EOD REVIEW")
    print("=" * 65)
    print()

    init_db()
    crew   = build_eod_crew()
    result = crew.kickoff()

    print()
    print("=" * 65)
    print("  [OK] EOD review complete. Feedback written to DB.")
    print("=" * 65)
    print()
    print(result)

    # ── Auto Alpha Decay: fill T+3 / T+7 / T+30 for any elapsed windows ──────
    print()
    print("  [>>] Running Alpha Decay tracker (T+3 / T+7 / T+30)...")
    summary = update_historical_returns()
    print(f"       Records scanned : {summary['records_scanned']}")
    print(f"       Records updated : {summary['records_updated']}")
    print(f"       Return cells    : {summary['updates_written']}")
    if summary["updates_written"] == 0:
        print("       (No new windows elapsed yet — nothing to update.)")
    print("  [OK] Alpha Decay tracker complete.")
    print()

    return str(result)


def run_squeeze_pipeline() -> str:
    """
    Short Squeeze & Float Rotation pipeline:
      SqueezeAgent — screens market, validates catalysts, saves strategy='squeeze' picks
    """
    from agents import build_squeeze_crew

    print()
    print("=" * 65)
    print("  CATALYST ALPHA v1.0  --  SHORT SQUEEZE & FLOAT ROTATION")
    print("=" * 65)
    print()

    init_db()
    crew   = build_squeeze_crew()
    result = crew.kickoff()

    print()
    print("=" * 65)
    print("  [OK] Squeeze pipeline complete. Picks saved with strategy='squeeze'.")
    print("=" * 65)
    print()
    print(result)
    return str(result)


def run_track_history() -> None:
    """
    Alpha Decay tracker: fills in return_3d / return_7d / return_30d for every
    prediction whose window has elapsed but whose return is still NULL.
    """
    print()
    print("=" * 65)
    print("  CATALYST ALPHA v1.0  --  ALPHA DECAY TRACKER")
    print("=" * 65)
    print()

    init_db()
    summary = update_historical_returns()

    print(f"  Records scanned : {summary['records_scanned']}")
    print(f"  Records updated : {summary['records_updated']}")
    print(f"  Return cells    : {summary['updates_written']}")
    print()
    print("  [OK] Historical returns updated. Refresh the dashboard to see T+3/T+7/T+30.")
    print("=" * 65)


def launch_dashboard() -> None:
    """Spawns the Streamlit dashboard in a subprocess."""
    print("[>>] Launching Streamlit dashboard...")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", "8501"],
        check=True,
    )


# --- CLI ----------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="catalyst-alpha",
        description="Catalyst Alpha v1.0 -- NASDAQ Breakout Prediction Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --init-db          Initialise the SQLite database
  python main.py --demo             Seed realistic sample data
  python main.py --dashboard        Open the Streamlit dashboard
  python main.py --morning          Run morning prediction pipeline
  python main.py --eod              Run EOD performance review
        """,
    )
    parser.add_argument(
        "--morning",
        action="store_true",
        help="Run morning prep  (DataAgent -> PM -> Analyst -> Reporter)",
    )
    parser.add_argument(
        "--eod",
        action="store_true",
        help="Run EOD review  (Manager evaluates actuals & writes feedback)",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialise the SQLite database schema",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Seed the database with realistic sample data for UI testing",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch the Streamlit dashboard (app.py) on port 8501",
    )
    parser.add_argument(
        "--track-history",
        action="store_true",
        help="Fill in T+3 / T+7 / T+30 returns for past predictions (Alpha Decay)",
    )
    parser.add_argument(
        "--squeeze",
        action="store_true",
        help="Run Short Squeeze & Float Rotation pipeline (strategy='squeeze')",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    if args.init_db:
        init_db()
        print("[OK] Database initialised at catalyst_alpha.db")

    elif args.demo:
        seed_demo_data()
        print("[OK] Demo data seeded. Run `python main.py --dashboard` to view.")

    elif args.dashboard:
        launch_dashboard()

    elif args.morning:
        run_morning_prep()

    elif args.eod:
        run_eod_review()

    elif args.track_history:
        run_track_history()

    elif args.squeeze:
        run_squeeze_pipeline()

    else:
        parser.print_help()
        print()
        print("Tip: start with  python main.py --demo  then  python main.py --dashboard")


if __name__ == "__main__":
    main()
