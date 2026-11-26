# Contributing

## Development setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-test.txt -r requirements-dev.txt
docker compose up -d        # Joern container, needed for integration tests
git config core.hooksPath .githooks   # check commit messages locally
```

See [Installation](installation.md) for prerequisites.

## Dependencies

`requirements.txt` (runtime) and `requirements-test.txt` (tests) declare the
supported version ranges. `requirements.lock` pins the exact versions the MCP
image installs, including transitive packages. After changing a requirement,
or to pick up new patch releases, regenerate the lock and rebuild:

```bash
scripts/update_lock.sh
docker compose build codebadger-mcp
```

## Running tests

```bash
pytest tests/ -q --ignore=tests/integration
```

For Postgres and integration tests, start Compose and create a test database once.

```bash
docker compose exec -T codebadger-postgres createdb -U codebadger codebadger_test
export CODEBADGER_TEST_PG_DSN=postgresql://codebadger:codebadger@localhost:55432/codebadger_test
```

Each store and durable queue test creates and drops its own schema. The test role
needs `CREATE` on the database. The integration suite starts an in-process MCP
server and uses `DATABASE_URL` for its catalog and workers.

```bash
pytest -q tests/test_postgres_db_manager.py tests/test_postgres_job_store.py tests/test_durable_queue.py
DATABASE_URL="$CODEBADGER_TEST_PG_DSN" pytest -q tests/integration
```

## Workflow

1. Branch from `main`: `git switch -c fix/short-name`.
2. Keep one behavior change or one tool repair per commit, with a regression test.
3. Run the focused tests in Docker:
   `docker run --rm -v "$PWD:/workspace" -w /workspace codebadger-mcp:latest pytest -q <tests>`.
4. Open a pull request. Its title follows the commit format below, and CI checks
   the title and every commit in it.
5. Pull requests are squash-merged, so the PR title becomes the commit on `main`.

## Commits and pull requests

Commit subjects and PR titles use `type(component): description`:

```
fix(cpg): stop excluding bin and target directories everywhere
feat(taint): report sources that control the sink
```

- **description**: 6-9 words, imperative, starts lowercase, no trailing period.
- **type**: `feat` (new behavior), `fix` (bug fix), `perf`, `refactor`, `test`,
  `docs`, `build`, `ci`, `chore`, `revert`. Add `!` before the colon for a
  breaking change: `feat(tools)!: ...`.
- **component**:

| Component | Covers |
|-----------|--------|
| `cpg` | CPG generation, frontends, build scoping, exclusions, include detection |
| `query` | Joern query execution, CPGQL templates and validation |
| `taint` | Taint analysis tools and flows |
| `slice` | Program slicing |
| `tools` | MCP tool surface, code browsing, presentation |
| `detectors` | Vulnerability detectors and custom tools |
| `cache` | Tool-result and CPG caching, cleanup |
| `db` | Postgres catalog, job store, schema |
| `workers` | Joern worker pool, Redis coordination, ports, reaping |
| `server` | MCP server entry point, health, startup, telemetry, logging |
| `config` | Configuration files and defaults |
| `docker` | Images, Compose, deployment |
| `deps` | Dependencies and the lockfile |
| `ci` | GitHub workflows |
| `docs` | README and `docs/` |
| `tests` | Test infrastructure and fixtures |
| `release` | Version bumps and release notes |
| `repo` | Repository tooling, hooks, templates |

Keep the body optional and short. Do not add AI co-author or "Generated with"
trailers. Check a message by hand with
`python3 scripts/check_commit_msg.py --title "fix(cpg): ..."`; the list of
types and components lives in that script.

## Guidelines

- Follow existing repository conventions.
- Add tests for behavioral changes.
- Ensure all tests pass before opening a PR.
- Fill in the PR template: what changed, why, and how it was tested.
- Update the relevant doc under [docs/](README.md) when behavior changes.
- Do not commit `config.yaml`, logs, playground CPGs, or database state.

Adding a new detector? See [Custom Tools](custom-tools.md) - no core changes needed.
