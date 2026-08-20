# Design addendum — BigQuery as a source

**Status:** ACCEPTED 2026-08-12 (D1–D5 below; the four delivery questions resolved in
[`PLAN-bigquery.md`](PLAN-bigquery.md) §8). Companion to `DESIGN.md` §9.3 (connector parity) and
§9.3.1 (source-scope guardrail). Delivery sequencing lives in
[`PLAN-bigquery.md`](PLAN-bigquery.md); the slice-by-slice engineering breakdown in
[`IMPLEMENTATION-PLAN-bigquery.md`](IMPLEMENTATION-PLAN-bigquery.md).

---

## 0. Driver, and why it lands here rather than in the POC

`gdelt-market-impact` (a customer POC) decided on 2026-08-11 **not** to refactor its ingest onto
BigQuery before its Aug 20 milestone. The reason recorded in its PRD §9 is specific rather than
conservative: GKG 2.0 in BigQuery drops `CAMEOEVENTIDS`, the join that carries themes/tone onto
Events and theme-tags 76.6% of the pulled corpus; re-implementing it through
`gdeltv2.eventmentions` means re-validating the theme vocabulary, re-pulling, and re-deriving
every downstream edge to arrive at the graph already on disk.

What that PRD *did* commit to is **D6 / G8**: point RSA / r2g / AOE at `gdelt-bq.gdeltv2`
**directly as a relational source**, side by side with the hand-built graph — *"here is the
ontology we designed by hand; here is what RSA extracts from the raw source; here is the diff."*

Three consequences for this library:

1. BigQuery becomes an RSA deliverable **with an external date** (Aug 20), not a someday-source.
2. The POC and the connector are **decoupled on purpose**. Nothing in the POC's pipeline depends
   on this work landing, so a slip costs a demo slide, not a milestone.
3. Building it here preserves the **backfit option**: once r2g can ingest from BigQuery, the POC
   can migrate its structural half (tables → collections, FK → edges) onto r2g and keep only its
   bespoke inferred edges (`SIMILAR_TO` / `CORRELATES_WITH` / `AFFECTS`). That is the POC's own
   stated architecture stance, and it needs an RSA connector underneath it.

---

## 1. Scope test against §9.3.1

The deciding lens the guardrail sets: *does the source yield a faithful, constraint-bearing
`PhysicalSchema`* — tables → typed columns → PK/unique/CHECK → FKs → provenance?

**BigQuery passes, and passes more cleanly than Databricks did.** It is a tabular warehouse with
a real ANSI-shaped `INFORMATION_SCHEMA`, GA (unenforced) primary- and foreign-key constraints,
per-table and per-column descriptions, and a three-level `project.dataset.table` namespace that
is structurally identical to Unity Catalog's `catalog.schema.table`. No model change is required
and no new connector-protocol capability is needed. This is the fourth confirmation that the
plugin design in §9.3 holds.

It is worth being explicit that BigQuery is **not** the lakehouse-catalog case (§9.3.1 items 3–4,
Glue / Hive / Iceberg). Those have no constraint vocabulary at all. BigQuery has one — GDELT just
does not use it, which is a different problem and is addressed by **D4** below.

---

## 2. What the catalog gives, and what it withholds

Verified against Google's `INFORMATION_SCHEMA` reference (2026-08-11). Items marked ⚠ are to be
re-verified against the live catalog in Slice 0 before the connector depends on them.

