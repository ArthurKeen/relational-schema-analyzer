# Implementation Plan — relational-schema-analyzer

Status: Draft v0.1
Companion to [`DESIGN.md`](DESIGN.md).

This plan is **phased and low-risk**: Stage 1 is a mechanical extraction of already-tested
code from `r2g`; the new conceptual/OWL value is built in Stage 2+ where it benefits the
whole ecosystem rather than being trapped inside `r2g`.

---

## Guiding principles

- **Reverse the dependency.** `r2g` ends up depending on this library, not vice versa.
- **Baseline first.** The deterministic, no-LLM path must produce a complete, useful bundle.
  LLM is additive refinement only.
- **Contract compatibility.** Match `arango-schema-mapper`'s tool-contract v1 wire shape so a
  single consumer (e.g. `arango-ontoextract`) handles relational and Arango sources.
- **No behavior change in r2g** during extraction — verified by r2g's existing test suite.

---

## Phase 0 — Repo bootstrap (this repo)

- [x] Create repo + git
- [x] `pyproject.toml` (hatchling, name `relational-schema-analyzer`, import
      `relational_schema_analyzer`, Python ≥ 3.10)
- [x] `relational_schema_analyzer/__init__.py` public API skeleton
- [x] `.gitignore`, license (Apache-2.0), CI stub (ruff + pytest)
- [x] `docs/tool-contract/v1/response.schema.json` (+ request schema, examples) copied/adapted
      from `arango-schema-mapper/docs/tool-contract/v1/`
- [x] Create GitHub repo and push `main`

Optional extras layout (mirror Arango analyzer):
`[postgres] [mysql] [sqlserver] [snowflake] [openai] [anthropic] [openrouter] [mcp] [dev]`

---

## Phase 1 — Extract the physical core (mechanical, low risk)

Lift from `r2g` with minimal edits. **Extraction inventory** (source → destination):

| `r2g` source | New library destination | Notes |
| --- | --- | --- |
| `src/r2g/types.py` (`Schema`/`Table`/`Column`/`ForeignKey`) | `relational_schema_analyzer/types.py` (rename `Schema` → `PhysicalSchema`, alias kept) | drop `MappingConfig`/`CollectionMapping`/`EdgeDefinition` (those stay in r2g) |
| `src/r2g/connectors/base.py` | `connectors/base.py` | `SourceConnector` protocol + `create_connector` factory |
| `src/r2g/connectors/postgres.py` | `connectors/postgres.py` | incl. partition metadata rollup |
| `src/r2g/connectors/mysql.py` | `connectors/mysql.py` | |
| `src/r2g/connectors/mssql.py` | `connectors/mssql.py` | |
| `src/r2g/connectors/snowflake.py` | `connectors/snowflake.py` | |
| `src/r2g/connectors/csv_source.py` | `connectors/csv_source.py` | |
| `src/r2g/connectors/session.py` | `connectors/session.py` | bulk-read protocol (kept for r2g reuse) |
| `src/r2g/fk_inference.py` | `fk_inference.py` | name + value-overlap heuristics |
| `src/r2g/schema_diff.py` | `schema_diff.py` | reused for fingerprint/drift |
| `src/r2g/topo_sort.py` | `topo_sort.py` | |
| `src/r2g/config.py::pg_type_to_json_type`, `DEFAULT_TYPE_MAP`, `_is_likely_join_table` | `typemap.py` + `heuristics.py` | the type map + join-table heuristic only |
| relevant `tests/` (`test_*_connector.py`, `test_fk_inference.py`, `test_schema_diff.py`, CSV) | `tests/` | port as-is |

**Stays in r2g** (not extracted): `config.py` (MappingConfig generation), `config_migrate.py`,
`transformers/`, `generators/`, `connectors/arango_reader.py`, `catalog.py`, `ui/`,
`mcp_server.py`, `main.py`.

Progress (extraction landed in this pass):
- [x] `types.py` (`PhysicalSchema` + `Schema` alias; `ForeignKey`/`Column`/`Table`)
- [x] `connectors/` (`base` + `create_connector` factory, `session`, `postgres`, `mysql`,
      `mssql`, `snowflake`, `csv_source`). Kafka **dropped** for v0 (decision §9.3).
- [x] `typemap.py` (`DEFAULT_TYPE_MAP` + `pg_type_to_json_type`), `heuristics.py`
      (`is_likely_join_table`), `naming.py` (dependency-free subset), `dump_reader.py`
- [x] `schema_diff.py`, `topo_sort.py`
- [x] `fk_inference.py` — decoupled from the Arango `EdgeDefinition`: the
      `to_edge_definition()` helper is replaced by relational-native
      `InferredForeignKey.to_foreign_key() -> ForeignKey`; all four value samplers
      (Postgres/MySQL/SQL Server/CSV) + `create_value_sampler` ported
