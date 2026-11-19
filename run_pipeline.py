"""Run the whole pipeline with one command.

    scrape -> build Parquet -> upload to Cloud Storage -> load into BigQuery

Each step is an existing script. This runner starts them one after another and
stops at the first failure, so a broken scrape never leads to uploading a
stale file. The runner exits with the failed step's exit code, which is how
schedulers (cron, Cloud Run Jobs, GitHub Actions) learn that a run failed.

Usage:
    python run_pipeline.py
    python run_pipeline.py --skip-scrape          # reuse output/hockey_teams.csv
    python run_pipeline.py --copies 500
    python run_pipeline.py --mode uri             # real GCP: BigQuery reads gs:// itself
    python run_pipeline.py --skip-preflight       # do not check the emulators first
    python run_pipeline.py --preflight-timeout 120  # wait longer for BigQuery to start
    python run_pipeline.py --step-timeout 300     # kill any step running over 5 minutes
"""
from __future__ import annotations

import argparse
import http.client
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import List, Tuple
from urllib.parse import urlparse

from common import setup_logger
from common.gcp_local import BIGQUERY_EMULATOR_HOST, PROJECT_ID, STORAGE_EMULATOR_HOST

log = setup_logger("pipeline")

Step = Tuple[str, List[str]]  # (step name, script and its arguments)

TIMEOUT_EXIT_CODE = 124  # same code the Unix `timeout` command uses


def build_steps(args: argparse.Namespace) -> List[Step]:
    """The ordered list of steps to run. Pure function, so it is easy to test."""
    steps: List[Step] = []
    if not args.skip_scrape:
        steps.append(("scrape", ["scrape_hockey_teams_senior.py"]))
    steps += [
        ("parquet", ["build_parquet.py", "--copies", str(args.copies)]),
        ("upload", ["upload_to_gcs.py"]),
        ("load", ["load_to_bigquery.py", "--mode", args.mode]),
    ]
    return steps


def with_scheme(address: str) -> str:
    """'localhost:4443' -> 'http://localhost:4443'. Addresses with a scheme are unchanged."""
    return address if "://" in address else f"http://{address}"


def is_healthy(url: str, request_timeout: float = 5.0) -> bool:
    """True if the service at `url` answers an HTTP request with a real reply.

    Checking only that a port accepts connections is not enough: localgcp's
    BigQuery proxy accepts connections even when the Docker container behind
    it failed to start, and then resets every request. So we send a request
    and require an HTTP response. Any status below 500 counts as healthy,
    because it proves a working server answered.
    """
    try:
        with urllib.request.urlopen(url, timeout=request_timeout):
            return True
    except urllib.error.HTTPError as err:  # server replied with an error status
        return err.code < 500
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        # connection refused, reset, closed without a reply, or timed out
        return False


def wait_until_healthy(url: str, wait_seconds: float, pause: float = 2.0) -> bool:
    """Retry is_healthy until it succeeds or `wait_seconds` have passed.

    The first request to localgcp's BigQuery starts its container, which can
    take a while (up to ~30s if the image must be pulled), so one try is not enough.
    """
    deadline = time.monotonic() + wait_seconds
    while True:
        if is_healthy(url):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(pause)


def preflight(wait_seconds: float = 60.0) -> bool:
    """Check that Cloud Storage and BigQuery really work before starting any step."""
    checks = [
        (
            "Cloud Storage",
            f"{with_scheme(STORAGE_EMULATOR_HOST)}/storage/v1/b?project={PROJECT_ID}",
            "start localgcp: localgcp up --services=bigquery",
        ),
        (
            "BigQuery",
            f"{with_scheme(BIGQUERY_EMULATOR_HOST)}/bigquery/v2/projects/{PROJECT_ID}/datasets",
            "start localgcp with BigQuery: localgcp up --services=bigquery --data-dir=\"$PWD/.localgcp\" "
            "(Docker must be running; use an ABSOLUTE --data-dir path). "
            "Check the localgcp terminal for 'Failed to start container' errors",
        ),
    ]
    ok = True
    for name, url, fix in checks:
        if wait_until_healthy(url, wait_seconds):
            log.info("Service healthy", extra={"service": name})
        else:
            ok = False
            log.error(
                "Service not healthy, aborting before any step runs",
                extra={"service": name, "url": url, "waited_seconds": wait_seconds, "hint": fix},
            )
    return ok


def run_step(name: str, command: List[str], timeout: float | None = None) -> int:
    """Run one script as its own process and return its exit code.

    If the script runs longer than `timeout` seconds it is killed and
    TIMEOUT_EXIT_CODE is returned, so a stuck step cannot block the run forever.
    """
    log.info("Step started", extra={"step": name, "command": command})
    started = time.monotonic()
    try:
        # sys.executable is the Python running this file, so the same virtualenv is used.
        code = subprocess.run([sys.executable, *command], timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        log.error("Step timed out", extra={"step": name, "timeout_seconds": timeout})
        return TIMEOUT_EXIT_CODE
    seconds = round(time.monotonic() - started, 1)
    if code == 0:
        log.info("Step finished", extra={"step": name, "seconds": seconds})
    else:
        log.error("Step failed", extra={"step": name, "exit_code": code, "seconds": seconds})
    return code


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-scrape", action="store_true", help="reuse the existing CSV")
    parser.add_argument("--copies", type=int, default=200, help="how many times to repeat the rows")
    parser.add_argument("--mode", choices=["insert", "uri"], default="insert", help="BigQuery load mode")
    parser.add_argument("--step-timeout", type=float, default=900, help="seconds before a step is killed")
    parser.add_argument("--preflight-timeout", type=float, default=60, help="seconds to wait for services to become healthy")
    parser.add_argument("--skip-preflight", action="store_true", help="do not check emulators first")
    args = parser.parse_args(argv)
    if args.copies < 1:
        parser.error("--copies must be at least 1")
    if args.step_timeout <= 0:
        parser.error("--step-timeout must be positive")
    if args.preflight_timeout < 0:
        parser.error("--preflight-timeout cannot be negative")
    return args


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.skip_preflight and not preflight(args.preflight_timeout):
        return 2

    started = time.monotonic()
    for name, command in build_steps(args):
        code = run_step(name, command, args.step_timeout)
        if code != 0:
            return code

    log.info("Pipeline finished", extra={"seconds": round(time.monotonic() - started, 1)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