| RSA model field | BigQuery source | Notes |
| --- | --- | --- |
| `Table.name`, `Table.is_view` | `INFORMATION_SCHEMA.TABLES.table_type` | vocabulary is `BASE TABLE`, `VIEW`, `MATERIALIZED VIEW`, `EXTERNAL`, `CLONE`, `SNAPSHOT`, ⚠ verify the exact set present. Only the two view kinds are query-defined; `EXTERNAL` / `CLONE` / `SNAPSHOT` are ordinary tables — the same call the Databricks connector makes. |
| `Table.comment` | `INFORMATION_SCHEMA.TABLE_OPTIONS` where `option_name = 'description'` | **not** a column on `TABLES`; `option_value` is a quoted string literal and needs unquoting. |
| `Table.schema_name` | dataset | see **D1**. |
| `Column.name` / `data_type` / `is_nullable` / `ordinal` | `INFORMATION_SCHEMA.COLUMNS` | `ordinal_position`, `is_nullable`, `data_type`. Filter out `is_hidden` / `is_system_defined` pseudo-columns (`_PARTITIONTIME`, `_PARTITIONDATE`). |
| `Column.default` | `COLUMNS.column_default` | ⚠ verify presence/format; degrade to `None` rather than fail, as the Databricks connector does for `full_data_type`. |
| `Column.comment` | `INFORMATION_SCHEMA.COLUMN_FIELD_PATHS.description` | ⚠ column descriptions are **not** on `COLUMNS`. `COLUMN_FIELD_PATHS` emits one row per top-level column *and* per nested `STRUCT` field, so filter to `field_path = column_name` for the top-level comment. |
| `Table.primary_key`, `Table.foreign_keys` | `TABLE_CONSTRAINTS` + `KEY_COLUMN_USAGE` + `CONSTRAINT_COLUMN_USAGE` | `constraint_type` is only ever `PRIMARY KEY` or `FOREIGN KEY`. |
| `ForeignKey.enforced` | — | **always `False`.** BigQuery's `enforced` column can hold `YES`/`NO` but *only `NO` is supported* — the constraint is an optimizer hint and nothing validates referenced rows. Identical to the Unity Catalog precedent (commit `08be3fb`, "unenforced FK is evidence, not proof"), so it is a dialect-level fact, not a per-constraint read. |
| `Table.unique_constraints` | — | **absent.** BigQuery has no `UNIQUE` constraint. Single-column uniqueness survives only via a single-column PK. |
| `Table.check_constraints` | — | **absent.** No `CHECK`. Consequence: `discriminator.py`'s *declared* channel (`CheckConstraint.enum_values`) can never fire on BigQuery; discriminator detection falls back to the **sampled** channel, which is where **D3** bites. |
| `Table.indexes` | — | no user indexes; the physical analogues are partitioning and clustering (**D2**). |
| `SourceProvenance` | `@@project_id` / job metadata | dialect `bigquery`; there is no `SELECT version()` — use the resolved data project + dataset + location. `server_version` is `None`; the conformance `PROVENANCE_VERSION` capability is therefore **not** claimed. |

**Resulting conformance capability set:** `ORDINAL`, `COMMENTS`, `FOREIGN_KEYS`, `VIEWS`,
and `DEFAULTS` (⚠ pending the `column_default` check) — **without** `UNIQUE` and
`PROVENANCE_VERSION`. The harness in `tests/_conformance.py` already gates on exactly these
flags, so no harness change is needed.

---

## 3. Decisions

### D1 — Namespace: project = catalog, dataset = `schema_name`

Mirrors the Databricks connector's handling of `catalog.schema.table`. The wrinkle BigQuery adds
is that **the project that owns the data and the project that pays for the query are different
things** — the whole point of a public dataset such as `gdelt-bq`, which nobody but Google can
bill against.

Proposed connection-string grammar (SQLAlchemy-ish, consistent with `sqlalchemy-bigquery` and
with our own `databricks://` form):

```
bigquery://<data_project>/<dataset>?billing_project=<my-project>&location=US&credentials_path=/path/sa.json
```

- `data_project` / `dataset` → `catalog` / `schema_name`; for the demo, `bigquery://gdelt-bq/gdeltv2`.
- `billing_project` falls back to `$GOOGLE_CLOUD_PROJECT`. **Missing billing project is a
  hard error with an actionable message**, not a silent default — an unbilled query fails deep in
  the driver with an opaque permission error.
- Credentials resolve through Application Default Credentials (`$GOOGLE_APPLICATION_CREDENTIALS`
  or `gcloud auth application-default login`); `credentials_path` is an explicit override.
- `location` is optional (dataset-level `INFORMATION_SCHEMA` routing is automatic); it exists for
  the case where a job must be pinned to a region.
- `expand_env_vars` applies as it does to every other source, so the catalog never stores secrets.

