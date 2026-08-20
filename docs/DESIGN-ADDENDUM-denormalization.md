# Design addendum — denormalization patterns, and where their code belongs

**Status:** PROPOSED, for review. Companion to `DESIGN.md` §4 (inference rules) and
`DESIGN-ADDENDUM-taxonomy.md` (the sibling cross-repo capability analysis, whose §1 established
the port-direction discipline this follows).

**Trigger.** An analysis of denormalization patterns that distort ontology extraction —
horizontal partitioning (single-table inheritance), vertical partitioning (pre-joined tables),
EAV, embedded semi-structured data, repeating groups / delimited strings, temporal
denormalization (SCD), and derived/aggregated columns — circulated to both RSA and
`arango-schema-analyzer` (ASA) on the premise that it may imply shared code. It does, but not
where the framing suggests, and the most important finding is about code that already exists.

---

## 1. The frame, and one correction

The analysis is right about the essential move: a physical schema is a record of the
designer's *performance* decisions, and recovering a conceptual model means reversing them.
That is precisely what `DESIGN.md` §4 does for the easy cases (FK → relationship, join table →
N:M) and what this addendum extends to the hard ones.

**The correction is one of voice.** The analysis is written in ETL terms — "the ETL must route
rows", "the ETL must pivot", "the ETL must perform shredding". RSA is explicitly **not** an ETL
(`DESIGN.md` §1: "does not load data, transform rows, or write to any target"). Three distinct
responsibilities are collapsed in that phrasing, and separating them is what decides where each
piece of code lands:

| Responsibility | Question answered | Owner |
| --- | --- | --- |
| **Detect** | "Is `zip → city, state` a functional dependency here?" | RSA / ASA (measurement + evidence) |
| **Advise** | "That looks like an embedded lookup; extracting `Address` is the remedy" | RSA / ASA (scored finding + recommendation) |
| **Remediate** | "Project those columns into a node and draw the edge" | r2g / the ETL |

This is not pedantry. It is the difference between a capability that belongs in a library two
analyzers share and one that belongs in each consumer. Detection and advice are
paradigm-neutral and reusable; remediation is target-specific (a `MappingConfig` for r2g, an
R2RML `rr:TriplesMap` for a virtual KG, an AQL migration for ASA) and is not shareable.

The analysis's tooling survey (R2RML/RML, Morph-KGC, Ontop, Apache Hop, Spark, dbt) sits almost
entirely in the **remediate** column. It is useful context for consumers and mostly out of
scope here — with one exception worth recording: RSA already emits R2RML, and R2RML **cannot
express** most of these remedies (no shredding, no pivot, no split). `r2rml_export.py:31`
already documents the analogous limit for a join table's attribute columns. RML is the
standard that lifts it. If denormalization remedies are ever to be emitted as a mapping rather
than as prose, **RML — not R2RML — is the target**, and that is a new export, not a patch.

---

## 2. State of play — the layering is inverted, and the probes are stranded

This is the finding that should drive the decision, and it was not visible from either repo alone.

**The measurement layer already exists in RSA, and nothing calls it.** Every sampler —
`PostgresValueSampler`, `MySQLValueSampler`, `SQLServerValueSampler`, `DatabricksValueSampler`,
`CsvValueSampler` — implements three probes under a header reading
`── Denormalization probes (PRD Phase 11) ──`:

- `group_single_valued(table, determinant_cols, dependent_col)` — the fraction of determinant
  groups with a single dependent value. **This is a functional-dependency measurement**, i.e.
  exactly the signal for the analysis's "vertical partitioning".
- `distinct_ratio(table, column)` — redundancy / low cardinality.
- `delimiter_rate(table, column, delimiter)` — the analysis's "repeating groups / delimited
  strings".

Fifteen method implementations across five dialects. A repo-wide search for callers returns
**nothing**. RSA measures denormalization and then discards the capability.

