# Plan — BigQuery support

**Status:** ACCEPTED. Written 2026-08-11; the four review questions resolved 2026-08-12 (§8).
Design rationale: [`DESIGN-ADDENDUM-bigquery.md`](DESIGN-ADDENDUM-bigquery.md).
Engineering breakdown: [`IMPLEMENTATION-PLAN-bigquery.md`](IMPLEMENTATION-PLAN-bigquery.md).

---

## 1. Objective

Ship a BigQuery source connector in RSA that (a) satisfies `gdelt-market-impact`'s **D6/G8**
platform-demo commitment on **2026-08-20**, and (b) leaves the library and its consumers in a
state where backfitting the POC's ingest onto r2g is a decision rather than a rewrite.

Two audiences with different bars:

| Audience | Bar |
| --- | --- |
| **Aug 20 demo** (customer POV) | RSA / AOE point at `gdelt-bq.gdeltv2` live and produce an ontology + R2RML mapping; the diff against the hand-designed graph is explainable. |
| **The library** | A connector held to the same conformance harness as the other eight sources, with the cost governance BigQuery uniquely requires, released as v0.7.0. |

The demo bar is the *earlier* and *narrower* one. The plan sequences accordingly: everything the
demo needs lands first, and it deliberately needs **no data-scanning query at all**.

## 2. Strategy — three commitments

**C1 — Metadata-only for the demo.** The default RSA path over BigQuery issues only
`INFORMATION_SCHEMA` queries (verified: `baseline.py:325` infers FKs with no sampler, and no CLI
or analyzer surface constructs one). The Aug 20 path therefore costs pennies and touches no table
data. Value sampling and its cost governor are **post-demo** work. This is the single biggest
de-risking move available: the deadline path has no cost exposure and no query-tuning tail.

**C2 — The interesting part of GDELT is declared, not discovered.** `gdeltv2` has no PKs or FKs,
so FK inference has nothing to anchor on (addendum D4). A **declared-key overlay** supplies them,
version-controlled and provenance-tagged. Building it as a generic RSA feature — not a GDELT
hack — is what makes this work worth doing in the library rather than in a script.

**C3 — Decouple from the POC, permanently.** Nothing in `gdelt-market-impact` changes. Its
pipeline keeps running on the HTTP-pulled corpus. If this slips, the POC loses a demo segment and
nothing else. The backfit stays a separate, explicitly-gated decision (§6).

## 3. Milestones

Dates assume work starts 2026-08-12. Everything through M3 is RSA-local except the AOE one-liners.

| ID | Date | Deliverable | Demoable at this point |
| --- | --- | --- | --- |
| **M0** | Aug 12 | **Recon + go/no-go.** GCP project with billing, ADC working (see §8 prerequisites), `gdeltv2` catalog surface verified against the addendum §2 table, `rsa_bq_it` test dataset provisioned with declared constraints (Q3), one recorded fixture of real `INFORMATION_SCHEMA` result sets, measured bytes for a full metadata sweep. | — |
| **M1** | Aug 14 | **Connector.** `connectors/bigquery.py` + registration + `[bigquery]` extra + typemap; mock-cursor tests green at the conformance capability set. | `snapshot --source bigquery --url bigquery://gdelt-bq/gdeltv2` emits a real `physical.json` |
| **M2** | Aug 16 | **Overlay + artifacts.** Declared-key overlay; `examples/gdelt/` committed (snapshot, overlay, bundle, `ontology.ttl`, `mapping.ttl`) with an offline golden test. | full `snapshot → analyze → owl → r2rml` chain over GDELT |
| **M3** | Aug 18 | **Surfaces + AOE.** CLI/tool-contract/README/DESIGN updates; AOE source registration; **demo dry run end to end**; the hand-designed-vs-extracted diff written up. | the actual D6 segment, rehearsed |
| **M4** | Aug 20 | **Demo.** | — |
| **M5** | post-demo | **Cost governor + sampler.** `BigQueryValueSampler` with dry-run gating, `maximum_bytes_billed`, TABLESAMPLE, partition-filter injection, per-column cache, session budget. Release **v0.7.0**. | value-overlap FK inference over BigQuery, safely |
| **M6** | post-demo | **r2g port** (introspection + `SourceSession` bulk read) → the backfit becomes possible. | r2g ingest from BigQuery |

