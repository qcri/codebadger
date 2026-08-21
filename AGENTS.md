# CodeBadger agent guide

## What this repository is

CodeBadger is a FastMCP server backed by Joern Code Property Graphs. The
application entry point is `main.py`; MCP tools live under `src/tools/`, query
templates under `src/tools/queries/`, and shared orchestration/services under
`src/services/`. Postgres stores the codebase catalog and cached tool output;
Redis coordinates queues and pooled Joern workers.

## Development workflow

- Keep one behavior change or one tool repair per commit.
- Add a regression test before or alongside the fix. Prefer the smallest
  focused test file, then run the relevant MCP test through Docker.
- The standard focused command is:
  `docker run --rm -v "$PWD:/workspace" -w /workspace codebadger-mcp:latest pytest -q <tests>`.
- For runtime confidence, rebuild with `docker compose build codebadger-mcp`,
  restart that service, and invoke the tool through `http://127.0.0.1:4242/mcp`.
- Preserve unrelated working-tree changes. Use `git diff --check` before each
  commit and keep generated/runtime artifacts out of commits.

## CPG and worker rules

- Use the existing libxml2 CPG or the bundled core fixture for discovery; do
  not regenerate large CPGs unless the task specifically requires it.
- A CPG may be sleeping while its files remain on disk. Queries can reactivate
  it; after a live check, stop the temporary worker with `remove_cpg` using
  `delete_files=false`.
- Never use `remove_cpg(delete_files=true)` casually: it deletes the CPG,
  copied source, and catalog row.
- Do not commit `config.yaml`, playground CPGs, logs, Postgres data, Redis data,
  or worker/container state. Use `config.example.yaml` for shipped defaults.

## MCP/tool contracts

- Keep output bounded and expose `total`, `returned`/`available`, pagination,
  and `truncated` whenever a result can be capped.
- Preserve stable machine-readable error codes and safe recovery hints.
- Explicit caller patterns should remain authoritative; defaults may be
  narrowed for precision, but document and test an opt-in broad mode.
- Query templates must escape user strings and clamp numeric limits through
  `QueryLoader`.

## Handoff expectations

Report the commit hash, focused test result, and live MCP result (or explain a
runtime limitation). Update `docs/tool-reliability-todo.md` when a backlog
item is genuinely complete.
