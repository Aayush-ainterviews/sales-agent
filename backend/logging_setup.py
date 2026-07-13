"""
Structured JSON logging (Phase 5). One JSON object per line so Railway's log viewer
(and any aggregator) can filter by field. No new dependency — just the stdlib.

Use `event(logger, "turn_complete", user_id=..., duration_s=...)` for structured events;
plain `logger.info("text")` still works (it just gets wrapped with ts/level/logger).
"""

import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            obj.update(fields)
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, default=str)


def configure(level: int = logging.INFO) -> None:
    """Install the JSON formatter on the root handler (replacing basicConfig)."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def event(logger: logging.Logger, name: str, **fields) -> None:
    """Emit a structured event: {ts, level, logger, msg=<name>, event=<name>, ...fields}."""
    logger.info(name, extra={"fields": {"event": name, **fields}})
