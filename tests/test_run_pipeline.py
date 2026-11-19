"""Tests for run_pipeline.py (no emulators or network needed)."""
from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import run_pipeline
from run_pipeline import build_steps, is_healthy, main, parse_args, wait_until_healthy, with_scheme


def names(argv):
    return [name for name, _ in build_steps(parse_args(argv))]


def test_default_steps_in_order():
    assert names([]) == ["scrape", "parquet", "upload", "load"]


def test_skip_scrape_drops_only_the_scrape_step():
    assert names(["--skip-scrape"]) == ["parquet", "upload", "load"]


def test_options_are_passed_to_scripts():
    steps = dict(build_steps(parse_args(["--copies", "7", "--mode", "uri"])))
    assert steps["parquet"] == ["build_parquet.py", "--copies", "7"]
    assert steps["load"] == ["load_to_bigquery.py", "--mode", "uri"]


def test_rejects_bad_copies():
    with pytest.raises(SystemExit):
        parse_args(["--copies", "0"])


@pytest.mark.parametrize(
    "address, expected",
    [
        ("localhost:4443", "http://localhost:4443"),
        ("http://localhost:9060", "http://localhost:9060"),
    ],
)
def test_with_scheme(address, expected):
    assert with_scheme(address) == expected


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        code = 200 if self.path == "/ok" else 404 if self.path == "/missing" else 500
        self.send_response(code)
        self.end_headers()

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_is_healthy_for_working_server(server):
    assert is_healthy(f"{server}/ok")
    assert is_healthy(f"{server}/missing")  # a 404 still proves a real server answered


def test_is_unhealthy_when_server_returns_5xx(server):
    assert not is_healthy(f"{server}/boom")


def test_is_unhealthy_when_nothing_listens():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert not is_healthy(f"http://127.0.0.1:{port}/x", request_timeout=0.5)


def test_is_unhealthy_when_proxy_accepts_then_resets():
    """Simulates localgcp's lazy proxy when the container behind it failed to start."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    port = listener.getsockname()[1]

    def accept_and_close():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            conn.close()

    threading.Thread(target=accept_and_close, daemon=True).start()
    try:
        assert not is_healthy(f"http://127.0.0.1:{port}/x", request_timeout=1)
    finally:
        listener.close()


def test_wait_until_healthy_gives_up_after_deadline():
    assert not wait_until_healthy("http://127.0.0.1:1/x", wait_seconds=0.3, pause=0.1)


def test_wait_until_healthy_succeeds_immediately(server):
    assert wait_until_healthy(f"{server}/ok", wait_seconds=5)


def test_stops_at_first_failure(monkeypatch):
    ran = []

    def fake_run_step(name, command, timeout=None):
        ran.append(name)
        return 3 if name == "parquet" else 0

    monkeypatch.setattr(run_pipeline, "run_step", fake_run_step)
    assert main(["--skip-preflight"]) == 3
    assert ran == ["scrape", "parquet"]  # upload and load never ran


def test_preflight_failure_runs_nothing(monkeypatch):
    monkeypatch.setattr(run_pipeline, "preflight", lambda *a, **k: False)
    monkeypatch.setattr(run_pipeline, "run_step", lambda *a, **k: pytest.fail("should not run"))
    assert main([]) == 2


def test_success_returns_zero(monkeypatch):
    monkeypatch.setattr(run_pipeline, "run_step", lambda name, command, timeout=None: 0)
    assert main(["--skip-preflight"]) == 0


def test_run_step_returns_exit_code_of_script(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("raise SystemExit(0)")
    bad = tmp_path / "bad.py"
    bad.write_text("raise SystemExit(5)")
    assert run_pipeline.run_step("ok", [str(ok)]) == 0
    assert run_pipeline.run_step("bad", [str(bad)]) == 5


def test_run_step_kills_script_that_runs_too_long(tmp_path):
    slow = tmp_path / "slow.py"
    slow.write_text("import time; time.sleep(30)")
    assert run_pipeline.run_step("slow", [str(slow)], timeout=0.5) == run_pipeline.TIMEOUT_EXIT_CODE


def test_rejects_bad_timeout():
    with pytest.raises(SystemExit):
        parse_args(["--step-timeout", "0"])
