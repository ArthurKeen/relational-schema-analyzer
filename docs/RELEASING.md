# Releasing

Tagging triggers the release. `.github/workflows/release.yml` builds, validates, and
publishes to PyPI on any `v*` tag.

This exists because publishing has stalled three times on local mechanics rather than on the
code: **0.5.0** was tagged and never uploaded, **0.6.0**'s upload was abandoned after `twine`
rejected a `Metadata-Version: 2.5` wheel, and **0.7.0** sat built-and-validated on a machine
with no PyPI credentials. Meanwhile PyPI's latest remained **0.4.0** while `main` carried five
versions of work, and the README claimed a version that was never published. Tagging is the
step that reliably happens, so the tag is what publishes.

---

## One-time setup (required before the first automated release)

**1. Register the Trusted Publisher on PyPI.** This is a PyPI-side setting; nobody but the
project owner can do it, and no API token is created or stored.

Go to the project → *Manage* → *Publishing* → *Add a new pending publisher*, and enter:

| Field | Value |
| --- | --- |
| Owner | `ArthurKeen` |
| Repository name | `relational-schema-analyzer` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

**2. Create the `pypi` environment** in GitHub: *Settings* → *Environments* → *New
environment* → name it `pypi`. Optionally add yourself as a required reviewer, which turns
every publish into an explicit approval click — worth it for an irreversible action.

The mirror at `arango-solutions/relational-schema-analyzer` must **not** be registered as a
publisher. One repository publishes; the other is a mirror.

---

## Cutting a release

1. **Bump the version in all four places.** They drift easily and a mismatch fails the build:

   | File | What |
   | --- | --- |
   | `pyproject.toml` | `version = "X.Y.Z"` |
   | `relational_schema_analyzer/__init__.py` | `__version__ = "X.Y.Z"` |
   | `README.md` | the status line near the top |
   | `tests/fixtures/csv_demo_bundle.golden.json` | the embedded `"version"` — **the golden test fails without it** |

2. **Verify green.** `pytest -q`, `ruff check .`, `mypy relational_schema_analyzer`.

3. **Update the release history** in `docs/IMPLEMENTATION-PLAN.md`.

4. **Commit** as `Release X.Y.Z`, with a body grouped by feature area (see `Release 0.6.0` and
   `Release 0.7.0` for the established shape).

5. **Tag and push.** The tag must sit on the release commit:

   ```bash
   git tag -a vX.Y.Z -m "Release X.Y.Z — <one line>"
   git push origin main --follow-tags
   git push solutions main --follow-tags     # mirror; does not publish
   ```

6. **Watch the run.** `gh run watch` or the Actions tab. The workflow refuses to publish if the
   tag disagrees with `pyproject.toml`, and refuses if `twine check` fails.

---

## Publishing by hand

Only needed if the workflow is unavailable. Requires a PyPI API token; use a **project-scoped**
one, not an account-wide token.

```bash
python -m build
python -m twine check dist/*                       # never skip this
python -m twine upload dist/relational_schema_analyzer-X.Y.Z*
```

Store the token in the keyring once rather than pasting it per release, or exporting it into
shell history:

```bash
python -m keyring set https://upload.pypi.org/legacy/ __token__
```

## Notes

- **PyPI versions are immutable.** A wrong upload cannot be replaced or deleted, only yanked —
  and a yanked version still occupies its number forever. This is why the tag/version check runs
  before the publish step.
- **`hatchling` is pinned `<1.32`** in `pyproject.toml`. 1.32 emits `Metadata-Version: 2.5`,
  which current `twine` rejects. Lift the pin only when `twine check` passes with it.
- **Versions need not be contiguous.** `main` is linear, so a later tag's artifacts already
  contain everything the skipped versions would have shipped — publishing 0.7.0 after 0.4.0 is
  complete, not partial.
