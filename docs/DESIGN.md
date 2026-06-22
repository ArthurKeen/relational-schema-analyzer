# Design Spec — relational-schema-analyzer

Status: Draft v0.1
Audience: maintainers of `r2g`, `arango-ontoextract`, `arango-schema-mapper`, and future
relational-native query tooling.

---

## 1. Purpose & scope

`relational-schema-analyzer` introspects a relational database and produces:

1. A **physical schema** — a faithful, paradigm-neutral model of the source database
   (tables, columns, primary keys, foreign keys, types, constraints).
2. A **conceptual schema** — entities, relationships, and properties (≈ OWL classes,
   object properties, datatype properties) inferred from the physical schema.
3. A **physical mapping** — the conceptual → relational back-reference, recording exactly
   which table/column/FK each conceptual element came from.
4. **Metadata** — confidence, fingerprints, detected patterns, provenance, review flags.
5. Optional **exports** — OWL Turtle / JSON-LD, plus target-specific views.

### Explicit non-goals

- It does **not** load data, transform rows, or write to any target. (That stays in `r2g`.)
- It does **not** generate an ArangoDB physical mapping (collections/edges). The ArangoDB
  physical layout is the job of `arango-schema-analyzer` (post-load) or `r2g`'s
  `MappingConfig`. This library's `physicalMapping` is **relational**.
- It does **not** parse DDL files. Introspection is via live catalog views (matching the
  current `r2g` connector behavior). A DDL-parsing connector may be added later.

### The architectural fact that shapes the contract

The ArangoDB analyzer's `physicalMapping` is queryable by `arango-cypher` / `arango-sparql`
because those tools query ArangoDB. A **relational** physical mapping is **not** directly
consumable by the AQL transpilers — relational data only becomes Arango-queryable *after*
`r2g` loads it, at which point the Arango physical mapping comes from `r2g`/the Arango
analyzer. Therefore the immediate consumers of this library are:

| Consumer | Consumes | Contract |
| --- | --- | --- |
| `arango-ontoextract` | OWL Turtle + provenance | `.ttl` file + provenance dict |
| `r2g` | conceptual schema + relational physical mapping | JSON bundle (in-process import) |
| Future SQL-native transpilers | conceptual schema + relational physical mapping | JSON bundle |
| Tooling / docs | JSON-LD, Markdown | exports |

The shared contract (chosen: **"all of the above via one tool contract"**) is the JSON
bundle `{ conceptualSchema, physicalMapping, metadata }`, aligned 1:1 with
`arango-schema-mapper/docs/tool-contract/v1/`, with a relational `physicalMapping` variant.

---

## 2. High-level architecture

```text
                         ┌─────────────────────────────────────────┐
                         │           relational_schema_analyzer      │
                         │                                           │
  source URL ─────────►  │  connectors/  ──►  PhysicalSchema         │
                         │       (pg, mysql, mssql, snowflake, csv)  │
                         │                        │                  │
                         │   fk_inference (optional, when FKs absent)│
                         │                        │                  │
                         │                        ▼                  │
                         │  baseline.py (deterministic rules)        │
                         │       + optional llm/ refinement          │
                         │                        │                  │
                         │                        ▼                  │
                         │   ConceptualSchema + PhysicalMapping      │
                         │                + Metadata                 │
                         │                        │                  │
                         │   exports: bundle JSON │ owl ttl/jsonld   │
                         └────────────────────────┼──────────────────┘
                                                  ▼
                            CLI  ·  MCP server  ·  Python API
```

Mirrors the ArangoDB analyzer's split:

- `snapshot` (physical) → `analyze` (conceptual) → `export` (bundle / OWL / views)
- **deterministic baseline** always works with no LLM and sets `reviewRequired` flags;
  LLM is *additive refinement*, never required.

A key difference / advantage: **relational → conceptual is mostly deterministic** because
the source schema is explicit. The LLM's job is refinement (embed-vs-link hints, n-ary
recognition, naming, denormalization detection), not structural recovery.

---

## 3. Data model

### 3.1 Physical schema (lifted from `r2g/src/r2g/types.py`)

```text
PhysicalSchema
  name: str
  source_type: "postgresql" | "mysql" | "sqlserver" | "snowflake" | "csv"
  namespace: str | None          # pg schema / mssql "dbo" / snowflake schema / mysql db
  tables: list[Table]

Table
  name: str
  columns: list[Column]
  primary_key: list[str]         # supports composite PKs
  foreign_keys: list[ForeignKey]
  is_partitioned / partition_of  # PG partition metadata (already in r2g)

Column
  name / type / nullable / unique / default

ForeignKey
  columns: list[str]             # composite-safe
  ref_table: str
  ref_columns: list[str]
```

