import subprocess
import sys
import textwrap
import urllib.request

import pytest
from config import prometheus

# A worker process: counts a request and holds a live gauge, then exits
WORKER = textwrap.dedent(
    """
    import os
    from prometheus_client import Counter, Gauge
    Counter("jobs", "Jobs", ["kind"]).labels("export").inc()
    Gauge("busy", "Busy", multiprocess_mode="livesum").set(1)
    print(os.getpid())
    """
)


@pytest.fixture
def directory(tmp_path, monkeypatch):
    path = tmp_path / "metrics"
    monkeypatch.setenv(prometheus.DIRECTORY, str(path))
    return path


def run_worker(path) -> int:
    env = {prometheus.DIRECTORY: str(path), "PATH": ""}
    result = subprocess.run(
        [sys.executable, "-c", WORKER],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout)


def sample(name, labels=None):
    return prometheus.registry().get_sample_value(name, labels or {})


class TestClearDirectory:
    def test_makes_it(self, directory):
        prometheus.clear_directory()
        assert directory.is_dir()

    def test_empties_it(self, directory):
        directory.mkdir()
        (directory / "counter_123.db").write_bytes(b"x")
        (directory / "stray").mkdir()
        prometheus.clear_directory()
        assert list(directory.iterdir()) == []

    def test_nothing_without_a_directory(self, monkeypatch):
        monkeypatch.delenv(prometheus.DIRECTORY, raising=False)
        assert prometheus.directory() is None
        prometheus.clear_directory()


class TestAcrossWorkers:
    def test_adds_up_every_worker(self, directory):
        prometheus.clear_directory()
        run_worker(directory)
        run_worker(directory)
        assert sample("jobs_total", {"kind": "export"}) == 2

    def test_dead_workers_counters_stay_and_live_gauges_go(self, directory):
        prometheus.clear_directory()
        pid = run_worker(directory)
        assert sample("busy") == 1
        prometheus.process_exited(pid)
        assert sample("jobs_total", {"kind": "export"}) == 1
        assert not sample("busy")

    def test_serves_them(self, directory):
        prometheus.clear_directory()
        run_worker(directory)
        server, _ = prometheus.serve(0, addr="127.0.0.1")
        try:
            url = f"http://127.0.0.1:{server.server_port}/metrics"
            with urllib.request.urlopen(url, timeout=5) as response:
                body = response.read().decode()
        finally:
            server.shutdown()
        assert 'jobs_total{kind="export"} 1.0' in body


class TestMain:
    def test_needs_the_directory(self, monkeypatch, capsys):
        monkeypatch.delenv(prometheus.DIRECTORY, raising=False)
        assert prometheus.main(["prometheus"]) == 1
        assert prometheus.DIRECTORY in capsys.readouterr().err
