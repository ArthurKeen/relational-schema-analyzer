# Your Database Already Knows What It Means

## Most of an ontology is sitting in your schema. You can extract it without an LLM — and you should.

*Draft. ~1,900 words.*

---

Someone hands you a database with 200 tables and asks a reasonable question: **what does this data mean?**

Not "what columns are there" — the catalog answers that. They mean the other thing. What are the actual *concepts* here? Which of these tables are real entities and which are plumbing? When `orders.customer_id` points at `customers.id`, what is the relationship called, and does one customer have many orders or exactly one?

That question has become urgent for a boring reason: nearly every technique for making enterprise data useful to AI — knowledge graphs, retrieval over structured data, semantic layers, agents that query your warehouse — needs a model of *meaning*, not a model of storage. A list of tables is not that model.

There are two well-trodden ways to produce one, and both are bad.

**The first is to model by hand.** Get a data architect and a domain expert in a room, go table by table, and produce an ontology. This works. It also takes months, costs a fortune, and goes stale the moment someone ships a migration.

**The second is to ask a language model.** Paste in the schema, ask for an ontology, get something back in seconds. It is fluent, well-organized, and confidently includes relationships that do not exist. The failure mode is not that it is wrong — it is that wrong and right look identical in the output, and you cannot tell which you got without doing the manual work you were trying to skip.

There is a third option that gets overlooked, and it is the one I want to argue for.

## The schema is more explicit than we give it credit for

Here is the thing about a relational schema: a great deal of it is *already a set of declarations about meaning*, made deliberately by a human being, and then almost entirely ignored by the tools that read it.

- A **primary key** is a statement about identity: these columns are what make a row *that thing* and not another one.
- A **foreign key** is a statement about a relationship, including its direction.
- A **unique constraint** on a foreign key is a statement about *cardinality* — it is the difference between one-to-one and one-to-many, declared right there in the DDL.
- A **CHECK constraint** of the form `status IN ('active', 'closed', 'pending')` is an enumeration of a concept's permitted states.
- A **table or column comment** is documentation the original designer wrote for exactly this purpose.
- Even a **junction table** — two foreign keys whose combination forms the primary key — is an unambiguous declaration of a many-to-many relationship. It is not an entity at all, though every tool that maps tables to classes one-to-one will insist that it is.

None of that requires inference. It requires *reading*. And once you commit to reading it properly, a surprising amount of the conceptual model falls out deterministically — same input, same output, every single time.

That is the premise behind a library I've been building, `relational-schema-analyzer`. It introspects a relational source and produces three things: a faithful **physical schema**, a derived **conceptual model** (classes, relationships, properties, class hierarchies), and a **mapping** that ties every concept back to the exact table, column, or constraint it came from. It then exports the result as W3C standards — OWL for the ontology, R2RML for the mapping.

The whole baseline runs with **no LLM in the loop**.

## What it actually looks like

Let me use a deliberately small example — a library: `authors`, `books`, `members`, `loans`. I'll use the CSV version, because CSV is the worst case: no primary keys, no foreign keys, no constraints, no comments. Just headers and rows.

```
$ relational-schema-analyzer analyze --source csv --url ./library
```

Out come four classes — `Authors`, `Books`, `Loans`, `Members` — and three relationships:

```
Books → Authors    1:N
Loans → Books      1:N
Loans → Members    1:N
```

That is the right answer. But the interesting part is not the answer, it's what the tool says about it:

```json
"confidence": 0.64,
"reviewRequired": true,
"detectedPatterns": ["inferred_foreign_keys"],
"assumptions": [
  "No foreign keys were declared; 3 relationship(s) were inferred
   from naming heuristics (review)."
]
```

It got the right answer *and told you not to trust it too much*. Each of those relationships is tagged `inferred` with a confidence of 0.85, derived from column naming and type compatibility — and because nothing was declared, the overall confidence drops to 0.64 and the whole bundle is flagged for human review.

Run the same tool against the same schema in PostgreSQL with the foreign keys actually declared, and the relationships come back **not** flagged, at high confidence, because now the database itself is the witness.

**This is the feature.** Not the extraction — the calibration. A tool that produces an ontology should tell you which parts it read, which parts it guessed, and how sure it is about the guesses.

## Three ways to know something

That principle generalized into what I think is the most useful idea in the project. Every relationship in the output has one of three provenances, and they are never blended:

**Declared** — the source catalog asserts it. Highest trust, no inference.

**Inferred** — the tool derived it from naming conventions, type compatibility, and optionally by sampling actual values to check that the join produces overlap. Scored, flagged, always reviewable.

**Overlaid** — a human wrote it down.

That third one came from a real problem. I pointed the tool at GDELT, a public dataset of global news events hosted on Google BigQuery. It's a wonderful corpus — and it declares *no* primary keys and *no* foreign keys, because public datasets rarely bother. So the extraction produced a set of completely disconnected classes. Correct, and useless.