**The engine that consumes them lives in r2g.** `r2g/src/r2g/denorm.py` is 611 lines with 428
lines of tests, implementing five detectors (`repeating_group`, `embedded_lookup`,
`multi_valued`, `redundant_reference`, `one_to_one`), a `DenormFinding` model with confidence
and evidence, and `remediation_hint()`. r2g's PRD records Phase 11 as **implemented (11a–11c)**.
Its only imports are `r2g.types.Schema/Table` (RSA's model, subclassed), RSA's type map, and its
own `DenormSampler` Protocol.

**And r2g's own `fk_inference.py` is a 260-line shim re-exporting RSA's samplers** — it holds
zero probe implementations.

So the current arrangement is:

```
RSA   ──  probes (measurement)          ← paradigm-neutral, unused here
r2g   ──  denorm.py (interpretation)    ← paradigm-neutral, consumes RSA's probes via a Protocol
ASA   ──  neither
```

The paradigm-neutral *analysis* sits in the consumer while the library holds only the
instrument. That is upside-down relative to every other capability in this stack, and it is the
same shape of finding `DESIGN-ADDENDUM-taxonomy.md` §1 recorded when ASA was about to
re-specify FK inference from scratch.

### 2.1 Coverage, in one table

Reconciling three vocabularies, because a vocabulary mismatch across three repos is how the
same detector gets written twice. "The analysis" column uses the circulated terms.

| The analysis | Implemented name | RSA | r2g | ASA |
| --- | --- | --- | --- | --- |
| Horizontal partitioning (STI, row-typing) | discriminator / specialization | ✅ `discriminator.py` + `taxonomy.py` | — | ✅ `type_detection.py` |
| Vertical partitioning (pre-joined) | `embedded_lookup` (FD) | ⚠️ probe only | ✅ | ❌ |
| Repeating groups / delimited strings | `multi_valued`, `repeating_group` | ⚠️ probe only | ✅ | ❌ |
| *(not in the analysis)* | `redundant_reference` | ⚠️ probe only | ✅ | ❌ |
| *(not in the analysis)* | `one_to_one` over-normalization | ❌ | ✅ | ❌ |
| EAV / open schemas | — | ❌ | ❌ | ❌ |
| Embedded semi-structured (JSON/STRUCT) | — | ⚠️ recorded, never interpreted | ❌ | ❌ (native case) |
| Temporal denormalization (SCD) | — | ❌ | ❌ | ❌ |
| Derived / aggregated columns | — | ❌ (declared signal unread — §3.1) | ❌ | ❌ |

Two additions the analysis omits are worth folding into the taxonomy. `one_to_one` is
**over-normalization** — the distortion runs in the opposite direction, and a model that only
looks for denormalization will never see it. `redundant_reference` is the weaker,
non-functional cousin of the embedded lookup: co-varying columns with few distinct
combinations, where no clean determinant exists.

---

## 3. What actually decides feasibility: three tiers of detectability

The analysis presents its patterns as a flat list of comparable ETL challenges. They are not
remotely comparable **as detection problems**, and sorting them is the difference between a
buildable increment and a research project.

**Tier 1 — Structural. Decidable from the schema alone, no data access, no false-positive
budget.**
- EAV: a table of ~3–4 columns shaped `(entity_id, attribute_name, attribute_value)`, where the
  attribute column is a low-cardinality string and the value column is a wide string/variant.
  Highly recognizable; arguably the *most* detectable pattern on the list, and currently the
  largest total gap.
- Embedded semi-structured: the column's type *is* the evidence (`json`, `jsonb`, `variant`,
  `STRUCT<…>`, `ARRAY<…>`). `type_category` already carries it.
- Repeating groups: `phone1/phone2/phone3` is a name-and-type judgment (r2g's implemented
  detector uses no sampler at all).
- Derived columns — **but only the declared ones**; see §3.1.

**Tier 2 — Measurable. Needs bounded value sampling; every finding is evidence, not proof.**
- Functional dependency (vertical partitioning) — `group_single_valued`.
- Delimited multi-value columns — `delimiter_rate`.
- Redundant reference data — `distinct_ratio`.
- SCD: measurable in principle — a business-key column duplicated across rows, plus a
  date-range column pair, plus at most one row per key with a null/sentinel end date. That is
  three cheap probes, not one, and no such probe exists today.

**Tier 3 — Semantic. Not decidable by any measurement; needs a human or an LLM.**
- Whether `Total_Lifetime_Value` is an intrinsic property or a materialized aggregate. Nothing
  in the schema or the data distinguishes a stored aggregate from a legitimately-stored number;
  only the *name* hints, and names lie.
- Whether an extracted `Company` is a genuine domain concept or an accident of one query's
  shape.
- Whether a discovered temporal state deserves reification.

