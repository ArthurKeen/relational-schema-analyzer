# relational-schema-analyzer

Analyze a **relational database schema** and produce a canonical **conceptual model**
(entities / relationships / properties), a **conceptual → physical mapping** back to the
source relational schema, and **metadata** (confidence, fingerprints, patterns). Optional
exports include **OWL** (Turtle / JSON-LD) for ontology pipelines.

This library is the relational analogue of
[`arangodb-schema-analyzer`](https://pypi.org/project/arangodb-schema-analyzer/) and
emits the **same tool-contract bundle shape** so that downstream consumers
(`arango-ontoextract`, transpilers, and ETL tools such as `r2g`) can treat relational and
ArangoDB sources interchangeably.

```text
PostgreSQL / MySQL / SQL Server / Snowflake / CSV
        │
        ▼  introspect (live catalog views, not DDL parsing)
   Physical Schema  (tables, columns, PKs, FKs, types)
        │
        ▼  infer (deterministic baseline + optional LLM refinement)
   { conceptualSchema, physicalMapping, metadata }   ← canonical JSON bundle
        │
        ├──► OWL Turtle / JSON-LD       (arango-ontoextract, ontology tooling)
        ├──► relational physical view   (SQL-native query tooling, future)
        └──► consumed by r2g            (drives ArangoDB MappingConfig generation)
```

## Status

Early development. **Phases 0–3 implemented**: the physical core (connectors, types,
FK inference) is extracted from `r2g`; the deterministic conceptual baseline emits a
contract-valid `{conceptualSchema, physicalMapping, metadata}` bundle with no LLM; and
OWL (Turtle / JSON-LD) exports + a CLI are in place. Next: optional LLM refinement
(Phase 4) and ecosystem integration (Phase 5). See:

- [`docs/DESIGN.md`](docs/DESIGN.md) — architecture, data model, tool contract, OWL mapping
- [`docs/IMPLEMENTATION-PLAN.md`](docs/IMPLEMENTATION-PLAN.md) — phased delivery plan & extraction inventory

```python
from relational_schema_analyzer import (
    create_connector, RelationalSchemaAnalyzer, export_owl_turtle,
)

physical = create_connector("postgresql", url, schema_name="public").get_schema()
analysis = RelationalSchemaAnalyzer().analyze(physical)   # baseline, no LLM
bundle = analysis.to_bundle()    # {conceptualSchema, physicalMapping, metadata}
ttl = export_owl_turtle(analysis)
```

```bash
relational-schema-analyzer snapshot --source postgresql --url "$DSN" -o physical.json
relational-schema-analyzer analyze  --from-snapshot physical.json --pretty
relational-schema-analyzer owl      --from-snapshot physical.json --format turtle -o schema.ttl
```

## Why this exists

Most of the relational **introspection** layer already exists and is battle-tested inside
the `r2g` (relational-to-graph) project, but it is welded to ArangoDB ETL and cannot be
reused elsewhere. This repo extracts that core into a paradigm-neutral library and adds the
**conceptual / OWL layer** that `r2g` never had, conforming to the contract the ArangoDB
analyzer already publishes.

## License

Apache-2.0 — matching the surrounding Arango ecosystem libraries
(`arangodb-schema-analyzer`, `r2g`). See [`LICENSE`](LICENSE).
