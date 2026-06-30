"""Command-line interface: ``snapshot`` / ``analyze`` / ``owl``.

Mirrors the ArangoDB analyzer's CLI shape (DESIGN §6). Each subcommand obtains a
:class:`PhysicalSchema` either by introspecting a live source (``--source`` +
``--url``) or by loading a previously captured snapshot (``--from-snapshot
physical.json``), then emits JSON / OWL to a file or stdout.

    relational-schema-analyzer snapshot --source postgresql --url ... -o physical.json
    relational-schema-analyzer analyze  --from-snapshot physical.json --pretty
    relational-schema-analyzer owl      --source postgresql --url ... --format turtle
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import __version__
from .analyzer import RelationalSchemaAnalyzer
from .connectors import create_connector
from .owl_export import (
    DEFAULT_OWL_BASE_IRI,
    DEFAULT_OWL_PHYSICAL_IRI,
    export_owl_jsonld,
    export_owl_turtle,
)
from .types import PhysicalSchema


def _add_source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source", help="Source type: postgresql | mysql | sqlserver | snowflake | csv")
    p.add_argument("--url", help="Connection string / DSN (or CSV directory path)")
    p.add_argument(
        "--schema",
        default="public",
        help="Source schema / namespace (default: public; SQL Server folds to dbo)",
    )
    p.add_argument("--delimiter", default=",", help="CSV delimiter (csv source only)")
    p.add_argument(
        "--no-header",
        action="store_true",
        help="CSV files have no header row (csv source only)",
    )
    p.add_argument(
        "--from-snapshot",
        metavar="FILE",
        help="Load a previously captured physical.json instead of introspecting a live source",
    )


def _add_output_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("-o", "--out", metavar="FILE", help="Write output to FILE (default: stdout)")
    p.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")


def _load_physical(args: argparse.Namespace) -> PhysicalSchema:
    if args.from_snapshot:
        return PhysicalSchema.load_from_file(args.from_snapshot)
    if not args.source or not args.url:
        raise SystemExit(
            "error: provide either --from-snapshot FILE or both --source and --url"
        )
    source_params = {"delimiter": args.delimiter, "has_header": not args.no_header}
    connector = create_connector(
        args.source, args.url, schema_name=args.schema, source_params=source_params
    )
    return connector.get_schema()


def _emit(payload: str, out: str | None) -> None:
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(payload if payload.endswith("\n") else payload + "\n")
    else:
        sys.stdout.write(payload if payload.endswith("\n") else payload + "\n")


def _dump_json(obj: Any, *, pretty: bool) -> str:
    if pretty:
        return json.dumps(obj, indent=2, ensure_ascii=False)
    return json.dumps(obj, ensure_ascii=False)


def _cmd_snapshot(args: argparse.Namespace) -> int:
    physical = _load_physical(args)
    _emit(_dump_json(physical.model_dump(mode="json"), pretty=args.pretty), args.out)
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    physical = _load_physical(args)
    bundle = RelationalSchemaAnalyzer().analyze(physical).to_bundle()
    _emit(_dump_json(bundle, pretty=args.pretty), args.out)
    return 0


def _cmd_owl(args: argparse.Namespace) -> int:
    physical = _load_physical(args)
    analysis = RelationalSchemaAnalyzer().analyze(physical)
    if args.format == "turtle":
        payload = export_owl_turtle(
            analysis, base_iri=args.iri_base, phys_iri=args.phys_iri_base
        )
    else:
        payload = _dump_json(
            export_owl_jsonld(analysis, base_iri=args.iri_base, phys_iri=args.phys_iri_base),
            pretty=args.pretty,
        )
    _emit(payload, args.out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="relational-schema-analyzer",
        description="Analyze a relational schema into a conceptual model + OWL.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snapshot", help="Introspect a source and emit physical schema JSON")
    _add_source_args(p_snap)
    _add_output_args(p_snap)
    p_snap.set_defaults(func=_cmd_snapshot)

    p_analyze = sub.add_parser("analyze", help="Emit the conceptual bundle JSON")
    _add_source_args(p_analyze)
    _add_output_args(p_analyze)
    p_analyze.set_defaults(func=_cmd_analyze)

    p_owl = sub.add_parser("owl", help="Emit OWL (Turtle or JSON-LD)")
    _add_source_args(p_owl)
    _add_output_args(p_owl)
    p_owl.add_argument(
        "--format", choices=["turtle", "jsonld"], default="turtle", help="OWL serialization"
    )
    p_owl.add_argument("--iri-base", default=DEFAULT_OWL_BASE_IRI, help="Conceptual IRI base")
    p_owl.add_argument(
        "--phys-iri-base", default=DEFAULT_OWL_PHYSICAL_IRI, help="Physical-annotation IRI base"
    )
    p_owl.set_defaults(func=_cmd_owl)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