**Why a URL rather than a params dict:** every RSA surface (CLI `--url`, tool contract,
MCP, AOE's overlay, r2g's catalog) is string-typed today. A dict would need five plumbing changes
for no gain.

### D2 — Nested / repeated columns stay one column

BigQuery has `STRUCT`/`RECORD` and `ARRAY`/`REPEATED`. Three options were considered:

1. **Flatten** each leaf field into its own `Column`. **Rejected** — it invents columns that do
   not exist in the SQL surface. R2RML `rr:logicalTable` and the AOE contract both assume a
   column name that can be selected; `V2Locations.field.lat` cannot. It also silently changes
   cardinality for `REPEATED` fields.
2. **Drop** the structure and keep the top-level column only. Rejected as lossy — the nesting is
   exactly the signal a consumer needs to decide embed-vs-link.
3. **Keep the top-level column authoritative, record the shape in `Column.extra`.** ✅ Adopted.

`data_type` holds the full type as BigQuery reports it (`ARRAY<STRUCT<lat FLOAT64, ...>>`),
`type_category` normalizes to `array` / `json`, and `Column.extra["fieldPaths"]` carries the
`COLUMN_FIELD_PATHS` rows for that column. This uses the v0.2.0 consumer-metadata passthrough
exactly as designed — the analyzer never interprets `extra`, it only guarantees the round-trip.

Partitioning and clustering land the same way: `Column.extra["isPartitioningColumn"]` and
`Column.extra["clusteringOrdinal"]`. **`Table.is_partitioned` is deliberately not reused** — that
field means PostgreSQL declarative partitioning, where child tables are separate relations that
r2g rolls up. BigQuery partitioning is intra-table and has no child relations; overloading the
field would make r2g's rollup logic wrong.

`typemap.py` gains the BigQuery base names it does not already cover: `int64`, `float64`,
`numeric`, `bignumeric`, `bool`, `string`, `bytes`, `date`, `datetime`, `time`, `timestamp`,
`geography`, `json`, `interval`, `struct`, `record`, `range`.

### D3 — Cost is a correctness concern, not an optimization

**This is the one genuinely new thing BigQuery brings to RSA.** Every source we support today
bills by *time*: a badly-shaped sampling query is slow, and slow is recoverable. BigQuery bills by
*bytes scanned*, and — the part that catches people — **`LIMIT` does not reduce bytes scanned.**
`SELECT DISTINCT col FROM t LIMIT 10000` reads the entire column. Against `gdelt-bq.gdeltv2.events`
that is a full-column scan of a table measured in hundreds of GB, per probe.

Every value-touching path in RSA is affected:

| Path | Shape on BigQuery |
| --- | --- |
| `fk_inference._apply_sampler` value-overlap | two full-column scans **per candidate pair**; candidates are combinatorial in column count |
| `fk_inference` denormalization probes (`distinct_ratio`, `group_single_valued`) | one full scan per probe |
| `discriminator.py` sampled `ValueEnumerator` | one full column scan per candidate column — and it is the *only* channel available, since BigQuery has no CHECK constraints |
| `taxonomy.SpecializationCounter` | a join across parent + subtype tables per cluster |

**The safe-by-default position, verified in the code:** `baseline.py:325` calls
`infer_foreign_keys(schema)` with **no sampler**, and nothing in `cli.py` / `analyzer.py` /
`tool.py` constructs one. So `snapshot`, `analyze`, `owl`, and `r2rml` over BigQuery issue
**only `INFORMATION_SCHEMA` queries** — metadata reads billed at a small per-query minimum
(10 MB at time of writing, ⚠ confirm by dry run). A full snapshot is a handful of them. That
property must be **preserved and documented**, not discovered later.

