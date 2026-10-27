"""Standalone background monitoring worker — fully automated pipeline.

Runs INDEPENDENTLY of the Streamlit dashboard. Start it in its own terminal:

    python -m worker                   # run once immediately, then on the schedule
    python -m worker --once            # run a single pass and exit
    python -m worker --once --dry-run  # test run: no DB writes, no emails sent
    python -m worker --interval daily  # override the .env interval for this session

The full automated pipeline executes in order on every run:
  1. Seed CSV sync    — imports new colleges from data/seed_colleges.csv
  2. Monitoring       — fetches college pages, extracts emails, records changes
  3. Auto-approve     — approves high-confidence contacts (if AUTO_APPROVE_ENABLED=true)
  4. Contact sync     — copies approved contact_records into the main contacts table
  5. Auto-send        — emails newly-approved contacts (if AUTO_SEND_ENABLED=true + live mode)

Each step is independently enabled/disabled in .env.
The process must stay running for scheduled checks to happen (use Task Scheduler or
run setup_scheduler.ps1 to register it as a Windows scheduled task).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from shortfundly import database as db
from shortfundly import monitoring_db as mdb
from shortfundly.config import load_config

# interval name -> seconds
INTERVAL_SECONDS = {
    "6h": 6 * 3600,
    "12h": 12 * 3600,
    "daily": 24 * 3600,
    "weekly": 7 * 24 * 3600,
    "manual": 0,
}


def _setup_logging(level: str = "INFO") -> None:
    """Configure root logger for the worker process."""
    numeric = getattr(logging, level.upper(), logging.INFO)
    log_dir = Path(__file__).resolve().parent / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "worker.log"

    logging.basicConfig(
        level=numeric,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


log = logging.getLogger(__name__)


def run_once(verbose: bool = True, dry_run: bool = False) -> dict:
    """Execute the full automated pipeline in one pass.

    Returns a combined summary dict with results from every step.
    When dry_run=True, no DB rows are written and no emails are sent,
    but all logic (scoring, discovery, template loading) still runs.
    """
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    summary: dict = {"dry_run": dry_run}

    try:
        mdb.init_monitoring(conn)

        # ------------------------------------------------------------------
        # Step 1: Seed CSV sync — import new colleges into the DB
        # ------------------------------------------------------------------
        log.info("=== Step 1/5: Seed CSV sync ===")
        try:
            from shortfundly.auto_discovery import sync_seed_colleges, create_seed_csv_template
            seed_path = cfg.seed_csv_path
            # Resolve relative paths from the project root
            if seed_path and not Path(seed_path).is_absolute():
                seed_path = str(Path(__file__).resolve().parent / seed_path)
            create_seed_csv_template(seed_path)
            disc = sync_seed_colleges(conn, seed_path, dry_run=dry_run)
            summary["discovery"] = disc
            if verbose:
                print(f"  Discovery: {disc}", flush=True)
        except Exception as exc:
            log.error("Step 1 (seed sync) failed: %s", exc)
            summary["discovery"] = {"error": str(exc)}

        # ------------------------------------------------------------------
        # Step 2: Monitoring — fetch pages, extract emails, record changes
        # ------------------------------------------------------------------
        log.info("=== Step 2/5: Monitoring ===")
        try:
            from monitoring.pipeline import run_monitoring

            def progress(done, total, name):
                if verbose:
                    print(f"  [{done}/{total}] checked: {name}", flush=True)

            mon = run_monitoring(
                conn, cfg,
                trigger="scheduled" if not dry_run else "manual",
                progress=progress,
            ) if not dry_run else {"skipped": True, "reason": "dry_run"}
            summary["monitoring"] = mon
            if verbose:
                print(f"  Monitoring: {mon}", flush=True)
        except Exception as exc:
            log.error("Step 2 (monitoring) failed: %s", exc)
            summary["monitoring"] = {"error": str(exc)}

        # ------------------------------------------------------------------
        # Step 3: Auto-approve — score and approve high-confidence contacts
        # ------------------------------------------------------------------
        log.info("=== Step 3/5: Auto-approve ===")
        try:
            from shortfundly.auto_approve import auto_approve_pending
            if cfg.auto_approve_enabled or dry_run:
                appr = auto_approve_pending(
                    conn,
                    threshold=cfg.auto_approve_threshold,
                    dry_run=dry_run,
                )
                summary["auto_approve"] = appr
                if verbose:
                    print(f"  Auto-approve: {appr}", flush=True)
            else:
                msg = "AUTO_APPROVE_ENABLED=false — skipped (set in .env to enable)"
                log.info(msg)
                summary["auto_approve"] = {"skipped": True, "reason": msg}
                if verbose:
                    print(f"  Auto-approve: {msg}", flush=True)
        except Exception as exc:
            log.error("Step 3 (auto-approve) failed: %s", exc)
            summary["auto_approve"] = {"error": str(exc)}

        # ------------------------------------------------------------------
        # Step 4: Contact sync — copy approved records to main contacts table
        # ------------------------------------------------------------------
        log.info("=== Step 4/5: Contact sync ===")
        try:
            from shortfundly.auto_approve import sync_approved_to_contacts
            sync = sync_approved_to_contacts(conn, dry_run=dry_run)
            summary["contact_sync"] = sync
            if verbose:
                print(f"  Contact sync: {sync}", flush=True)
        except Exception as exc:
            log.error("Step 4 (contact sync) failed: %s", exc)
            summary["contact_sync"] = {"error": str(exc)}

        # ------------------------------------------------------------------
        # Step 5: Auto-send — email newly approved contacts
        # ------------------------------------------------------------------
        log.info("=== Step 5/5: Auto-send ===")
        try:
            from shortfundly.auto_sender import auto_send_new_contacts
            send = auto_send_new_contacts(conn, cfg, dry_run=dry_run)
            summary["auto_send"] = send
            if verbose:
                print(f"  Auto-send: {send}", flush=True)
        except Exception as exc:
            log.error("Step 5 (auto-send) failed: %s", exc)
            summary["auto_send"] = {"error": str(exc)}

    finally:
        conn.close()

    log.info("Run complete: %s", summary)
    if verbose:
        print(f"\nRun complete: {summary}", flush=True)
    return summary


def _interval_seconds(name: str) -> int:
    return INTERVAL_SECONDS.get((name or "daily").lower(), INTERVAL_SECONDS["daily"])


def run_scheduler(interval_name: str, dry_run: bool = False) -> None:
    """Run run_once() now, then repeatedly on the configured interval."""
    secs = _interval_seconds(interval_name)
    if secs <= 0:
        print("Interval is 'manual' — running once and exiting.", flush=True)
        run_once(dry_run=dry_run)
        return

    # Prefer APScheduler when available.
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore
        print(
            f"APScheduler configured: interval={interval_name} ({secs}s). "
            "Running an initial pass now...",
            flush=True,
        )
        run_once(dry_run=dry_run)
        scheduler = BlockingScheduler()
        scheduler.add_job(
            lambda: run_once(dry_run=dry_run),
            "interval",
            seconds=secs,
            id="monitor",
            max_instances=1,
            coalesce=True,
        )
        print("Scheduler RUNNING. Press Ctrl+C to stop.", flush=True)
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            print("Scheduler stopped.", flush=True)
        return
    except ImportError:
        pass  # fall through to simple sleep loop

    # Fallback: simple sleep loop (no external dependency).
    print(
        f"APScheduler not available; using a simple loop, interval={interval_name} "
        f"({secs}s). Press Ctrl+C to stop.",
        flush=True,
    )
    try:
        while True:
            run_once(dry_run=dry_run)
            time.sleep(secs)
    except (KeyboardInterrupt, SystemExit):
        print("Worker stopped.", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Shortfundly fully-automated monitoring + email worker"
    )
    parser.add_argument("--once", action="store_true", help="Run a single pipeline pass and exit")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simulate the full pipeline without writing to DB or sending emails",
    )
    parser.add_argument(
        "--interval", default="",
        help="Override schedule: 6h | 12h | daily | weekly | manual",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    _setup_logging(cfg.log_level)

    interval = args.interval or cfg.monitor_interval
    dry_run = args.dry_run

    if dry_run:
        print("=== DRY RUN MODE — no DB writes, no emails sent ===", flush=True)

    if args.once or args.dry_run:
        run_once(dry_run=dry_run)
    else:
        run_scheduler(interval, dry_run=dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
