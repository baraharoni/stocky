"""
telegram_bot.py — Catalyst Alpha v1.0
Remote-control Telegram bot for the multi-agent prediction system.

Commands
--------
/start   — welcome + command list
/help    — same as /start
/morning — run the Alpha morning pipeline
/eod     — run the EOD review pipeline
/squeeze — run the Short Squeeze & Float Rotation pipeline
/history — update T+3 / T+7 / T+30 historical returns
/picks   — show today's Alpha picks from the database
/snipes  — show today's Squeeze picks from the database

Usage
-----
    python telegram_bot.py

Requires TELEGRAM_BOT_TOKEN in .env (get one from @BotFather on Telegram).
"""

import asyncio
import os
import subprocess
import sys
from datetime import date

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

# ─── Config ───────────────────────────────────────────────────────────────────

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Absolute path to the Python interpreter running this script, and the project
# directory — ensures subprocess commands work regardless of cwd.
PYTHON  = sys.executable
PROJ_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _run_pipeline(flag: str) -> tuple[bool, str]:
    """
    Run `python main.py <flag>` in a thread-pool executor so the bot's event
    loop isn't blocked.  Returns (success, stderr_or_stdout_on_error).
    """
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: subprocess.run(
            [PYTHON, "main.py", flag],
            capture_output=True,
            text=True,
            cwd=PROJ_DIR,
        ),
    )
    if result.returncode == 0:
        return True, ""
    # Surface first non-empty stderr line as the error detail
    err = (result.stderr or result.stdout or "unknown error").strip()
    first_line = next((l for l in err.splitlines() if l.strip()), err)
    return False, first_line


def _format_picks(picks: list[dict], title: str, footer: str) -> str:
    """Build a clean, readable Telegram message for a list of picks."""
    today_str = date.today().strftime("%A, %d %b %Y")
    lines = [f"*{title} — {today_str}*", ""]

    for i, pick in enumerate(picks, 1):
        ticker     = pick.get("ticker", "—")
        confidence = pick.get("confidence_score")
        rationale  = (pick.get("pm_rationale") or "_No rationale recorded._").strip()
        conf_str   = f"{confidence:.0%}" if confidence is not None else "N/A"

        lines += [
            f"*{i}. {ticker}*  ·  Confidence: `{conf_str}`",
            rationale,
            "",
        ]

    lines.append(footer)
    return "\n".join(lines)


# ─── Command handlers ─────────────────────────────────────────────────────────

HELP_TEXT = (
    "*Catalyst Alpha v1.0 — Remote Control* 🤖\n"
    "\n"
    "*── Alpha Breakout Strategy ──*\n"
    "*/morning*  — Run the morning Alpha pipeline\n"
    "  _(DataAgent → PM → Analyst → Reporter)_\n"
    "\n"
    "*/eod*  — Run the EOD performance review\n"
    "  _(Manager fetches actuals, writes feedback)_\n"
    "\n"
    "*/picks*  — Show today's Alpha picks from the DB\n"
    "  _(Read-only; no API calls)_\n"
    "\n"
    "*── Short Squeeze Strategy ──*\n"
    "*/squeeze*  — Run the Short Squeeze & Float Rotation pipeline\n"
    "  _(Quantitative screen → catalyst validation → save picks)_\n"
    "\n"
    "*/snipes*  — Show today's Squeeze picks from the DB\n"
    "  _(Read-only; no API calls)_\n"
    "\n"
    "*── Shared ──*\n"
    "*/history*  — Update T+3 / T+7 / T+30 returns for ALL picks\n"
    "  _(Alpha Decay tracker — safe to run daily)_\n"
    "\n"
    "*/help*  — Show this message\n"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⏳ Running morning pipeline… this may take a few minutes.",
    )
    ok, err = await _run_pipeline("--morning")
    if ok:
        await update.message.reply_text("✅ Morning pipeline executed successfully.")
    else:
        await update.message.reply_text(f"❌ Error: {err}")


async def cmd_eod(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⏳ Running EOD review… this may take a minute.",
    )
    ok, err = await _run_pipeline("--eod")
    if ok:
        await update.message.reply_text("✅ EOD review executed successfully.")
    else:
        await update.message.reply_text(f"❌ Error: {err}")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ Updating historical returns…")
    ok, err = await _run_pipeline("--track-history")
    if ok:
        await update.message.reply_text("✅ History tracking updated successfully.")
    else:
        await update.message.reply_text(f"❌ Error: {err}")


async def cmd_squeeze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⏳ Running Short Squeeze & Float Rotation pipeline… this may take a few minutes.",
    )
    ok, err = await _run_pipeline("--squeeze")
    if ok:
        await update.message.reply_text(
            "✅ Squeeze pipeline executed successfully. Run /snipes to see today's picks."
        )
    else:
        await update.message.reply_text(f"❌ Error: {err}")


async def cmd_picks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from database import get_predictions_for_date

    today = date.today().isoformat()
    picks = get_predictions_for_date(today, strategy="alpha")

    if not picks:
        await update.message.reply_text(
            "No Alpha picks found for today yet. Run /morning first."
        )
        return

    msg = _format_picks(
        picks,
        title="Alpha Breakout Picks",
        footer="_Run /history to update T+3 / T+7 / T+30 returns._",
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_snipes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from database import get_predictions_for_date

    today = date.today().isoformat()
    picks = get_predictions_for_date(today, strategy="squeeze")

    if not picks:
        await update.message.reply_text(
            "No Squeeze picks found for today yet. Run /squeeze first."
        )
        return

    msg = _format_picks(
        picks,
        title="Short Squeeze Snipers",
        footer=(
            "_Setup: RVOL>2x | Float 5-20M | Short>10% | catalyst confirmed._\n"
            "_Run /history to update T+3 / T+7 / T+30 returns._"
        ),
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


# ─── Entry-point ──────────────────────────────────────────────────────────────

def main() -> None:
    if not TOKEN:
        print(
            "[ERROR] TELEGRAM_BOT_TOKEN is not set.\n"
            "  1. Open the .env file in this directory.\n"
            "  2. Add: TELEGRAM_BOT_TOKEN=<your token from @BotFather>\n"
            "  3. Re-run: python telegram_bot.py"
        )
        sys.exit(1)

    print("[OK] Starting Catalyst Alpha Telegram bot…")
    print("     Send /help in Telegram to get started.\n")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("morning", cmd_morning))
    app.add_handler(CommandHandler("eod",     cmd_eod))
    app.add_handler(CommandHandler("squeeze", cmd_squeeze))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("picks",   cmd_picks))
    app.add_handler(CommandHandler("snipes",  cmd_snipes))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