Tier 3 is where the LLM refinement layer (`refine.py`) earns its place, and it must stay
advisory. **Tier 1 is where the unclaimed value is**, and it is the tier RSA currently ignores
most completely.

### 3.1 The declared channel for derived columns — an unexploited free signal

The analysis treats derived/aggregated data as a judgment call. Partly true, but it misses that
**a meaningful subset is declared in the catalog and RSA reads none of it**:

| Dialect | Declared derived-column signal |
| --- | --- |
| PostgreSQL | `information_schema.columns.is_generated` / `generation_expression` |
| MySQL | `EXTRA` contains `GENERATED`, plus `GENERATION_EXPRESSION` |
| SQL Server | `sys.computed_columns.definition` |
| BigQuery | `INFORMATION_SCHEMA.COLUMNS.is_generated` |
| Snowflake | virtual columns |

A generated column is a derived attribute *stated as such by the designer* — no heuristic, no
sampling, no ambiguity. This is precisely the precedent `discriminator.py` already sets in its
docstring: prefer the declared `CHECK (col IN (…))` over sampling, because "exact, free, no
database access" beats a probe every time. The same two-channel structure applies here, and the
declared channel is missing across all five connectors.

This is the single cheapest item in this document: additive, structural, no new model concepts
(`Column.extra` or a new optional field), and it converts a Tier-3 pattern into a partially
Tier-1 one.

---

## 4. The consequence nobody will notice until it hurts: the style vocabulary is now a contract

`DESIGN-ADDENDUM-taxonomy.md` §1 recorded that ASA is adopting RSA's `physicalMapping`
relationship-style vocabulary (`FOREIGN_KEY`, `JOIN_TABLE`) rather than minting Arango-specific
names, and stated the consequence plainly: *"If RSA renames or restructures either style, that
is now a cross-repo contract change."*

Today RSA emits exactly three styles: `TABLE`, `FOREIGN_KEY`, `JOIN_TABLE`.

**Detection alone changes nothing.** A `DenormFinding` is an advisory side-channel; it needs no
new style and no contract negotiation. That is what makes detection a safe, cheap increment.

**Acting on a finding in the conceptual output changes the contract.** The moment an embedded
lookup becomes an extracted `Company` entity, or a JSON array becomes a class, or an EAV table
becomes properties on another entity, the `physicalMapping` must say *how to get back to the
source* — and none of the three existing styles can. Each remedy implies a new style, e.g.:

| Remedy | Needs a style meaning |
| --- | --- |
| Extract embedded lookup | "this entity is a projection of a column subset of table T" |
| Shred JSON / STRUCT | "this entity lives at JSON path `$.a.b[*]` inside column C" |
| Split delimited column | "instances come from splitting column C on delimiter D" |
| Pivot EAV | "this property is the row of T where `attr = 'x'`" |
| Reify temporal state | "this entity is the validity interval `[from, to)` of table T" |

