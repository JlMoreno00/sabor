# Contributing to Sabor

Develop in a clone or worktree with test data. Keep each commit reviewable and
run the safety suite before deploying reviewed changes.

## Development Workflow

1. Confirm the issue, behavior, and rollback boundary before editing.
2. Create a branch in a development clone or worktree.
3. Inspect the relevant implementation and tests; do not infer contracts from
   documentation alone.
4. Add or update tests in the same work unit as the behavior they verify.
5. Run focused tests while developing, then run every required check below.
6. Review the diff for secrets, runtime artifacts, and unrelated changes.
7. Use work-unit commits and open a review before deployment.

Do not develop by editing a running container. Container changes disappear on
rebuild and bypass source review.

## Testing Expectations

Every behavior change requires a regression test. Documentation-only changes
still require the full validation suite because commands and configuration are
part of the operational contract.

Required before review:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile sabor_mcp.py nutrition/*.py scripts/*.py tests/*.py
docker compose config -q
```

Tests must not depend on live Mealie data, real API credentials, provider
availability, or a persistent database. Use `unittest.mock`, temporary
directories, and the stubs in `tests/support.py`.

For integration-sensitive changes, record separately:

- The exact focused test command and result
- The full-suite command and result
- The runtime validation performed, or why it was intentionally not performed
- The rollback boundary

Never convert a unit test into a live mutation test merely to increase
coverage.

## Work-Unit Commits

A commit should represent one complete, reversible outcome. Keep implementation,
tests, configuration, and user-facing documentation for that outcome together.
Do not split commits by file type.

Before committing, verify that:

- The commit has one clear purpose.
- The repository remains coherent if only that commit is applied.
- Tests and docs travel with the behavior they cover.
- Rollback does not remove unrelated work.
- Verification evidence is recorded.

Use Conventional Commits:

```text
feat(nutrition): add guarded profile refresh
fix(import): preserve ingredient source text
docs(operations): document sidecar rollback boundary
chore(deps): update MCP runtime dependency
```

Use `!` and a `BREAKING CHANGE:` footer for an incompatible MCP contract,
configuration contract, or persisted-data change.

## Secrets and Runtime Data

Never commit or paste into review artifacts:

- `.env` or environment-specific variants
- API tokens, passwords, cookies, bearer headers, or provider responses that
  contain credentials
- `/data/MEMORY.md`, nutrition databases, SQLite WAL/SHM files, or volume dumps
- Backups, logs, video files, or captured user recipe data

Use only obvious placeholders in examples. Before review, inspect staged files
and scan for common token, password, private-key, and bearer patterns. If a
secret enters Git history, stop and rotate it; deleting it from the latest file
is not sufficient.

## Deployment Safety

Treat your deployment checkout as a promotion target, not a sandbox.

- Do not use `git reset --hard`, `git clean`, destructive checkout commands, or
  ad hoc file replacement there.
- Do not run `docker compose down -v`; it can remove the named data volume.
- Do not run mutation tools, catalog apply, nutrition pilot, or nutrition
  backfill as validation.
- Do not overwrite `.env` or inspect it in logs, diffs, or support output.
- Back up the Sabor data volume before a schema-affecting nutrition rollout.
- Coordinate restarts because configuration is loaded at Python import time.
- Promote reviewed work units only, then verify health and logs without exposing
  request headers or environment values.

See [docs/operations.md](docs/operations.md) for the startup, health, backup,
and rollback boundaries.

## Review Checklist

- [ ] Scope and rollback boundary are explicit.
- [ ] External and persisted-data contracts were checked in code.
- [ ] New behavior has focused tests.
- [ ] Full `unittest` discovery passes.
- [ ] `py_compile` passes.
- [ ] `docker compose config -q` passes.
- [ ] Nutrition remains disabled by default unless a reviewed rollout explicitly changes it.
- [ ] Mutation tools retain explicit confirmation flags or dry-run behavior.
- [ ] No secrets, user data, databases, backups, or generated artifacts are included.
- [ ] Configuration and operational documentation match the implementation.
- [ ] Commit messages follow Conventional Commits and each commit is a work unit.
