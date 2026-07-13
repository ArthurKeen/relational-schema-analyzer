# Tool contract v1 (relational variant)

These schemas define the wire shape of the analyzer's JSON bundle:

```json
{ "conceptualSchema": {...}, "physicalMapping": {...}, "metadata": {...} }
```

## Provenance & compatibility

`response.schema.json` and `request.schema.json` are **copied from**
[`arango-schema-mapper/docs/tool-contract/v1/`](https://github.com/ArthurKeen/arango-schema-mapper)
(the `arangodb-schema-analyzer` contract). This is deliberate — per `docs/DESIGN.md`
§9.2, we **copy the v1 schema now and converge to a shared contract package later** rather
than block on extracting one.

`conceptualSchema` and `metadata` are **identical in shape** to the ArangoDB analyzer, which
is what lets a single downstream consumer handle both relational and ArangoDB sources.

## Relational `physicalMapping` variant

The only intended divergence is in `physicalMapping` **style enums and back-reference
fields**, which carry relational rather than ArangoDB semantics:

| Element | ArangoDB analyzer | This (relational) analyzer |
| --- | --- | --- |
| Entity `style` | `COLLECTION` / `LABEL` | `TABLE` |
| Relationship `style` | `DEDICATED_COLLECTION` / `GENERIC_WITH_TYPE` | `FOREIGN_KEY` / `JOIN_TABLE` |
| Entity back-refs | `collectionName` | `tableName`, `schemaName`, `primaryKey` |
| Relationship back-refs | `edgeCollectionName` | `fromTable`/`fromColumns`/`toTable`/`toColumns`, `joinTable`/`joinFromColumns`/`joinToColumns`/`attributeColumns` |

> **Status:** the relational fields above are in place, and emitted bundles are validated
> against `response.schema.json` in the test suite (`tests/test_golden_csv.py::…::
> test_validates_against_contract`; success criterion S2). The schema is intentionally a
> **superset** — it retains inherited Arango-only blocks (sharding, tenant scope, graphRag
> roles, vertex-centric indexes) so the same contract shape is shared with
> `arangodb-schema-analyzer` — rather than a pruned relational-only schema. Consumers should
> read the relational back-refs and ignore the Arango-only blocks (which the relational
> analyzer never populates).