Five new styles is a substantial contract expansion, it must be agreed with ASA before either
side ships it, and — critically — **ASA needs the same five for its own denormalization**
(embedded sub-documents are the Arango-native case, per the analysis's §4). This is the
strongest argument that the two repos should design the vocabulary jointly *now*, even if
neither implements remediation for months. Detection can proceed in parallel and independently;
the style vocabulary cannot.

---

## 5. Recommendation: where the code should live

**R1 — Extract `denorm.py` from r2g into RSA.** This is the main recommendation, and it is
narrower than it sounds. The engine is already paradigm-neutral (its only inputs are
`Schema`/`Table`, the type map, and an injected sampler Protocol), the probes it calls already
live in RSA, and r2g has already proven the exact migration pattern — its `fk_inference.py` is a
re-export shim over RSA's. `denorm.py` would follow it, and r2g would keep only its remediation
scaffolding (`MappingConfig` edits), which is correctly r2g's.

Note the deliberate contrast with `DESIGN-ADDENDUM-taxonomy.md` §3, which **deferred** extracting
the FK engine. The reasoning there does not carry over: that would have meant refactoring RSA's
largest, most battle-tested module (1,539 lines) in service of a consumer that did not yet
exist. This is a 611-line module *moving toward* its dependency rather than away, with two
identified consumers (RSA's own conceptual layer; ASA), and it removes a duplicated layer
boundary instead of adding one.

**R2 — Wire the stranded probes to something.** Whatever happens with R1, RSA shipping fifteen
unused probe implementations across five dialects is a defect. Either the engine arrives (R1) or
the probes should be documented as a consumer-facing API rather than looking like dead code.

**R3 — Close the Tier-1 gaps, in this order.** EAV detection (structural, largest gap,
zero-sampling), declared derived columns (§3.1, cheapest item here), then nested/JSON
interpretation — noting that the BigQuery addendum's **D2** already decided the *representation*
(`Column.extra["fieldPaths"]`, top-level column stays authoritative), so the remaining work is
interpretation, not modelling.

**R4 — Design the style vocabulary jointly with ASA before either side emits a remedy.** §4.
Detection ships independently and immediately; the vocabulary is a negotiation.

**R5 — Treat SCD as its own increment, not part of this one.** It needs three new probes,
it interacts with r2g's existing temporal graph mode (PRD Phase 5, implemented) and with
FinReflectKG's `validFrom`/`validTo` design, and reification is a Tier-3 judgment. Scoping it
in alongside five detectors is how this becomes a six-month project.

---

## 6. What is genuinely shared with ASA, and what is not

Answering the question the analysis was circulated to settle.

| Layer | Shared? | Reasoning |
| --- | --- | --- |
| **Finding model** (`kind`, columns, confidence, evidence, recommended action) | ✅ **Yes** — highest value | Both analyzers must say the same thing about the same pattern, or the conceptual model depends on which side ran — the exact failure `DESIGN-ADDENDUM-taxonomy.md` §5 already guards against for taxonomy |
| **Remedy vocabulary** (extract / embed / split / pivot / reify / merge) | ✅ **Yes** | Feeds §4's style vocabulary; must not fork |
| **Structural detectors** (EAV shape, repeating groups, JSON-typed columns) | ✅ **Mostly** | Operate on tables/columns/types; ASA's "columns" are profiled document fields, so it needs an adapter, not a rewrite |
| **FD / distinct / delimiter probes** | ❌ **No** | Dialect-specific SQL. ASA's equivalents are AQL. Shared **Protocol**, separate implementations — exactly the seam `DESIGN-ADDENDUM-taxonomy.md` §1 identified for `Sampler` |
| **Remediation** (`MappingConfig`, AQL migration, RML) | ❌ **No** | Target-specific; belongs in each consumer |

The asymmetry the analysis correctly identifies — that ASA is **schema-on-read**, so implicit
structure must be profiled before it can be mapped — means ASA's detectors are data-driven where
RSA's are catalog-driven. That affects *where the evidence comes from*, not *what a finding
says*. Which is exactly why the finding model is the piece to share and the probes are not.

---

## 7. Proposed PRD changes

1. **`DESIGN.md` §4** — add a denormalization/over-normalization row group to the inference-rule
   table, marked advisory (findings, not conceptual rewrites), pointing here.
2. **`DESIGN.md` §3.1** — record the declared derived-column signal (§3.1) as a physical-model
   enrichment, alongside the existing CHECK/unique/index enrichment.
3. **`DESIGN.md` §9** — a new decision recording R1 (engine extraction) and R4 (joint style
   vocabulary), once reviewed.
4. **`IMPLEMENTATION-PLAN.md`** — the Phase 4 line currently reads *"Deferred — denormalization
   detection (needs value sampling)"*. That is now wrong twice over: the sampling exists, and the
   engine exists in r2g. Replace it with the R1–R5 sequence.

## 8. Open questions

1. **R1 direction** — extract to RSA, or leave in r2g and have ASA port independently (the
   taxonomy §3 answer)? This addendum argues extract; the counter-argument is that r2g's
   Phase 11 is shipped and working, and moving it risks a production consumer for a
   second consumer that has not asked yet.
2. **Does ASA actually want it?** `DESIGN-ADDENDUM-taxonomy.md` §4 records that AOE consumes RSA's
   connectors while **bypassing its conceptual layer entirely**. Before extracting a module for
   ASA's benefit, confirm ASA will consume it — the same question that section says is worth
   asking AOE.
3. **Findings in the bundle?** A `DenormFinding` list is not in the tool contract today. Additive
   `metadata` key, a new top-level section, or out-of-band?
4. **EAV's conceptual output.** Pivoting EAV rows into properties changes the entity's property
   set based on *data*, not schema — the first place RSA's conceptual model would be
   data-derived rather than catalog-derived. That is a real boundary crossing and deserves an
   explicit decision, not a slide into it.