**Critical path to Aug 20:** M0 → M1 → M2 → M3. Roughly 3–4 focused days of work with ~4 days of
slack. The slack is deliberate and should be spent on the *narrative* (M3's diff write-up), which
is the part the audience actually sees.

**M0 is a real gate.** If credentials, billing, or org policy block access to `gdelt-bq`, the
whole thing stops on Aug 12 with one day lost, not on Aug 19. Nothing downstream starts until a
`SELECT` against `gdeltv2.INFORMATION_SCHEMA.TABLES` returns rows. Its prerequisites are
**unmet as of 2026-08-12** and are owned outside this repo — see §8.

**The one thing that can proceed while M0 is blocked** is Slice 4, the declared-key overlay: it
operates on a `PhysicalSchema` and needs neither BigQuery nor credentials. If the gate slips, doing
Slice 4 first loses nothing.

## 4. Cross-repo sequencing

```
RSA  M1 connector ──┬──> AOE   (4 cosmetic lines; works as soon as RSA ships)  ──> M3 demo
                    │
                    ├──> RSA   M2 overlay + examples/gdelt/                    ──> M3 demo
                    │
                    └──> r2g   M6 port + SourceSession  ──> gdelt-market-impact backfit (§6)
```

| Repo | Change | Blocking? |
| --- | --- | --- |
| `relational-schema-analyzer` | connector, overlay, sampler, docs, examples | — |
| `arango-ontoextract` | 3 description strings + 1 frontend dropdown entry | blocks the AOE half of D6 only |
| `r2g` | own `connectors/bigquery.py` + session (its registry is separate — `r2g/src/r2g/connectors/base.py:29`) | blocks the backfit, **not** the demo |
| `gdelt-market-impact` | none; consumes committed artifacts | — |

## 5. Risks

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| **Runaway query cost** — an accidental full scan of `gdeltv2.events` | low (C1 removes the exposure from the demo path) | high | Sampling off by default and out of the demo path entirely; `maximum_bytes_billed` on every job once M5 lands; a GCP **billing alert + custom quota on the billing project set at M0**, before the first query |
| **Credentials / billing / org policy** block `gdelt-bq` access | medium — **prerequisites unmet as of Aug 12** (§8) | fatal to the demo | M0 gate on day 1; fallback is `gdelt_fallback`, a user-owned dataset loaded from the POC's existing JSONL (Q3), which demos the connector faithfully even if the public dataset is unreachable |
| **Catalog surface differs** from the addendum §2 table (⚠ items) | medium | low | M0 verifies before code depends on it; every optional column read is wrapped in the same try/fallback pattern the Databricks connector uses for `full_data_type` |
| **The extracted ontology is unimpressive** — flat, few relationships | **high, and expected** | medium if unhandled | Named in the addendum §5 as the story, not the failure. The overlay (C2) makes relationships real; the diff is framed as "what a modeller adds" |
| **Nested/repeated columns** in some `gdeltv2` tables break assumptions | medium | low | D2 keeps them as single columns; M0 records which tables actually have them |
| **Aug 20 slip** | low | low | The POC is decoupled (C3); the segment is additive to its milestone |
| **Scope creep into PK inference / ingest** | medium | medium | Both explicitly deferred (addendum D4c, D5). Revisit only after M5 |

## 6. The backfit decision (deliberately deferred)

Moving `gdelt-market-impact`'s ingest onto r2g + BigQuery is **not** part of this plan. Recording
the gate so it is decided on evidence rather than momentum. It should be reconsidered only when
**all** of these hold:

1. **M6 has landed** — r2g can bulk-read BigQuery, not merely introspect it (addendum D5).
2. **The `CAMEOEVENTIDS` problem is solved** — the `eventmentions` join reproduces theme-tagging
   at parity with the current 76.6% coverage, *measured*, not assumed. This is the reason the POC
   said no in the first place and it has not changed.
3. **The theme vocabulary is re-validated** against GKG 2.0 tokens.
4. **There is a reason** — full-archive scale, freshness, or removing bespoke ETL — beyond
   architectural tidiness. The current corpus is on disk and works.

Until then the connector's value to the POC is exactly what its PRD says: a *demo* of the platform
story and an option held open.

## 7. What "done" means

- [ ] `bigquery` in `SUPPORTED_SOURCE_TYPES`, documented everywhere the other eight sources are
- [ ] Acceptance criteria B1–B7 (addendum §7) pass; suite green; ruff + mypy clean
- [ ] `examples/gdelt/` regenerates offline with no network and no credentials
- [ ] The demo runbook records **measured** bytes and cost for the full metadata sweep
- [ ] v0.7.0 released with the connector, overlay, and cost-governed sampler
- [ ] `DESIGN.md` §9.3 / §9.3.1 and the architecture diagram updated to include BigQuery

## 8. Decisions (resolved 2026-08-12)

**Q1 — Overlay scope → IN SCOPE.** The generic declared-key overlay (addendum D4b) ships in this
increment as Slice 4, on the critical path ahead of the example set. A hand-edited snapshot was the
alternative; it produces the same demo and no reusable capability. Consequence: the overlay is
documented as a **general RSA feature** (README, `DESIGN.md` §9.3.1) rather than a BigQuery
appendix, because every constraint-poor source needs it.

**Q2 — Sampler timing → POST-DEMO (M5).** The cost governor and `BigQueryValueSampler` land after
Aug 20. Consequence, and the part worth enforcing rather than remembering: until Slice 6,
`create_value_sampler("bigquery", ...)` returns `None`, so BigQuery inference is name-only and the
default path cannot issue a data-scanning query. **Slice 2 asserts this with a test**, so the
property is verified rather than assumed.

**Q3 — Provision a user-owned dataset → YES, and split in two.** Writing the recommendation down
exposed that it was really two datasets with different jobs, and conflating them would have left
B2 untestable:

| Dataset | Job | Content |
| --- | --- | --- |
| `rsa_bq_it` | **integration-test target** for `RSA_BIGQUERY_DSN` | the canonical shop schema from `tests/_conformance.py`, created by DDL with **declared** `PRIMARY KEY … NOT ENFORCED` / `FOREIGN KEY … NOT ENFORCED`, descriptions, a default, and a view. The only way to test B2 honestly — `gdelt-bq` declares no constraints and never will. Kilobytes; free tier |
| `gdelt_fallback` | **demo hedge**, provisioned only if `gdelt-bq` proves unreachable | loaded from the POC's existing `data/*.jsonl`, which is already on disk |

`rsa_bq_it` is provisioned unconditionally at M0 (it is the test target regardless);
`gdelt_fallback` only on a failed access check. DDL and load recipe:
[`IMPLEMENTATION-PLAN-bigquery.md`](IMPLEMENTATION-PLAN-bigquery.md) Slice 0.

**Q4 — Release shape → a single v0.7.0 after M5.** `pyproject.toml` stays at 0.6.0 through the
demo; the demo runs from a git checkout / editable install, which needs no release. Cutting a
connector-only 0.7.0 at M3 would publish the one configuration we most want nobody to build on —
BigQuery support with the cost governor still missing.

### M0 prerequisites — owner: Arthur, blocking everything

Verified 2026-08-12 on this machine: **no GCP access exists yet.**
`GOOGLE_CLOUD_PROJECT` is empty in the POC's `.env`, `GOOGLE_APPLICATION_CREDENTIALS` still holds
the `.env.example` placeholder path, and there is no `gcloud` / `bq` CLI or
`google-cloud-bigquery` installed. Slice 0 cannot start until:

- [ ] a GCP project with **billing enabled** (required to query public datasets — `gdelt-bq` bills
      the caller, not Google)
- [ ] ADC configured, or a service-account JSON at a real path
- [ ] a **billing alert + custom query quota** on that project, set before the first query
- [ ] `pip install 'relational-schema-analyzer[bigquery]'` (plus `gcloud` CLI, optional but useful)

Slice 4 (the overlay) is generic RSA code and is **not** blocked by any of this — it can proceed in
parallel.
