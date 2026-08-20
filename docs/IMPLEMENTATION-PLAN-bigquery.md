# Implementation plan — BigQuery source

**Status:** ACCEPTED 2026-08-12. Companion to [`DESIGN-ADDENDUM-bigquery.md`](DESIGN-ADDENDUM-bigquery.md)
(the *what* and *why*) and [`PLAN-bigquery.md`](PLAN-bigquery.md) (dates, risks, cross-repo).

Nine slices. Slices 0–5 are the Aug 20 critical path; 6–7 are post-demo. Each is independently
mergeable and leaves the suite green.

---

## Touch-point inventory

Every place a source type is named today, established by tracing `databricks` through the repo.
This is the full edit surface for a ninth source — worth having in one table, since half of these
are one-liners that are easy to miss.

| File | Line(s) | Change |
| --- | --- | --- |
| `relational_schema_analyzer/connectors/bigquery.py` | new | the connector |
| `connectors/base.py` | 81–91 | add `"bigquery"` to `SUPPORTED_SOURCE_TYPES` |
| `connectors/base.py` | ~203–206 | factory branch (lazy import, beside `databricks`) |
| `typemap.py` | `DEFAULT_TYPE_MAP` | BigQuery base type names (D2) |
| `pyproject.toml` | ~42 | `bigquery = ["google-cloud-bigquery>=3"]`; add to `dev` |
| `cli.py` | 39 | `--source` help string |
| `cli.py` | new | `--overlay` (Slice 4) |
| `tool.py` | 15 | tool-contract `type` description |
| `fk_inference.py` | ~1134 (beside `DatabricksValueSampler`) | `BigQueryValueSampler` (Slice 6) |
| `fk_inference.py` | 1494–1539 | `create_value_sampler` branch (Slice 6) |
| `samplers.py` | `executor_from_connection` | **Slice 6, found while building Slice 4.** The discriminator's `ValueEnumerator` and taxonomy's `SpecializationCounter` are built here from a **DB-API connection**, not from the per-connector samplers. BigQuery's native client is not DB-API, so Slice 6 needs either a `google.cloud.bigquery.dbapi` adapter or a BigQuery-specific executor — and either way its SQL bypasses the cost guard as written. Cheap to handle, expensive to discover late |
| `overlay.py` | new | ✅ landed (Slice 4) |
| `README.md` | 139 | source list + a BigQuery usage/cost note |
| `docs/DESIGN.md` | 94, §9.3 | mermaid connector list; §9.3 "Update" paragraph |
| `docs/IMPLEMENTATION-PLAN.md` | testing matrix | add the BigQuery row |
| `tests/test_bigquery_connector.py` | new | mock-cursor + conformance |
| `tests/integration/conftest.py` | — | `RSA_BIGQUERY_DSN` fixture |
| `examples/gdelt/` | new | demo artifacts (Slice 5) |
| **AOE** `backend/app/services/relational_schema_extraction.py` | 61 | description string |
| **AOE** `backend/app/mcp/tools/relational.py` | 44, 87 | description strings |
| **AOE** `frontend/src/components/workspace/RelationalExtractionOverlay.tsx` | ~52 | dropdown entry |

---

## Slice 0 — Recon and fixture capture (M0 gate)

**No production code.** Establish that the thing is reachable and that the addendum's §2 table is
true, then freeze reality into a test fixture. Scratch scripts only.

- [ ] GCP project with billing enabled; ADC configured (`GOOGLE_APPLICATION_CREDENTIALS` or
      `gcloud auth application-default login`); `$GOOGLE_CLOUD_PROJECT` set
- [ ] **Set a billing alert and a custom query quota on the project before the first query.**
      This is the cheapest insurance available and it takes two minutes
