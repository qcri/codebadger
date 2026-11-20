# Contributing

## Development setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-test.txt -r requirements-dev.txt
docker compose up -d        # Joern container, needed for integration tests
```

See [Installation](installation.md) for prerequisites.

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

## Guidelines

- Follow existing repository conventions.
- Add tests for behavioral changes.
- Ensure all tests pass before opening a PR.
- Write a clear PR description.
- Update the relevant doc under [docs/](README.md) when behavior changes.

Adding a new detector? See [Custom Tools](custom-tools.md) - no core changes needed.
