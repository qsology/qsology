import logging


class TraceContextFilter(logging.Filter):
    """Makes sure every record has trace and span ids for the log format,
    0 when there's no trace or OpenTelemetry isn't set up"""

    def filter(self, record: logging.LogRecord) -> bool:
        for name in ("otelTraceID", "otelSpanID"):
            if not hasattr(record, name):
                setattr(record, name, "0")
        return True
