"""Minimal structured-logging shim.

The connectors call ``logger.info("event_name", key=value, ...)`` in the
structlog style. To keep this library dependency-light (no structlog, no
credential-scrubbing machinery from the originating project), we provide a tiny
adapter over the stdlib :mod:`logging` module that accepts arbitrary keyword
fields and renders them as ``key=value`` pairs.

If ``structlog`` is installed, we defer to it so applications that already
configure structured logging get native structured events.
"""

from __future__ import annotations

import logging
from typing import Any

try:  # pragma: no cover - exercised indirectly when structlog is present
    import structlog

    def get_logger(name: str) -> Any:
        return structlog.get_logger(name)

except ImportError:  # pragma: no cover - default lightweight path

    class _BoundLogger:
        """stdlib-backed logger with a structlog-like keyword interface."""

        def __init__(self, name: str) -> None:
            self._log = logging.getLogger(name)

        @staticmethod
        def _render(event: str, fields: dict[str, Any]) -> str:
            if not fields:
                return event
            extras = " ".join(f"{k}={v!r}" for k, v in fields.items())
            return f"{event} {extras}"

        def debug(self, event: str, **fields: Any) -> None:
            self._log.debug(self._render(event, fields))

        def info(self, event: str, **fields: Any) -> None:
            self._log.info(self._render(event, fields))

        def warning(self, event: str, **fields: Any) -> None:
            self._log.warning(self._render(event, fields))

        def error(self, event: str, **fields: Any) -> None:
            self._log.error(self._render(event, fields))

    def get_logger(name: str) -> Any:
        return _BoundLogger(name)
