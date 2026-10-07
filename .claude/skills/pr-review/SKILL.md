---
name: pr-review
description: Review one pull request on arango-solutions/relational-schema-analyzer the way this repository needs — verify every claim with a command (live Postgres/MySQL/SQL Server, fakesnow for Snowflake, reverting the fix to prove each test), apply the repo's own rules (output changes are fingerprint changes, converge with the arango-schema-analyzer port and r2g's re-export, stacked-PR merges, release numbering), and produce a draft review for the user to approve before anything is posted. Use when asked to review an RSA PR, check whether one can merge, or re-check after the author pushed fixes. NOT a whole-repo audit, and NOT for r2g, arango-schema-analyzer or contextual-data-fabric PRs (they have their own).
---

# PR Review

One pull request, reviewed by verification. The built-in `/code-review` reads a
diff and finds bugs; this runs RSA's checks against the branch, against real
databases where the change touches a connector, and drafts a review that says
which claims were executed and which remain read.

## Invocation

`/pr-review 8` — full review, draft only.
`/pr-review 8 recheck` — the author pushed after review: re-run each finding's
reproduction on the new head, nothing else.
`/pr-review 8 post` — post the approved draft (only after the user has seen it).

## The two rules that make it worth running

**Nothing is posted until the user has read the draft and said so.** Draft in
the reply, offer to post in one line, post verbatim when told. The repo is
**public**.

