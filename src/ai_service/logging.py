"""Logging JSON terstruktur (structlog) — satu baris JSON per event ke stdout.

Meniru slog notification-service: field tetap `level`, `timestamp`, `event`, `logger`,
plus `request_id` dari contextvars untuk setiap request HTTP.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "info") -> None:
    numeric = logging.getLevelName(level.upper())
    if not isinstance(numeric, int):
        numeric = logging.INFO

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(numeric)

    # Request HTTP dicatat middleware kita sendiri; akses log uvicorn dimatikan.
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine", "openai"):
        logging.getLogger(noisy).setLevel(max(numeric, logging.WARNING))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
