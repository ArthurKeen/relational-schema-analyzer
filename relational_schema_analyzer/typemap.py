"""SQL type → JSON/conceptual type mapping.

Extracted from ``r2g/src/r2g/config.py`` (the ``DEFAULT_TYPE_MAP`` table and
``pg_type_to_json_type`` helper). Covers PostgreSQL, MySQL/MariaDB, SQL Server,
and Snowflake ``DATA_TYPE`` base names. Conceptual inference (DESIGN §4) maps these
JSON types onward to XSD/OWL datatypes.
"""

from __future__ import annotations

from typing import Dict

DEFAULT_TYPE_MAP: Dict[str, str] = {
    "integer": "integer",
    "int": "integer",
    "int4": "integer",
    "int8": "integer",
    "int2": "integer",
    "bigint": "integer",
    "smallint": "integer",
    "serial": "integer",
    "bigserial": "integer",
    "smallserial": "integer",
    "oid": "integer",
    "numeric": "float",
    "decimal": "float",
    "real": "float",
    "float4": "float",
    "float8": "float",
    "double precision": "float",
    "money": "string",
    "boolean": "boolean",
    "bool": "boolean",
    "json": "object",
    "jsonb": "object",
    "uuid": "string",
    "text": "string",
    "varchar": "string",
    "character varying": "string",
    "char": "string",
    "character": "string",
    "bpchar": "string",
    "name": "string",
    "bytea": "string",
    "date": "string",
    "time": "string",
    "time without time zone": "string",
    "time with time zone": "string",
    "timetz": "string",
    "timestamp": "string",
    "timestamp without time zone": "string",
    "timestamp with time zone": "string",
    "timestamptz": "string",
    "interval": "string",
    "inet": "string",
    "cidr": "string",
    "macaddr": "string",
    "macaddr8": "string",
    "xml": "string",
    "point": "string",
    "line": "string",
    "lseg": "string",
    "box": "string",
    "path": "string",
    "polygon": "string",
    "circle": "string",
    "tsvector": "string",
    "tsquery": "string",
    "bit": "string",
    "bit varying": "string",
    "varbit": "string",
    # MySQL / MariaDB integer + text + blob variants (DATA_TYPE base names).
    "tinyint": "integer",
    "mediumint": "integer",
    "year": "integer",
    "tinytext": "string",
    "mediumtext": "string",
    "longtext": "string",
    "tinyblob": "string",
    "blob": "string",
    "mediumblob": "string",
    "longblob": "string",
    "enum": "string",
    "set": "string",
    # SQL Server variants (DATA_TYPE names). `bit` is boolean in SQL Server but
    # a bit-string in PostgreSQL, so the conflict is resolved in the connector
    # rather than here. `tinyint`/`smallmoney`/`money`/`real` are already mapped.
    "nvarchar": "string",
    "nchar": "string",
    "ntext": "string",
    "datetime2": "string",
    "smalldatetime": "string",
    "datetimeoffset": "string",
    "uniqueidentifier": "string",
    "smallmoney": "string",
    "image": "string",
    "number": "float",
    "fixed": "float",
    "float": "float",
    "double": "float",
    "float32": "float",
    "float64": "float",
    "binary": "string",
    "varbinary": "string",
    "string": "string",
    "datetime": "string",
    "timestamp_ltz": "string",
    "timestamp_ntz": "string",
    "timestamp_tz": "string",
    "variant": "object",
    "object": "object",
    "array": "array",
    "geography": "object",
    "geometry": "object",
    "vector": "array",
}


def _base_pg_type_name(pg_type: str) -> str:
    t = pg_type.strip().lower()
    if "(" in t:
        t = t.split("(", 1)[0].strip()
    return t


def _is_array_pg_type(pg_type: str) -> bool:
    s = pg_type.strip().lower()
    return "[]" in s or s.startswith("array") or s.endswith("[]")


def pg_type_to_json_type(pg_type: str) -> str:
    if _is_array_pg_type(pg_type):
        return "array"
    base = _base_pg_type_name(pg_type)
    if base in DEFAULT_TYPE_MAP:
        return DEFAULT_TYPE_MAP[base]
    return "string"
