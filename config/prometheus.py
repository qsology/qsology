"""Serving Prometheus metrics from a server with several worker processes

Each worker counts on its own, so prometheus_client's multiprocess mode is
used: with PROMETHEUS_MULTIPROC_DIR set, every process writes its metrics
to files there, and one endpoint adds them all up. That endpoint is run by
whatever outlives the workers, so it doesn't matter which worker a scrape
would have landed on, and a worker being replaced doesn't reset anything.

This module only reads the directory, it never makes metrics of its own,
so it's safe to import in the server's master process. The metrics
themselves are in config.metrics.

Under gunicorn (gunicorn.conf.py), with sync, gthread or ASGI (uvicorn)
workers alike:
  - on_starting: clear_directory(), so a restart starts from nothing
  - when_ready: serve(port), in the master
  - child_exit: process_exited(pid), so its live gauges stop counting

For a server without hooks (e.g. uvicorn --workers), run the endpoint next
to it instead, from the same directory:

    python -m config.prometheus 9100"""

import os
import shutil
import sys
from pathlib import Path

from prometheus_client import CollectorRegistry, multiprocess, start_http_server

DIRECTORY = "PROMETHEUS_MULTIPROC_DIR"


def directory() -> Path | None:
    value = os.environ.get(DIRECTORY)
    return Path(value) if value else None


def clear_directory() -> None:
    """Remove what the last run left, before any worker starts"""
    path = directory()
    if path is None:
        return
    path.mkdir(parents=True, exist_ok=True)
    for entry in path.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def registry() -> CollectorRegistry:
    """Every worker's metrics, added up"""
    collected = CollectorRegistry()
    multiprocess.MultiProcessCollector(collected)
    return collected


def serve(port: int, addr: str = "0.0.0.0"):
    """Serve /metrics, returning the server and its thread"""
    return start_http_server(port, addr=addr, registry=registry())


def process_exited(pid: int) -> None:
    """Stop counting a dead worker's live gauges (e.g. requests in
    progress). Its counters and histograms are kept, so totals don't drop"""
    if directory() is not None:
        multiprocess.mark_process_dead(pid)


def main(argv: list[str]) -> int:
    if directory() is None:
        print(f"{DIRECTORY} isn't set", file=sys.stderr)
        return 1
    port = int(argv[1]) if len(argv) > 1 else 9100
    _, thread = serve(port)
    thread.join()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
