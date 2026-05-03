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

from database import (
    init_db,
    seed_demo_data,
    update_historical_returns,
    validate_and_backfill_all,
    merge_duplicate_predictions,
)

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

    # ── Auto Alpha Decay: fill T+3 / T+7 / T+14 / T+30 for any elapsed windows ──
    print()
    print("  [>>] Running Alpha Decay tracker (Session / EOD / T+3 / T+7 / T+14 / T+30)...")
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
    Alpha Decay tracker: fills in actual_eod_change, return_session, and
    return_3d / return_7d / return_14d / return_30d for every row that still
    has NULLs where data is available. Existing values are never overwritten.
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
    print("  [OK] Historical returns updated. Refresh the dashboard to see Session / EOD / T+3 / T+7 / T+14 / T+30.")
    print("=" * 65)


def run_validate(*, overwrite: bool = False) -> None:
    """
    Audit every prediction row against yfinance.

    Without --fix-mismatches: fills NULLs and reports cells whose stored value
    diverges from the canonical yfinance value by more than 0.5 percentage
    points (no overwrite — the operator decides).

    With --fix-mismatches: recomputes EVERY elapsed cell from yfinance and
    writes the canonical value, so every number on the dashboard is provably
    fresh.
    """
    print()
    print("=" * 65)
    if overwrite:
        print("  CATALYST ALPHA v1.0  --  DATA AUDIT  (overwrite mode)")
    else:
        print("  CATALYST ALPHA v1.0  --  DATA AUDIT  (gap-fill + report)")
    print("=" * 65)
    print()

    init_db()

    print("  [>>] Consolidating duplicate rows (one per ticker / strategy / day)...")
    dup = merge_duplicate_predictions()
    print(f"       Groups scanned : {dup['groups_scanned']}")
    print(f"       Groups merged  : {dup['groups_merged']}")
    print(f"       Rows updated   : {dup['rows_updated']}")
    print(f"       Rows deleted   : {dup['rows_deleted']}")

    print()
    print("  [>>] Validating every row against yfinance...")
    audit = validate_and_backfill_all(overwrite=overwrite)
    print(f"       Records scanned : {audit['records_scanned']}")
    print(f"       Records updated : {audit['records_updated']}")
    print(f"       Cells written   : {audit['updates_written']}")
    if audit.get("cells_filled"):
        print(f"       NULLs filled    :")
        for col, n in audit["cells_filled"].items():
            if n:
                print(f"           {col:<20} {n}")
    if audit.get("unfetchable"):
        print(f"       Unfetchable     : {len(audit['unfetchable'])} ticker(s)")
        for label in audit["unfetchable"][:10]:
            print(f"           - {label}")
        if len(audit["unfetchable"]) > 10:
            print(f"           ... and {len(audit['unfetchable']) - 10} more")
    if not overwrite and audit.get("mismatches"):
        print()
        print(f"  [!!] {len(audit['mismatches'])} cell(s) disagree with yfinance (> 0.5 pp).")
        print("       Re-run with --fix-mismatches to overwrite them.")
        for m in audit["mismatches"][:15]:
            print(
                f"       {m['date']} {m['ticker']:<6} {m['column']:<18} "
                f"stored={m['stored']:+.2f}  yfin={m['fetched']:+.2f}  "
                f"diff={m['diff']:+.2f}"
            )
        if len(audit["mismatches"]) > 15:
            print(f"       ... and {len(audit['mismatches']) - 15} more")
    elif not overwrite:
        print()
        print("  [OK] Every stored number agrees with yfinance within 0.5 pp.")
    print()
    print("=" * 65)


def launch_dashboard() -> None:
    """Spawns the Streamlit dashboard in a subprocess."""
    print("[>>] Launching Streamlit dashboard...")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", "8501"],
        check=True,
    )


def run_simulate_history(
    *,
    start: str | None = None,
    end:   str | None = None,
    run_id: str | None = None,
    top_n: int = 3,
    fresh: bool = False,
) -> None:
    """
    Replay the live Alpha pipeline across a historical window. For every
    business day in [start, end] this asks claude_llm to reproduce what the
    DataAgent → PM → Analyst → Reporter chain would have produced, using
    only data that was visible on that date. The top-N picks are persisted
    to `simulated_predictions` tagged by `run_id`.
    """
    from simulator import (
        DEFAULT_END,
        DEFAULT_START,
        simulate_range,
        compute_simulated_returns,
    )

    print()
    print("=" * 65)
    print("  CATALYST ALPHA v1.0  --  HISTORICAL SIMULATION")
    print("=" * 65)
    print()

    init_db()

    s = start or DEFAULT_START
    e = end   or DEFAULT_END

    overall = simulate_range(
        s, e,
        run_id=run_id,
        top_n=top_n,
        skip_existing=not fresh,
        progress=True,
    )

    print()
    print("  [>>] Backfilling Session / EOD / T+3 / T+7 / T+14 / T+30 / "
          "T+90 / T+180 / price_today...")
    summary = compute_simulated_returns(run_id=overall.get("run_id"))
    print(f"       Records scanned       : {summary['records_scanned']}")
    print(f"       Records updated       : {summary['records_updated']}")
    print(f"       Return cells written  : {summary['updates_written']}")
    print(f"       Target hits resolved  : {summary['target_hits_resolved']}")
    print()
    print(f"  [OK] Simulation complete. run_id = {overall.get('run_id')}")
    print(
        f"       Open the dashboard ('python main.py --dashboard') and "
        "switch to the 'Historical Simulation' page in the sidebar."
    )
    print("=" * 65)