- [ ] Verify each ⚠ item in addendum §2 against `gdelt-bq.gdeltv2`:
      - [ ] `TABLES.table_type` value set actually present
      - [ ] `COLUMNS.column_default` exists and its format
      - [ ] `COLUMN_FIELD_PATHS.description` carries column comments; `field_path` shape for
            nested `STRUCT`s
      - [ ] `TABLE_OPTIONS` `description` quoting
      - [ ] `TABLE_CONSTRAINTS` — expected to be **empty** for `gdeltv2`; confirm the query
            succeeds rather than erroring on a dataset with no constraints
- [ ] Record which `gdeltv2` tables carry `RECORD`/`REPEATED` columns
- [ ] Measure bytes billed for the full metadata sweep (`dry_run=True` on each query)
- [ ] Save raw result-set rows to `tests/fixtures/bigquery/*.json` — the tuples the mock cursor
      will replay in Slice 2. **Recorded from the real catalog, not invented**
**Exit:** the addendum §2 table is corrected against reality, fixtures are committed,
`rsa_bq_it` exists, and the go/no-go is called.

### 0a — `rsa_bq_it`, the integration-test dataset (PLAN §8 Q3)

Provisioned unconditionally: it is the `RSA_BIGQUERY_DSN` target, and the only dataset where B2
(declared PK/FK read correctly) can be tested at all — `gdelt-bq` declares no constraints. It is
the canonical shop schema from `tests/_conformance.py`, so the same assertions that run against
Postgres and MySQL run here. Kilobytes; comfortably inside the free tier.

```sql
CREATE SCHEMA IF NOT EXISTS `PROJECT.rsa_bq_it` OPTIONS(location = 'US');

CREATE OR REPLACE TABLE `PROJECT.rsa_bq_it.users` (
  id         INT64  NOT NULL,
  email      STRING NOT NULL OPTIONS(description = 'contact email'),
  status     STRING DEFAULT 'active',
  created_at TIMESTAMP,
  PRIMARY KEY (id) NOT ENFORCED          -- ⚠ inline PK confirmed supported; FK inline is not
) OPTIONS(description = 'people');

CREATE OR REPLACE TABLE `PROJECT.rsa_bq_it.orders` (
  id      INT64 NOT NULL,
  user_id INT64 NOT NULL,
  total   NUMERIC,
  PRIMARY KEY (id) NOT ENFORCED
);

-- FK via ALTER, the documented path (inline REFERENCES may work; do not depend on it)
ALTER TABLE `PROJECT.rsa_bq_it.orders`
  ADD CONSTRAINT orders_user_fk FOREIGN KEY (user_id)
  REFERENCES `PROJECT.rsa_bq_it.users`(id) NOT ENFORCED;

CREATE OR REPLACE VIEW `PROJECT.rsa_bq_it.active_users` AS
  SELECT id FROM `PROJECT.rsa_bq_it.users`;
```

No `UNIQUE` on `email` — BigQuery has none, which is why the `UNIQUE` capability is not claimed
(addendum §2). The shop schema is otherwise identical to `_PG_DDL` in
`tests/integration/conftest.py`, deliberately: a dialect that needs a *different* fixture to pass is
a dialect whose differences we have not understood.

- [ ] Create the dataset and run the DDL; confirm `TABLE_CONSTRAINTS` returns both constraints and
      that `enforced` is `NO`
- [ ] Record the result-set rows as a second fixture — this one exercises the constraint path that
      `gdeltv2` cannot

### 0b — `gdelt_fallback`, only if `gdelt-bq` is unreachable

Contingency, not default work. Load the POC's existing `data/events.jsonl` / `data/gkg.jsonl`
(already on disk, 168k + 86k rows) with `bq load --source_format=NEWLINE_DELIMITED_JSON --autodetect`,
then point the demo at `bigquery://PROJECT/gdelt_fallback`. Note in the runbook if this path is used:
autodetected types will differ from `gdelt-bq`'s curated schema, so the extracted ontology is
*similar*, not identical.

---

## Slice 1 — Connector core

