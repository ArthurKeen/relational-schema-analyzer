"""Relational source connectors.

Extracted from ``r2g/src/r2g/connectors/``. Concrete connectors are imported
lazily by :func:`create_connector` so optional DB-driver dependencies are only
loaded for the source type actually in use.
"""

from __future__ import annotations

from .base import (
    SUPPORTED_SOURCE_TYPES,
    SourceConnector,
    create_connector,
    create_source_connector,
    expand_env_vars,
    is_mysql,
    is_postgresql,
    is_sqlserver,
    normalize_source_type,
    serialize_rows,
)
from .session import SourceSession

__all__ = [
    "SUPPORTED_SOURCE_TYPES",
    "SourceConnector",
    "SourceSession",
    "create_connector",
    "create_source_connector",
    "expand_env_vars",
    "is_mysql",
    "is_postgresql",
    "is_sqlserver",
    "normalize_source_type",
    "serialize_rows",
]