- [x] Ported tests pass (191): connectors (base/csv/mysql/mssql/snowflake), `schema_diff`,
      `topo_sort`, physical `types`, `fk_inference`; ruff clean
- [ ] `relational-schema-analyzer snapshot` CLI emits `physical.json` (CLI is Phase 3)

Exit criteria:
- [x] `create_connector(...)` builds a connector for PG/MySQL/MSSQL/Snowflake/CSV
      (URL-parsing + protocol conformance covered; live `get_schema()` needs a DB)
- [x] ported connector + fk-inference + diff tests pass
- [ ] `relational-schema-analyzer snapshot` CLI emits `physical.json`

---

## Phase 2 — Conceptual model + deterministic baseline (new value)

- [ ] `conceptual.py` — `ConceptualSchema`, `Entity`, `Relationship`, `PropertyDef`
- [ ] `mapping.py` — relational `PhysicalMapping` (`TABLE` / `FOREIGN_KEY` / `JOIN_TABLE`)
- [ ] `baseline.py` — deterministic rules from DESIGN §4
  - table → entity; column → datatype property
  - FK → relationship (cardinality from PK/UNIQUE)
  - join table → N:M relationship + association properties
  - shared-PK-FK → `subClassOf` candidate (review-flagged)
- [ ] `metadata.py` — confidence scoring, `reviewRequired`, fingerprint (reuse `schema_diff`)
- [ ] `analyzer.py` — `RelationalSchemaAnalyzer.analyze(physical) -> Analysis`
- [ ] tests: golden bundles for Pagila / Chinook / Northwind (r2g already ships these SQL
      fixtures under `docker/`) — reuse as integration corpora

Exit criteria:
- [ ] `analyze` produces a valid bundle conforming to `docs/tool-contract/v1/response.schema.json`
- [ ] baseline runs with **no LLM** and flags ambiguous cases

---

## Phase 3 — Exports & tool contract

- [ ] `exports.py` — `export_bundle()` (tool-contract JSON), target-specific views
- [ ] `owl_export.py` — Turtle + JSON-LD with `phys:*` annotations (DESIGN §5)
- [ ] `cli.py` — `snapshot` / `analyze` / `owl` subcommands; stdin/stdout JSON contract
- [ ] validate emitted bundles against the JSON Schema in CI

Exit criteria:
- [ ] `owl --format turtle` produces a `.ttl` that `arango-ontoextract` can import
- [ ] round-trip: OWL `phys:*` annotations resolve back to source tables/columns

---

## Phase 4 — LLM refinement (optional, additive)

- [ ] `providers/` interface (openai / anthropic / openrouter) — copy pattern from
      `arango-schema-mapper/schema_analyzer/providers/`
- [ ] `workflow.py` — generate / validate / repair loop refining the baseline conceptual model
- [ ] embed-vs-link hints, n-ary recognition, semantic naming, denormalization detection
- [ ] eval harness (`eval/`) comparing baseline vs LLM-refined against golden corpora

---

## Phase 5 — MCP + ecosystem integration

- [ ] `mcp_server.py` + `relational-schema-analyzer-mcp` entry point
- [ ] **r2g integration PR**: add dependency, replace embedded modules with imports/shims,
      delete duplicated code, wire conceptual schema into `MappingConfig` generation
      (this realizes r2g's planned Phase 10 ontology derivation via the shared lib)
- [ ] **arango-ontoextract integration**: add relational source path that calls
      `export_owl_turtle()` + provenance, alongside the existing Arango path
- [ ] Coordinate with `arango-schema-mapper` maintainers on a **shared contract package** to
      retire `MappingBundle` duplication

---

## Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| Contract drift vs `arango-schema-analyzer` (consumers duplicate `MappingBundle`) | Pin compatible ranges; copy the v1 JSON Schema; drive toward a shared contract package in Phase 5 |
| Extraction breaks r2g | Stage 1 is behavior-preserving; gate on r2g's existing test suite; ship re-export shims |
| Relational physical mapping mistaken as Arango-queryable | DESIGN §1 makes the boundary explicit; consumers documented per-artifact |
| `arango-ontoextract` moving to its own direct extractor | Position this lib as an *additive* TTL+provenance source, not the sole path |
| Scope creep (Kafka, DDL parsing, Oracle/SQLite) | v0 = RDBMS + CSV only; defer the rest behind connector plugins |

---

## Suggested milestone cut

- **v0.1.0** — Phases 0–2 (physical core + baseline conceptual model + JSON bundle)
- **v0.2.0** — Phase 3 (OWL exports + CLI + contract validation)
- **v0.3.0** — Phase 5 r2g + ontoextract integrations
- **v0.4.0** — Phase 4 LLM refinement