This is intentionally identical to r2g's current `Schema`/`Table`/`Column`/`ForeignKey`
so the extraction is mechanical and r2g can import it back unchanged.

### 3.2 Conceptual schema (new; mirrors `schema_analyzer/conceptual.py`)

```text
ConceptualSchema
  entities: list[Entity]
  relationships: list[Relationship]
  properties: list[Property]     # usually empty; properties live under entities

Entity
  name: str                      # OWL class local name
  labels: list[str]
  properties: list[PropertyDef]  # {name, type, indexed, unique, nullable}
  source: { kind: "baseline" | "llm" | "human" }

Relationship
  type: str                      # OWL object property local name
  fromEntity / toEntity
  properties: list[PropertyDef]  # association attributes (from join-table columns)
  inverseOf: str | None
  cardinality: "1:1" | "1:N" | "N:M"
  source: { kind: ... }
```

### 3.3 Physical mapping (new; relational variant of `schema_analyzer/mapping.py`)

The back-reference from each conceptual element to the relational source. Uses an explicit
**style enum** mirroring the Arango analyzer's pattern, but with relational semantics:

```text
PhysicalMapping
  entities: { <EntityName>: EntityPhysical }
  relationships: { <RelType>: RelationshipPhysical }

EntityPhysical
  style: "TABLE"                 # one entity per table (baseline)
  schema: str | None
  tableName: str
  properties: [ { conceptualName, columnName, sqlType, nullable, unique } ]
  primaryKey: list[str]

RelationshipPhysical
  style: "FOREIGN_KEY" | "JOIN_TABLE"
  # FOREIGN_KEY: relationship realized by an FK on one entity's table
  fromTable / fromColumns / toTable / toColumns
  # JOIN_TABLE (N:M): relationship realized by an associative table
  joinTable / joinFromColumns / joinToColumns / attributeColumns
```

### 3.4 Metadata (mirrors Arango analyzer)

```text
Metadata
  confidence: float
  reviewRequired: bool
  timestamp / generator / version
  physicalSchemaFingerprint: str   # for drift detection (reuse r2g schema_diff)
  patterns: [ "join_table", "inheritance_via_shared_pk", "soft_delete", ... ]
  provenance: { ... }
```

---

## 4. Conceptual inference rules (deterministic baseline)

Because relational schemas are explicit, the baseline produces a high-quality conceptual
model with no LLM. Rules:

| Relational construct | Conceptual result |
| --- | --- |
| Table (non-join) | `Entity` / `owl:Class` |
| Column | datatype `PropertyDef` (SQL type → XSD/JSON type via shared type map) |
| Primary key | entity identity; functional property hint |
| `UNIQUE` column | `owl:InverseFunctionalProperty` hint |
| Foreign key (N:1) | `Relationship`, `style=FOREIGN_KEY`, `cardinality=1:N` |
| FK column also `UNIQUE` | `cardinality=1:1` |
| Associative / join table (2 FKs, PK = those FKs) | `Relationship`, `style=JOIN_TABLE`, `cardinality=N:M`; extra columns → relationship properties |
| Shared-PK-is-also-FK (table whose PK is an FK to parent) | candidate `rdfs:subClassOf` (inheritance) — flag for review |
| Missing declared FKs | run `fk_inference` (name + value-overlap heuristics) → relationships marked lower confidence |

Join-table detection reuses r2g's existing `_is_likely_join_table()` heuristic.

### LLM refinement (optional, additive)

Mirrors the Arango analyzer's generate/validate/repair loop. Refines, never replaces:

- embed-vs-link recommendations (for downstream graph mapping)
- n-ary relationship recognition (associative tables with >2 FKs)
- semantic naming (pluralization, FK column → relationship name)
- denormalization detection (planned in r2g `PLAN-denormalization-analysis.md`)

Providers behind an interface (`openai` / `anthropic` / `openrouter` extras), same as
`arango-schema-mapper/schema_analyzer/providers/`.

---

## 5. OWL export

`owl_export.py` serializes the conceptual schema to Turtle / JSON-LD with **physical
back-links as annotation properties**, mirroring `schema_analyzer/owl_export.py`:

- `owl:Class` per entity
- `owl:DatatypeProperty` per column-derived property (with `rdfs:domain`, `rdfs:range`)
- `owl:ObjectProperty` per relationship (`rdfs:domain`/`rdfs:range`, `owl:inverseOf`)
- `owl:FunctionalProperty` / `owl:InverseFunctionalProperty` from PK / UNIQUE
- physical annotations under a `phys:` namespace pointing back to source:
  - `phys:mappingStyle`, `phys:tableName`, `phys:columnName`, `phys:schemaName`
  - `phys:fromColumns`, `phys:toColumns`, `phys:joinTable`

Default IRIs (configurable), parallel to the Arango analyzer:

- conceptual: `http://arangodb.com/schema/relational#`
- physical annotations: `http://arangodb.com/schema/physical#`

This is exactly the artifact `arango-ontoextract` consumes (TTL + provenance dict).

---

## 6. Public API

```python
from relational_schema_analyzer import (
    create_connector,        # factory: source_type + url → Connector
    RelationalSchemaAnalyzer,
    export_bundle,           # analysis → tool-contract JSON dict
    export_owl_turtle,       # analysis → .ttl string
    export_owl_jsonld,       # analysis → JSON-LD dict
)

conn = create_connector("postgresql", url, namespace="public")
physical = conn.get_schema()                      # PhysicalSchema

analyzer = RelationalSchemaAnalyzer(llm_provider=None)   # baseline, no LLM
analysis = analyzer.analyze(physical)              # conceptual + mapping + metadata

bundle = export_bundle(analysis)                   # {conceptualSchema, physicalMapping, metadata}
ttl    = export_owl_turtle(analysis)
```

### CLI (entry points mirror the Arango analyzer)

```bash
relational-schema-analyzer snapshot --source postgresql --url ... -o physical.json
relational-schema-analyzer analyze  --source postgresql --url ... --pretty
relational-schema-analyzer owl      --source postgresql --url ... --format turtle
```

### MCP server

`relational-schema-analyzer-mcp` exposing `snapshot` / `analyze` / `owl` over the same
JSON tool contract (stdio).

---

## 7. Tool contract ("all of the above via one contract")

We adopt the **same wire shape** as
`arango-schema-mapper/docs/tool-contract/v1/response.schema.json`:

```json
{
  "conceptualSchema": { "entities": [], "relationships": [], "properties": [] },
  "physicalMapping":  { "entities": {}, "relationships": {} },
  "metadata":         { "confidence": 0.0, "reviewRequired": true }
}
```

Differences are confined to `physicalMapping` style enums (relational `TABLE` /
`FOREIGN_KEY` / `JOIN_TABLE` vs Arango `COLLECTION` / `LABEL` / `DEDICATED_COLLECTION` /
`GENERIC_WITH_TYPE`). `conceptualSchema` is **identical in shape** to the Arango analyzer,
which is what lets one consumer handle both source paradigms.

We ship our own JSON Schema at `docs/tool-contract/v1/response.schema.json` and coordinate
with the Arango analyzer maintainers toward a **shared contract package** to avoid the
`MappingBundle` duplication that already exists across `arango-cypher-py` /
`arango-sparql-py`. Until that exists, we publish the schema + example payloads and pin
compatible version ranges.

---

## 8. Relationship to `r2g`

- `r2g` **depends on** `relational-schema-analyzer` (dependency direction reversed).
- r2g deletes its embedded `types.Schema`, `connectors/`, `fk_inference.py`, `schema_diff.py`
  and re-exports them from the library (shim for back-compat), or imports directly.
- r2g's ArangoDB-specific code (`MappingConfig`, transformers, loaders, `arango_reader`)
  **stays in r2g**.
- r2g's planned Phase 10 LLM ontology derivation is implemented **here** instead, and r2g
  consumes the conceptual schema to seed `MappingConfig`.

See `docs/IMPLEMENTATION-PLAN.md` §"Extraction inventory" for the exact file moves.

---

## 9. Open questions

1. **License** — match the Arango ecosystem libraries (confirm which: Apache-2.0?).
2. **Shared contract package** — extract a `*-schema-contract` package now, or copy the
   JSON Schema and converge later? (Recommend: copy now, converge in a follow-up.)
3. **Connector parity** — port all of r2g's connectors (incl. Kafka) or only the RDBMS +
   CSV ones for v0? (Recommend: RDBMS + CSV for v0; Kafka is arguably out of scope for a
   *relational* analyzer.)
4. **IRI namespace** — confirm `arangodb.com/schema/relational#` vs a vendor-neutral host.