def run_simulate_returns_refresh(*, run_id: str | None = None) -> None:
    """Refresh price_today + extended return horizons for simulated picks."""
    from simulator import compute_simulated_returns

    print()
    print("=" * 65)
    print("  CATALYST ALPHA v1.0  --  SIMULATED RETURNS REFRESH")
    print("=" * 65)
    print()

    init_db()
    summary = compute_simulated_returns(run_id=run_id)

    print(f"  Records scanned       : {summary['records_scanned']}")
    print(f"  Records updated       : {summary['records_updated']}")
    print(f"  Return cells written  : {summary['updates_written']}")
    print(f"  Target hits resolved  : {summary['target_hits_resolved']}")
    print()
    print("  [OK] Simulated picks now reflect the latest closes.")
    print("=" * 65)


def run_simulate_targets(*, redo_all: bool = False) -> None:
    """
    Back-simulate analyst sell targets for historical predictions using
    claude_llm with only pick-time context. Then resolve hits against
    yfinance T+30 intraday highs.
    """
    from agents import simulate_historical_targets

    print()
    print("=" * 65)
    if redo_all:
        print("  CATALYST ALPHA v1.0  --  TARGET SIMULATION  (redo all rows)")
    else:
        print("  CATALYST ALPHA v1.0  --  TARGET SIMULATION  (missing only)")
    print("=" * 65)
    print()
    print("  [>>] Asking claude_llm for a sell target on every eligible pick...")
    print()

    init_db()
    summary = simulate_historical_targets(only_missing=not redo_all, progress=True)

    print()
    print(f"  Rows scanned             : {summary['scanned']}")
    print(f"  LLM calls issued         : {summary['asked']}")
    print(f"  Targets written          : {summary['written']}")
    print(f"  Rejected (out of range)  : {summary['rejected_invalid']}")
    print(f"  Rejected (no pick price) : {summary['rejected_no_pick_price']}")
    print(f"  LLM errors               : {summary['llm_errors']}")
    print(f"  Hits resolved (T+30 high): {summary['hits_resolved']}")
    print()
    print("  [OK] Target simulation complete. Refresh the dashboard to see "
          "sell targets and hit/miss badges.")
    print("=" * 65)


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
  python main.py --validate         Audit DB vs yfinance + report mismatches
  python main.py --validate --fix-mismatches  Same, but overwrite divergent cells
  python main.py --simulate-targets         Back-simulate sell targets via LLM
  python main.py --simulate-targets --redo-all  Same, but overwrite existing targets
  python main.py --simulate-history         Back-test Alpha pipeline Sep-25 → Mar-26
  python main.py --simulate-history --start 2026-02-01 --end 2026-02-28
                                            Custom window
  python main.py --simulate-returns --run-id sim_xxx
                                            Refresh price_today / T+90 / T+180
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
        help="Fill in Session / EOD / T+3 / T+7 / T+14 / T+30 returns for past predictions",
    )
    parser.add_argument(
        "--squeeze",
        action="store_true",
        help="Run Short Squeeze & Float Rotation pipeline (strategy='squeeze')",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Audit every row against yfinance: merges dupes, fills NULLs, reports mismatches",
    )
    parser.add_argument(
        "--fix-mismatches",
        action="store_true",
        help="With --validate: also overwrite stored cells that disagree with yfinance",
    )
    parser.add_argument(
        "--simulate-targets",
        action="store_true",
        help="Back-simulate sell targets via claude_llm for picks with no target",
    )
    parser.add_argument(
        "--redo-all",
        action="store_true",
        help="With --simulate-targets: also overwrite picks that already have a target",
    )
    parser.add_argument(
        "--simulate-history",
        action="store_true",
        help=(
            "Back-test the Alpha pipeline across a historical window. "
            "Default window: 2025-09-01 to 2026-03-31. Top 3 picks per "
            "trading day are saved to simulated_predictions."
        ),
    )
    parser.add_argument(
        "--simulate-returns",
        action="store_true",
        help=(
            "Refresh return windows (T+3/7/14/30/90/180) and price_today "
            "for already-saved simulated picks."
        ),
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="With --simulate-history: window start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="With --simulate-history: window end date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help=(
            "With --simulate-history / --simulate-returns: tag the run "
            "(or refresh a specific past run). Auto-generated if omitted."
        ),
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=3,
        help="With --simulate-history: number of picks per day (default 3)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help=(
            "With --simulate-history: re-run dates that already have rows "
            "for this run_id (default skips them)."
        ),
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

    elif args.validate:
        run_validate(overwrite=args.fix_mismatches)

    elif args.simulate_targets:
        run_simulate_targets(redo_all=args.redo_all)

    elif args.simulate_history:
        run_simulate_history(
            start=args.start,
            end=args.end,
            run_id=args.run_id,
            top_n=args.top_n,
            fresh=args.fresh,
        )

    elif args.simulate_returns:
        run_simulate_returns_refresh(run_id=args.run_id)

    elif args.squeeze:
        run_squeeze_pipeline()

    else:
        parser.print_help()
        print()
        print("Tip: start with  python main.py --demo  then  python main.py --dashboard")


if __name__ == "__main__":
    main()