When sampling *is* opted into (r2g's path today, and any future RSA caller), `BigQueryValueSampler`
carries a cost governor that no other sampler needs:

- **Dry-run first.** Every probe is compiled with `dry_run=True`, and `total_bytes_processed` is
  checked against a per-probe ceiling. Over budget → return `None` (the documented "not evaluated"
  answer) rather than run. A dry run is free.
- **`maximum_bytes_billed`** on every real job, so a mis-estimate fails the job instead of the
  invoice.
- **`TABLESAMPLE SYSTEM (n PERCENT)`** instead of `LIMIT` where the target is a base table, since
  it genuinely reduces bytes read.
- **Partition-filter injection** when `extra["isPartitioningColumn"]` identifies one; on a
  partitioned table this is the single largest reduction available.
- **Per-column distinct-value cache.** The sampler protocol is
  `(local_table, local_column, foreign_table, foreign_column) -> float | None`, which invites one
  scan *per pair*. Caching the bounded distinct set per `(table, column)` turns an O(pairs)
  scan count into O(columns). This is a win on every engine and a cost difference of one to two
  orders of magnitude here.
- **A session budget.** Total estimated bytes across all probes is capped; on exhaustion the
  sampler degrades to `None` and logs, so inference falls back to name-only rather than failing.

Defaults: sampling **off**; when enabled, per-probe ceiling and session budget both required
(no unbounded default).

**Sequencing (PLAN §8 Q2, decided).** The governor and sampler land *after* the Aug 20 demo. Until
they do, `create_value_sampler("bigquery", ...)` returns `None` — asserted by a test, so the
metadata-only property above is enforced rather than trusted.

### D4 — A constraint-free dataset needs a key seed

GDELT is the motivating case, and it exposes a real gap rather than a BigQuery quirk.

`fk_inference` builds its target index from **declared primary keys** — `_build_pk_index`
(`fk_inference.py:212-215`) skips any table with no `primary_key`, and `_candidate_tables_for_prefix`
(`:340`) only matches tables that have one. The public `gdelt-bq.gdeltv2` tables declare no PKs
and no FKs. Therefore:

> Pointed at `gdeltv2` as-is, RSA produces **N isolated entities and zero relationships** — a
> correct result, and a poor demo.

Worse, name-based inference could not recover the interesting join even with PKs present:
`eventmentions.GLOBALEVENTID → events.GLOBALEVENTID` is an exact-name match and would be found,
but `eventmentions.MentionIdentifier → gkg.DocumentIdentifier` shares no name structure at all
and never will be.

Three ways out:

| Option | Verdict |
| --- | --- |
| (a) Hand-edit the snapshot JSON between `snapshot` and `analyze --from-snapshot` | Works **today, zero code**. Unprincipled, unrepeatable, invisible in provenance. Keep as the Aug-20 fallback. |
| (b) **A declared-key overlay** — a small JSON/YAML file of PKs, FKs and uniques merged onto a `PhysicalSchema`, recorded in provenance as `declared_by: overlay` | ✅ **Adopted** (PLAN §8 Q1). ~80 lines plus tests. Generic: every constraint-poor source (Glue, Hive, Iceberg — §9.3.1 items 3–4) hits this same wall, so it is not BigQuery-specific work, and it is documented as a general capability rather than a BigQuery appendix. It is also the honest artifact — the human knowledge that the catalog lacks, written down and version-controlled. |
| (c) **Infer PKs by sampling** uniqueness (`APPROX_COUNT_DISTINCT(col) = COUNT(*)`) | Deferred. Genuinely useful for lakehouse sources, but it is new inference (not a connector), it costs money on exactly the tables where it matters, and false positives silently corrupt the conceptual model. Track separately. |

The overlay's provenance must be explicit: an overlay-supplied FK is **not** a declared FK. It
carries `enforced=False` and should surface in `metadata.detectedPatterns` so a reader can tell
which relationships came from the catalog, which from inference, and which from a human.

### D5 — Introspection only; the bulk-read seam stays open

RSA does not load data (DESIGN §1). The BigQuery connector implements `get_schema()`;
`open_session()` is **not** implemented, matching the DuckDB and Databricks precedent, and
r2g's own registry already "omits RSA's analysis-only `duckdb` / `databricks` sources"
(`r2g/src/r2g/connectors/base.py:29`).

The backfit path is therefore a **separate, later port in r2g**: its own
`connectors/bigquery.py` plus a `SourceSession` implementation over the BigQuery Storage Read API
(`google-cloud-bigquery-storage`) or an `EXPORT DATA` → GCS staging step, which is the right shape
for the full 30-year archive the POC's PRD contemplates. Recording it here so the boundary is not
rediscovered mid-demo: **RSA shipping BigQuery does not by itself let r2g ingest GDELT.**

---

## 4. Consumer impact

| Consumer | What it needs | Size |
| --- | --- | --- |
| **`arango-ontoextract`** | `source_type` is a free-form string handed to `create_connector`, so BigQuery works the moment RSA ships it and the extra is installed. Edits are cosmetic: the description at `relational_schema_extraction.py:61`, the two MCP tool docstrings (`mcp/tools/relational.py:44,87`), and one dropdown entry beside `RelationalExtractionOverlay.tsx:52`. | ~4 lines |
| **`r2g`** | Keeps its own registry and concrete connectors. Introspection could be delegated to RSA's connector; **ingest needs the new session** (D5). | port, post-demo |
| **`gdelt-market-impact`** | Consumes the demo artifacts (snapshot / bundle / TTL / R2RML) for the D6 narrative. **No pipeline change** — that is the point of the decoupling. | 0 |

---

## 5. GDELT as the acceptance corpus

`gdelt-bq.gdeltv2` is a good acceptance corpus precisely because it is *hostile* in ways our
existing fixtures are not: no constraints, wide denormalized tables, `RECORD`/`REPEATED` columns
in some tables, delimited multi-value strings (`V2Themes`) that look scalar to any catalog, and a
size where a careless query costs real money. Every one of those exercises a decision above.

**What the demo will claim:** RSA introspects a live BigQuery dataset faithfully, produces a
conceptual bundle + OWL + R2RML with no hand-written mapping, and the diff against the
hand-designed `docs/schema.md` graph is explainable — each difference traces to a specific missing
signal in the source.

**What it will not claim** (mirroring the POC PRD's own §7.1 discipline):

- **Not** that the extracted ontology matches the hand-designed one. It will be flatter and
  denormalized. `Actor`, `Theme`, `Location`, `Source` are *rows and delimited substrings* inside
  `events` / `gkg`, not tables — no introspector can recover them, and RSA is explicitly not in
  the business of guessing. The gap **is** the story: it is the value a modeller adds.
- **Not** that the relationships are discovered. With the overlay, they are *declared by a human
  and verified by the tool*. Say so on the slide.
- **Not** a cost or performance benchmark of BigQuery.

---

## 6. Non-goals

- No data loading, staging, or `EXPORT DATA` (unchanged from §1; see D5).
- No BigQuery ML models, routines, stored procedures, or `INFORMATION_SCHEMA.JOBS` cost analytics.
- No DDL parsing (§1 non-goal), including the `TABLES.ddl` column — tempting, and still out.
- No flattening of nested columns into synthetic tables (D2).
- No PK inference (D4 option c) in this increment.

---

## 7. Acceptance

| ID | Criterion | Verified by |
| --- | --- | --- |
| B1 | `create_connector("bigquery", url).get_schema()` returns a faithful `PhysicalSchema` at the D2 capability set | mock-cursor tests replaying recorded `INFORMATION_SCHEMA` result sets; `tests/_conformance.py` assertions |
| B2 | Declared PK/FK are read; every FK carries `enforced=False` | conformance against **`rsa_bq_it`** (PLAN §8 Q3) — a user-owned dataset that declares constraints, since `gdelt-bq` declares none |
| B3 | Nested/repeated columns survive as one column with `extra["fieldPaths"]` populated, and round-trip through snapshot JSON | physical-model test |
| B4 | `snapshot` / `analyze` / `owl` / `r2rml` against a live dataset issue **only** `INFORMATION_SCHEMA` queries, with measured bytes recorded in the demo runbook | live opt-in test gated on `RSA_BIGQUERY_DSN`; dry-run byte assertion |
| B5 | Overlay-supplied keys produce relationships distinguishable in provenance from declared and inferred ones | overlay unit test + golden bundle |
| B6 | A committed GDELT example set (snapshot, overlay, bundle, `ontology.ttl`, `mapping.ttl`) regenerates byte-identically offline from the recorded snapshot | golden test, no network |
| B7 | With sampling enabled, no probe runs without a dry-run estimate under the ceiling, and the session budget halts probing | sampler unit tests with a fake client |