The fix was not to make the guessing more aggressive. It was to let a human supply the missing keys in a small version-controlled file:

```json
{
  "tables": {
    "events": { "primaryKey": ["GLOBALEVENTID"] },
    "eventmentions": {
      "foreignKeys": [{
        "columns": ["GLOBALEVENTID"],
        "references": { "table": "events", "columns": ["GLOBALEVENTID"] },
        "comment": "GDELT codebook: mentions reference their event"
      }]
    }
  }
}
```

Three rules make that an asset rather than a hack. **The catalog always wins** — an overlay fills gaps, it never overrides something the database actually declared. **Overlay keys stay labelled, not laundered** — they are marked as human-asserted in the output forever, so nobody downstream mistakes them for facts the database vouched for. And **a typo fails loudly** — misspell a table or a column and you get an error, because an overlay that silently does nothing is worse than no overlay at all. You'd discover it as an empty ontology, three steps later, with no idea why.

There's a broader point here. The interesting engineering in this kind of tool is rarely the inference. It's the **bookkeeping about how much you know** — and resisting the temptation to flatten three different epistemic states into one confident-looking answer.

## Where it gets genuinely hard

Everything above assumes the schema is an honest description of the concepts. It usually isn't, and this is the part I underestimated.

**A physical schema is a record of performance decisions.** Every denormalization a DBA made for good reasons is a distortion of the conceptual model:

- A `customers` table with `company_name`, `company_address`, and `company_industry` columns is hiding a `Company` entity that was folded in to avoid a join.
- A `party` table holding both people and organizations, told apart by a `party_type` column, is two classes wearing one table as a coat.
- A `tags` column containing `"12, 45, 89"` is a many-to-many relationship that someone stuffed into a string.
- A `products` table with five rows for the same product and `effective_date` / `expiration_date` columns is mixing a thing with its history.
- A `total_lifetime_value` column is not a property of a customer at all. It's a calculation over relationships, frozen and stored.

Recovering the concepts means reversing those optimizations. Some of it is genuinely tractable: a single-table-inheritance discriminator column is detectable from value distribution; a functional dependency (`zip` determines `city` and `state`) is measurable with a bounded sampling query; a delimited multi-value column shows up as a consistent delimiter rate.

But some of it isn't. Nothing in the schema or the data distinguishes a stored aggregate from a legitimately stored number. Only the *name* hints, and names lie. That is a judgment call, and it needs a human — or, if you like, that is exactly where a language model earns its keep, as an advisor on the residue rather than the author of the whole model.

Which is the actual argument for determinism-first. **Not** that LLMs are useless here — they're genuinely good at the semantic residue: naming, spotting that `cust_ref` and `customer_id` mean the same thing, noticing that a table is really an event log. It's that you want them working on the 10% that requires judgment, grounded by the 90% you derived from evidence, instead of hallucinating over the whole thing. A deterministic baseline gives the model something true to stand on, and gives you a way to tell which parts came from where.

## Standards on the way out

One deliberate choice worth mentioning: the output is not a proprietary format.

The ontology comes out as **OWL** (Turtle or JSON-LD), with annotations linking every class and property back to its physical origin:

```turtle
:Books_Authors a owl:ObjectProperty, owl:FunctionalProperty ;
  rdfs:domain :Books ;
  rdfs:range :Authors .
:Books_Authors phys:mappingStyle "FOREIGN_KEY" .
:Books_Authors phys:tableName "books" .
:Books_Authors phys:fromColumns "author_id" .
:Books_Authors phys:toColumns "id" .
```

Every concept traces home. You can always ask "where did this come from?" and get a column name.

The mapping comes out as **R2RML**, the W3C standard for relational-to-RDF mappings. Feed the ontology and the mapping to an off-the-shelf engine like Ontop or Morph-KGC and you have a queryable knowledge graph over your live database — with no hand-written mapping file, which is normally where weeks of this work go.

The library currently reads PostgreSQL, MySQL, SQL Server, Snowflake, Databricks, DuckDB, CSV, dbt manifests, and Open Semantic Interchange models — nine sources behind one interface, because the analysis is the same regardless of dialect. (Google BigQuery is in progress.) It's Apache-2.0, and the deterministic baseline is covered by a few hundred tests, which is only possible *because* it's deterministic. You cannot regression-test a coin flip.

## The claim

I don't think ontology extraction should be a research problem or a consulting engagement. For the majority of a well-designed schema, it's a reading problem — and the reading is mechanical, repeatable, and cheap.

The parts that genuinely require judgment deserve to be *marked* as requiring judgment, not silently absorbed into an answer that looks as confident as the rest.

Your database already knows most of what it means. It's worth building tools that listen to it before reaching for one that guesses.

---

*`relational-schema-analyzer` is open source under Apache-2.0.*
*[github.com/ArthurKeen/relational-schema-analyzer](https://github.com/ArthurKeen/relational-schema-analyzer)*