**Verify, don't opine.** Every finding names the command that produced it or
says "read, not executed". The October 2026 reviews (#4–#6) are the calibration;
each serious finding came from running something:

- **#4** (unique indexes): on live Postgres, a unique index that a foreign key
  points at was dropped. `pg_constraint.conindid` is set on FK constraints too,
  so "skip indexes a constraint uses" matched them. Separately, `indnkeyatts`
  does not exist before Postgres 11.
- **#5** (Snowflake sampler): one side of an overlap was rendered by Python
  `str()` and the other by Snowflake `TO_VARCHAR`. Booleans, large floats and
  timestamps never matched, and the zero-overlap veto dropped real FKs. A new
  auth check rejected SSO (`authenticator=externalbrowser`) URLs that parsed on
  `main`. `raise … from err` kept the unmasked secret as `__cause__`.
- **#6** (PK profiling): with a one-query budget, a table came back "no key"
  instead of "not evaluated". Snowflake `NUMBER(38,0)` ids are typed `decimal`.
  Both reproduced with fakesnow.

Re-run before you post.

## This repo's shape — read before anything

- **Two GitHub repos.** PRs live on
  **`arango-solutions/relational-schema-analyzer`**. `origin` fetches from it
  and pushes to it *and* to `ArthurKeen/relational-schema-analyzer`. Pass
  `--repo` to every `gh` call.
- **Merges are merge commits, `delete_branch_on_merge` is off.** A **stacked
  PR** whose parent merges first then merges *into the parent's branch*, not
  `main`. #6 and #7 both did this and needed #8 to reach `main`. Before calling
  a stacked PR mergeable, say who retargets it to `main` once its parent lands,
  and after merge check `git merge-base --is-ancestor <head> origin/main`.
- **No CHANGELOG.** Release notes are the version entries in
  `docs/IMPLEMENTATION-PLAN.md` plus the release commit message.
- **Releases** (`docs/RELEASING.md`): a `v*` tag push publishes, and only the
  ArthurKeen copy publishes. Version numbers are planned: BigQuery is reserved
  as **0.10.0** (`docs/PLAN-bigquery.md`). Never push a tag as part of a review.
- **Downstream.** r2g pins RSA to a band (`>=0.9.0,<0.10.0` today) and
  `r2g.fk_inference` re-exports RSA's, so a patch release reaches r2g with no PR.
  arango-schema-analyzer's `fk_inference.py` is a hand port of RSA's (divergences
  marked `ARANGO:`), so an inference-core change needs porting there.

## Method

### 0. Load the context before reading the diff

```sh
R=arango-solutions/relational-schema-analyzer
gh pr view N --repo $R \
  --json title,author,headRefName,baseRefName,headRefOid,isDraft,mergeStateStatus,files,body,reviews,comments
gh pr checks N --repo $R
git fetch origin && H=origin/<headRefName>
git log --oneline origin/main..$H && git diff origin/main...$H --stat
```

Read existing reviews first. If the base is not `main`, review **only what this
PR adds on top of its base** (`git diff origin/<base>...$H`), and say so.

### 1. Does it merge, really

```sh
W=$SCRATCH/wt-check && git worktree add -q --detach $W origin/main && cd $W \
  && (git merge -q --no-edit $H && echo clean || { git diff --name-only --diff-filter=U; git merge --abort; }) \
  ; cd - && git worktree remove --force $W
```

### 2. Run what CI runs, and more where CI is blind

The package is at the repo root (not `src/`), and the local venv is an editable
install of the main checkout. Run from the worktree with it first on the path:

```sh
PYTHONPATH=$W .venv/bin/python -m pytest -q -m "not integration"
.venv/bin/ruff check .
```

**Live databases.** CI's integration job runs only **Postgres 16 and MySQL 8**.
SQL Server is never exercised in CI, so a connector change there is unverified
until you run it. The r2g compose containers work locally:

```sh
RUN_INTEGRATION=1 \
RSA_PG_DSN="postgresql://r2g:r2g_test_2026@localhost:5432/northwind" \
RSA_MYSQL_DSN="mysql://r2g:r2g_test_2026@localhost:3306/shop" \
RSA_MSSQL_DSN="mssql://sa:r2g_Test_2026!@localhost:1433/shop" \
PYTHONPATH=$W python -m pytest -q -m integration tests/integration/<file>
```

The main `.venv` lacks `pymssql`. Make a scratch venv with
`pip install -e "$W[postgres,mysql,sqlserver]" pytest`. Create throwaway tables
or schemas, and drop them afterwards (check with a count query).

**Snowflake: fakesnow only.** Never run a review against a real Snowflake
account. fakesnow is DuckDB underneath, so it hides differences in how
Snowflake renders values. Construct a case that would differ (booleans,
`1e16`), and say that live Snowflake was not run.

### 3. Verify every claim that can be verified

| The PR says | Run |
|---|---|
| "fixes X" / any new test | **revert the fix** (`git show origin/main:<file> > <file>`), run the test, and confirm it fails; then restore |
| connector query change | run it live against each dialect it touches. Think about which catalog rows the filter also matches (FK `conindid`), and which server versions lack a column |
| "masks credentials" | check `str(exc)` **and** `traceback.format_exception(exc)`. The `__cause__` chain prints in full |
| auth / URL parsing change | parse a URL of every supported sign-in method on `main` and on the head, and diff the results (password, key-pair, `authenticator=`) |
| "governed" / "budget" | make the probe fail (bad login, unreadable table) and count the attempts and the budget spent |
| "not evaluated vs none" | exhaust the budget mid-table and check the table lands in `not_evaluated` |
| output-shape change (new field, new key) | does it change `fingerprint_physical_schema`? Column/table `extra` is hashed |
| "same fix as r2g / ASA" | diff the sibling's copy (`../r2g`, `../arango-schema-analyzer`) |

### 4. The repository's own rules

- **An output change is a fingerprint change.** The schema fingerprint hashes
  everything except bitemporal fields. Under the bitemporal rule, a fingerprint
  change is what makes a catalog timestamp trusted as `valid_from`. So a
  release that shifts every fingerprint once makes old catalog dates look like
  real schema changes. Flag it. It needs a minor version (r2g moves its band on
  purpose), never a patch.
- **Converge, don't fork.** A change to FK inference's core (candidates,
  scoring, sampler fold, `InferenceOptions`) needs porting to
  arango-schema-analyzer, and it reaches r2g through the re-export. Say which.
- **"Couldn't check" never reads as "no".** A declined probe, a timeout or a
  truncated search must stay distinguishable from a negative result.
- **Stacked PRs** (above): the review names the retarget step.
- **Public repo.** No credentials, account state or customer names in code,
  tests, fixtures or the review.

### 5. `recheck` — after the author pushes

For each finding, re-run its reproduction on the new head and report pass/fail.
If the head merged its base in, re-run the base's tests too.

## Output

Draft in the reply, for the user. Say who it is written for in one line, then:

1. **Verdict in one sentence**, and why.
2. **Verified**: what was run (unit, live per dialect, fakesnow) and passed.
3. **Must fix before merge**, numbered, each with its reproduction.
4. **Should fix** and **Minor**.
5. **Answers to the author's open questions**.
6. **Decisions for the user**, kept separate (version number, an output change
   r2g must adopt, a deferred fix with a cost).

Plain language. The user usually wants **CAR form** per finding (Context:
what happens now; Action: the change; Result: what becomes true). Post with
`gh pr review N --repo $R --comment --body-file -`. The user is usually the
author, and GitHub refuses `--request-changes` on your own PR.

## Known gotchas

- zsh: `"${ref}:path"`, never `$ref:path` (`:r`, `:t` are modifiers).
- `git fetch origin <short-sha>` fails. Fetch the branch.
- fakesnow's `INFORMATION_SCHEMA` has `NUMERIC_SCALE`, but the connector does
  not read it today.
- `gh pr checks --watch` can exit early. Loop on pending/queued/in_progress.
- PyPI's JSON API lags uploads. Check with a fresh `pip install --no-cache-dir`.
- `git stash` is shared across worktrees. Use a WIP commit.