**File:** `relational_schema_analyzer/connectors/bigquery.py`

Structurally a sibling of `databricks_source.py` — same shape, same module-level helpers, so the
`fk_inference` sampler can reuse the URL parser exactly as `DatabricksValueSampler` does.

```python
_parse_bigquery_url(url) -> dict            # data_project, dataset, billing_project, location, credentials_path
_load_bigquery() -> Any                     # lazy import + pip-install hint on ImportError
_safe_identifier(name, kind) -> str         # reuse the Databricks guard

class BigQueryConnector:
    def __init__(self, connection_string, schema_name="default") -> None
    def get_schema(self) -> Schema
```

**Use the native `google.cloud.bigquery.Client`, not the DB-API wrapper.** The Databricks
connector uses a cursor because that is all its driver offers; here the job object carries
`total_bytes_processed` and accepts `dry_run` / `maximum_bytes_billed`, which Slice 6 depends on.
Keep a thin `_rows(sql) -> list[tuple]` seam so the tests can inject a fake client with the same
ergonomics as the mock-cursor tests.

Queries (dataset-qualified; routing to the dataset's region is automatic):

| # | Query | Populates |
| --- | --- | --- |
| 1 | `TABLES`: `table_name, table_type` | table set, `is_view` |
| 2 | `TABLE_OPTIONS WHERE option_name = 'description'` | `Table.comment` (unquote `option_value`) |
| 3 | `COLUMNS`: `table_name, column_name, data_type, is_nullable, column_default, ordinal_position, is_partitioning_column, clustering_ordinal_position, is_hidden, is_system_defined` | columns; skip hidden/system |
| 4 | `COLUMN_FIELD_PATHS`: `table_name, column_name, field_path, data_type, description` | `Column.comment` where `field_path = column_name`; all rows → `extra["fieldPaths"]` |
| 5 | `TABLE_CONSTRAINTS` + `KEY_COLUMN_USAGE` + `CONSTRAINT_COLUMN_USAGE` | PK, FK (always `enforced=False`) |

Notes that will otherwise be rediscovered painfully:

- **Constraint resolution differs from Databricks.** Unity Catalog resolves an FK's target through
  `referential_constraints.unique_constraint_name`; BigQuery's `CONSTRAINT_COLUMN_USAGE` names the
  *referenced* table/column directly. Simpler — do not port the indirection.
- **Every optional read degrades, never fails.** Wrap queries 2, 4, and 5 the way
  `_columns_by_table` wraps `full_data_type` (`databricks_source.py:188-191`): on error, log and
  continue with the field unpopulated. A dataset with no constraints, or a principal without
  metadata permission on one view, must still produce a schema.
- **Missing billing project → a clear `ValueError` at construction**, per addendum D1.
- `SourceProvenance(dialect="bigquery", server_version=None, database=<data_project>,
  namespace=<dataset>)`.
- No `open_session()` (addendum D5).

**Registration:** `base.py` `SUPPORTED_SOURCE_TYPES` + factory branch; `pyproject.toml` extra;
`typemap.py` additions (`int64`, `float64`, `numeric`, `bignumeric`, `bool`, `string`, `bytes`,
`date`, `datetime`, `time`, `timestamp`, `geography`, `json`, `interval`, `struct`, `record`,
`range`) with `normalized_type_category` returning `array` for `ARRAY<...>` and `json` for
`STRUCT<...>`.

**Exit:** `create_connector("bigquery", url)` builds; `get_schema()` returns a populated
`PhysicalSchema` against the live dataset.

---

## Slice 2 — Tests

**File:** `tests/test_bigquery_connector.py`, patterned on `test_databricks_connector.py`.

- [ ] `TestParseUrl` — full URL, defaults, missing billing project rejected, wrong scheme rejected,
      `$VAR` expansion
- [ ] Fake client replaying the **Slice 0 fixtures**, dispatching on query text
- [ ] `conf.assert_shop_conformance(schema, dialect="bigquery", capabilities={ORDINAL, DEFAULTS,
      COMMENTS, FOREIGN_KEYS, VIEWS})` — **no `UNIQUE`, no `PROVENANCE_VERSION`** (addendum §2).
      The harness already gates on these flags; no harness change needed
- [ ] Nested/repeated: `ARRAY<STRUCT<...>>` stays one column, `type_category == "array"`,
      `extra["fieldPaths"]` populated, survives a snapshot JSON round-trip (B3)
- [ ] Degradation: constraint queries raising → schema still returns, no PK/FK
- [ ] Partition/cluster metadata lands in `extra`, and `Table.is_partitioned` is **untouched**
- [ ] Live opt-in integration in `tests/integration/`, gated on `RUN_INTEGRATION=1` +
      `RSA_BIGQUERY_DSN`, against `rsa_bq_it` with its declared constraints (B2)
- [ ] **`create_value_sampler("bigquery", ...) is None`** — the test that enforces PLAN §8 Q2. Until
      Slice 6 lands, BigQuery inference is name-only and no default path can issue a data-scanning
      query. Asserting it means the cost-safety property is verified, not remembered; when Slice 6
      arrives, this test is *replaced*, and replacing it is the moment to re-read the cost governor

**Exit:** always-on tests green with no network or credentials.

---

## Slice 3 — Surfaces

Docs and strings, batched so nothing is half-registered.

- [ ] `cli.py:39` help; `tool.py:15` contract description
- [ ] `README.md:139` source list, plus a short **cost note**: metadata-only by default, sampling
      opt-in — this is the one thing a new BigQuery user must be told
- [ ] `DESIGN.md:94` mermaid; §9.3 "Update" paragraph naming BigQuery as the third
      warehouse-class source and pointing at this addendum
- [ ] `docs/IMPLEMENTATION-PLAN.md` testing matrix: new row — *Live opt-in (DSN) · BigQuery ·
      recorded-fixture mock tests always-on, live via `RSA_BIGQUERY_DSN`*
- [ ] AOE: three description strings + one dropdown entry
      (`{ id: "bigquery", label: "BigQuery", urlHint: "bigquery://project/dataset" }`)

---

## Slice 4 — Declared-key overlay

**File:** `relational_schema_analyzer/overlay.py` (new). Generic, not BigQuery-specific
(addendum D4) — every constraint-poor source needs it.

Confirmed in scope by PLAN §8 Q1. **Needs no BigQuery access**, so it is the slice to run while the
M0 credentials gate is open.

```python
def apply_key_overlay(schema: Schema, overlay: dict) -> Schema:
    """Merge human-declared PKs / FKs / uniques onto a PhysicalSchema.

    Additive and non-destructive: a declared constraint always wins; the overlay
    only fills gaps. Unknown tables/columns are an error, not a silent no-op —
    a typo'd overlay that quietly does nothing is worse than one that fails.
    """
```

Format (JSON or YAML, mirroring the `physicalMapping` vocabulary):

```json
{
  "version": 1,
  "tables": {
    "events":        { "primaryKey": ["GLOBALEVENTID"] },
    "gkg":           { "primaryKey": ["GKGRECORDID"] },
    "eventmentions": {
      "foreignKeys": [
        { "columns": ["GLOBALEVENTID"],     "references": { "table": "events", "columns": ["GLOBALEVENTID"] } },
        { "columns": ["MentionIdentifier"], "references": { "table": "gkg",    "columns": ["DocumentIdentifier"] } }
      ]
    }
  }
}
```

- [x] `apply_key_overlay` + validation (unknown table/column/key → `OverlayError` naming it)
- [x] Overlay FKs carry `enforced=False` and `constraint_name` prefixed `overlay:`
- [x] Provenance: `Table.extra["overlay"]` marker → `metadata.detectedPatterns` gains
      `overlay_declared_keys` plus an assumptions line, so a bundle reader can tell
      catalog-declared from human-declared from inferred (B5)
- [x] `cli.py --overlay FILE` on `snapshot` / `analyze` / `owl` / `r2rml`, applied immediately
      after `get_schema()` / `--from-snapshot` load
- [x] Export from `__init__.py`; tests incl. round-trip and the typo case
- [x] Documented as a **general** capability (README + `DESIGN.md` §9.3.1), not a BigQuery appendix
      — the Q1 consequence

**Landed 2026-08-14** — `overlay.py` + `tests/test_overlay.py` (29 tests) + 3 CLI tests;
suite 490 passed / 3 skipped, ruff + mypy clean. Decisions made during implementation, beyond the sketch:

- **Case-insensitive name resolution.** Dialects disagree about case and an overlay is written
  by a human reading a codebook, not a catalog dump. Folds only while unambiguous; two names
  differing solely by case are left to exact match rather than silently picked between.
- **Unknown *keys* rejected, not just unknown tables/columns.** `"primarykey"` for
  `"primaryKey"` was the exact failure the strictness rule exists to catch — it would
  otherwise apply nothing and surface as an empty ontology. `comment` / `description` are
  permitted everywhere, since recording *why* a key was asserted is most of the artifact's value.
- **Not review-flagged.** The overlay adds its pattern and an assumptions line but does not set
  `reviewRequired`: a human reviewed these by writing them down, which is strictly more than an
  inferred FK can claim. Inference keeps its review flag.
- **FK cardinality hint computed after merge**, so a 1:1 is detected when the overlay itself
  supplied the uniqueness it depends on.

**Why here and not in a script:** the overlay is the human knowledge the catalog lacks. Written
down, versioned, and provenance-tagged it is an artifact; hand-edited into a JSON dump it is a
liability. It also serves Glue/Hive/Iceberg later at zero extra cost.

---

## Slice 5 — GDELT example set (the demo artifacts)

**Directory:** `examples/gdelt/`

- [ ] `README.md` — runbook: exact commands, credentials needed, **measured bytes and cost** for
      the full metadata sweep
- [ ] `physical.json` — captured snapshot of `gdelt-bq.gdeltv2`
- [ ] `keys.overlay.json` — the declared keys, each with a one-line comment on its evidence
      (`eventmentions.MentionIdentifier → gkg.DocumentIdentifier` is the join the POC's own
      `bigquery_pull.py` header documents as the GKG-2.0 replacement for `CAMEOEVENTIDS`)
- [ ] `bundle.json`, `ontology.ttl`, `mapping.ttl` — regenerated from `physical.json + overlay`
- [ ] `DIFF.md` — **the demo narrative.** Hand-designed graph (`gdelt-market-impact/docs/schema.md`)
      versus the extracted ontology, difference by difference, each traced to a missing source
      signal: `Actor` / `Theme` / `Location` / `Source` are delimited substrings inside `events` /
      `gkg`, not tables; `MENTIONS` is a denormalized column, not an FK; the inferred edges
      (`CORRELATES_WITH` / `AFFECTS`) are analytics no introspector can produce
- [ ] `tests/test_golden_bigquery.py` — regenerates bundle + TTL from the committed snapshot,
      offline, no credentials (B6), matching the `test_golden_csv.py` pattern

**Exit:** the D6 segment runs from committed artifacts even with no network.

---

## Slice 6 — Cost governor + value sampler *(post-demo)*

**File:** `fk_inference.py`, beside `DatabricksValueSampler` (~line 1134).

Deferred past the demo by PLAN §8 Q2. Landing it means **replacing the Slice 2 guard test** that
asserts `create_value_sampler("bigquery", ...) is None` — treat that replacement as the checkpoint
for re-reading everything below, because it is the commit where BigQuery gains the ability to spend
money.

```python
class BigQueryCostGuard:
    """Dry-run estimate → decide → run. The estimate is free; the query is not."""
    def estimate_bytes(self, sql: str) -> int | None
    def run_if_affordable(self, sql: str) -> Any | None

class BigQueryValueSampler:
    def __init__(self, connection_string, *, schema_name="", limit=10_000,
                 max_bytes_per_probe=1_000_000_000, max_bytes_per_session=10_000_000_000)
```

- [ ] Dry-run gate on every probe; over ceiling → `None` (the documented "not evaluated" value)
- [ ] `maximum_bytes_billed` on every real job
- [ ] `TABLESAMPLE SYSTEM (n PERCENT)` in place of `LIMIT` for base tables — **`LIMIT` does not
      reduce bytes scanned**, which is the single most important fact in this slice
- [ ] Partition-filter injection when `extra["isPartitioningColumn"]` names one
- [ ] **Per-`(table, column)` distinct-value cache** — turns O(candidate pairs) scans into
      O(columns). Worth doing here and worth back-porting to the other samplers later
- [ ] Session budget; on exhaustion degrade to name-only inference and log
- [ ] `create_value_sampler` branch (`fk_inference.py:1528` pattern)
- [ ] Tests with a fake client asserting **no job runs without a passing dry run**, and that budget
      exhaustion degrades rather than raises (B7)
- [ ] A `ValueEnumerator` for `discriminator.py` behind the same guard — BigQuery has no CHECK
      constraints, so sampling is the only channel there (addendum §2)
- [ ] **`samplers.py` is a second, separate spend path.** `executor_from_connection` adapts a
      DB-API connection and both `make_value_enumerator` and `make_specialization_counter` issue
      their own SQL through it — bypassing `BigQueryValueSampler` entirely. Either adapt
      `google.cloud.bigquery.dbapi` and accept that those queries are ungoverned, or give
      BigQuery an executor that routes through the cost guard. **The latter**, or the guarantee
      is only as good as the path someone happens to use

---

## Slice 7 — r2g port *(post-demo, separate repo)*

Recorded for completeness; not RSA work. r2g keeps its own registry
(`r2g/src/r2g/connectors/base.py:29`), so it needs its own `connectors/bigquery.py` plus a
`SourceSession` (`count_rows` / `stream_rows` / `dump_table_to_csv`) over the **BigQuery Storage
Read API** (`google-cloud-bigquery-storage`) or an `EXPORT DATA` → GCS staging step for
archive-scale reads. This is what unlocks the `gdelt-market-impact` backfit gated in
[`PLAN-bigquery.md`](PLAN-bigquery.md) §6.

---

## Sequencing and effort

| Slice | Effort | Needs GCP? | Blocks |
| --- | --- | --- | --- |
| 0 Recon + `rsa_bq_it` | 3–4 h | **yes** | 1, 2, 5 |
| 1 Connector | 4–6 h | yes (to validate) | 2, 5 |
| 2 Tests | 3–4 h | no (fixtures) | merge |
| 3 Surfaces | 1–2 h | no | AOE demo |
| 4 Overlay | 3–4 h | **no** | 5 |
| 5 Examples + diff | 3–4 h | yes | demo |
| 6 Sampler | 6–8 h | yes | post-demo |
| 7 r2g port | separate | yes | backfit |

Critical path to Aug 20: **0 → 1 → 2 → 4 → 5 → 3**, ~3–4 focused days.

**If the M0 credentials gate is still open**, reorder to **4 → 0 → 1 → 2 → 5 → 3**. Slice 4 is the
only critical-path slice that touches no cloud, so spending the blocked time there costs nothing and
shortens the tail.

## Definition of done (per slice)

Every slice: `pytest` green, `ruff` clean, `mypy` clean, docstrings carrying the *why* in the house
style, and no new always-on dependency (the `[bigquery]` extra stays optional and lazily imported).
