"""Programmatic v1 tool-contract entrypoint: ``run_tool(request) -> response``.

A single dict-in / dict-out function that drives the ``snapshot`` / ``analyze`` /
``owl`` operations. It is the substrate the MCP server (and any non-interactive
tool caller) wraps, and it is fully testable without the ``mcp`` package.

Request shape (relational variant of the shared tool contract):

    {
      "contractVersion": "1",
      "requestId": "opt-id",
      "operation": "snapshot" | "analyze" | "owl",
      "source": {                       # a live source to introspect ...
        "type": "postgresql|mysql|sqlserver|snowflake|duckdb|databricks|csv",
        "url": "<connection string / DSN / path>",
        "schema": "public",             # optional namespace
        "params": { ... }               # optional source_params (e.g. csv delimiter)
      },
      "input": { "physical": { ... } }, # ... OR a previously captured PhysicalSchema
      "owl": { "format": "turtle"|"jsonld", "iriBase": "...", "physIriBase": "..." }
    }

Response envelope (aligned with the shared response contract):

    { "contractVersion": "1", "requestId": ..., "operation": ..., "ok": bool,
      "result": { ... } | "error": { "code": str, "message": str } }
"""

from __future__ import annotations

from typing import Any

from .analyzer import RelationalSchemaAnalyzer
from .connectors import create_connector
from .owl_export import export_owl_jsonld, export_owl_turtle
from .types import PhysicalSchema

CONTRACT_VERSION = "1"
_OPERATIONS = ("snapshot", "analyze", "owl")


def _response(request: dict[str, Any], *, ok: bool, **extra: Any) -> dict[str, Any]:
    resp: dict[str, Any] = {
        "contractVersion": CONTRACT_VERSION,
        "operation": request.get("operation") if isinstance(request, dict) else None,
        "ok": ok,
    }
    if isinstance(request, dict) and request.get("requestId"):
        resp["requestId"] = request["requestId"]
    resp.update(extra)
    return resp


def _error(request: Any, code: str, message: str) -> dict[str, Any]:
    return _response(
        request if isinstance(request, dict) else {}, ok=False,
        error={"code": code, "message": message},
    )


def _load_physical(request: dict[str, Any]) -> PhysicalSchema:
    """Introspect a live source, or load a previously captured PhysicalSchema."""
    given = request.get("input") or {}
    if isinstance(given, dict) and given.get("physical") is not None:
        return PhysicalSchema.model_validate(given["physical"])

    source = request.get("source")
    if not isinstance(source, dict) or not source.get("type") or not source.get("url"):
        raise ValueError("request needs 'input.physical' or 'source' with 'type' and 'url'")
    connector = create_connector(
        source["type"],
        source["url"],
        schema_name=source.get("schema", "public"),
        source_params=source.get("params"),
    )
    return connector.get_schema()


def run_tool(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one tool-contract operation and return the response envelope."""
    if not isinstance(request, dict):
        return _error(None, "INVALID_REQUEST", "request must be a JSON object")

    operation = request.get("operation")
    if operation not in _OPERATIONS:
        return _error(
            request, "INVALID_REQUEST",
            f"operation must be one of {', '.join(_OPERATIONS)}",
        )

    try:
        physical = _load_physical(request)
    except ValueError as err:
        return _error(request, "INVALID_REQUEST", str(err))
    except Exception as err:  # noqa: BLE001 - surface source/connection failures cleanly
        return _error(request, "SOURCE_ERROR", str(err))

    try:
        if operation == "snapshot":
            return _response(
                request, ok=True, result={"physical": physical.model_dump(mode="json")}
            )

        analysis = RelationalSchemaAnalyzer().analyze(physical)

        if operation == "analyze":
            return _response(request, ok=True, result={"analysis": analysis.to_bundle()})

        # operation == "owl"
        owl_opts = request.get("owl") or {}
        fmt = owl_opts.get("format", "turtle")
        kwargs = {}
        if owl_opts.get("iriBase"):
            kwargs["base_iri"] = owl_opts["iriBase"]
        if owl_opts.get("physIriBase"):
            kwargs["phys_iri"] = owl_opts["physIriBase"]
        if fmt == "jsonld":
            content = export_owl_jsonld(analysis, **kwargs)
        elif fmt == "turtle":
            content = export_owl_turtle(analysis, **kwargs)
        else:
            return _error(request, "INVALID_REQUEST", "owl.format must be turtle or jsonld")
        return _response(request, ok=True, result={"owl": {"format": fmt, "content": content}})
    except Exception as err:  # noqa: BLE001 - never let an operation crash the server
        return _error(request, "ANALYSIS_ERROR", str(err))
