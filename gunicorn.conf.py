"""Gunicorn settings, each can be overridden with GUNICORN_CMD_ARGS"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("GUNICORN_WORKERS", "3"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
worker_class = "gthread"
# Requests longer than this are cut off, exports and imports run in celery
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = 30
keepalive = 75
# Worker temp files in memory, the root filesystem is read only
worker_tmp_dir = "/dev/shm"
# Recycle workers now and then, so a slow leak can't build up
max_requests = 2000
max_requests_jitter = 200
accesslog = "-"
errorlog = "-"
# The gateway in front sets X-Forwarded-*, it's the only way in
forwarded_allow_ips = "*"
proxy_allow_ips = "*"


# Prometheus metrics, added up across the workers and served by the master
# on their own port, so they're never reachable through the gateway. Only
# when PROMETHEUS_MULTIPROC_DIR is set, see config/prometheus.py
metrics_port = int(os.environ.get("METRICS_PORT", "9100"))


def on_starting(server):
    from config import prometheus

    prometheus.clear_directory()


def when_ready(server):
    from config import prometheus

    if prometheus.directory() is not None:
        prometheus.serve(metrics_port)
        server.log.info("Serving metrics on :%s", metrics_port)


def child_exit(server, worker):
    from config import prometheus

    prometheus.process_exited(worker.pid)


def post_fork(server, worker):
    # Exporters run threads, which don't survive a fork, so each worker sets
    # up its own
    from config import telemetry

    telemetry.configure("qsology-web")
