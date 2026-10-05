"""Structured logging: JSON lines to <workspace>/logs/wbs.log, short human lines to stderr."""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(workspace: Path | None = None, verbose: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger("wbs")
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(console)
    if workspace is not None:
        log_dir = workspace / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / "wbs.log", encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"wbs.{name}")


def log_event(logger: logging.Logger, msg: str, **fields: object) -> None:
    logger.info(msg, extra={"fields": fields})
