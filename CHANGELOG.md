# Changelog

## 0.8.0-beta

The version strings were not updated for the `v0.7.0-beta` tag, so this release
also covers everything since `v0.6.2-beta` that the code still reported.

### Breaking changes

- **Tool failures are MCP errors.** Every tool reports a failed call as an MCP
  error result whose text starts with a stable code: `VALIDATION_ERROR`,
  `QUERY_ERROR`, `TIMEOUT`, `SERVER_BUSY`, `SERVER_UNAVAILABLE`,
  `INTERNAL_ERROR`, or `NOT_SUPPORTED`. Tools no longer return
  `{"success": false, ...}` for failures. Results that describe a CPG's state
  (`not_found` or `failed` status, `queue_full`, partial removal) are still
  normal results.
- **One name for result caps.** `max_results` (`find_taint_flows`,
  `find_command_injection_sinks`) and `max_nodes` (`get_cfg`) are now `limit`.
  Every `limit`, `page`, and `page_size` declares its bounds, and out-of-range
  values fail with `VALIDATION_ERROR` instead of being clamped.
- **One set of paging fields.** Capped replies use `total`, `returned`, and
  `truncated`, plus `page`, `page_size`, and `total_pages` when paged.
  Removed: `available`, `result_cap`, `has_more`, the echoed `limit`, and the
  `cpg_*` paging names in `get_backend_status`. In `list_methods`/`list_calls`,
  `truncated` is now also true when later pages remain.
- **Fixed choices are enums**, and `find_taint_flows` no longer accepts the
  deprecated `source_pattern`, `sink_pattern`, and `depth` arguments.
- **New exclusion rules.** The default `--exclude-regex` rules are per-language
  directory and test-file rules instead of ~100 word-prefix patterns. On 30
  real repositories they drop no product code (the old rules dropped 1,203
  files, including all of Maven's `src/main/java/`). Because the rules are part
  of each CPG's cache key, existing CPGs rebuild on their next `generate_cpg`.
  An explicit `cpg.exclusion_patterns` still overrides them; remove an old copied
  list from `config.yaml` to get the new defaults.
- **Larger `cpg.bin` files.** Builds persist Joern's dataflow overlay, so files
  are about 2.7× larger and cold loads about 5× faster.
  `cpg.persist_dataflow_overlay: false` turns it off.
- **MCP 2026 protocol.** FastMCP 4 is sessionless and does not answer `ping`;
  check connectivity with `list_tools`.
- `src.services` no longer exports the unused `CPGGenerator`.

### New

- `diff_cpgs`: method-level diff between two CPGs (two commits or builds).
- `diff_findings`: compare a detector's findings between two CPGs.
- `generate_cpg(commit=..., refresh=...)`: pin an exact revision or rebuild a
  moving branch's tip.
- `get_cpg_status` reports `provenance` (commit, Joern version, frontend, build
  options) and `coverage` (compilation database, scope, warnings, excluded files).
- `get_cfg`/`get_call_graph` accept `method_id`; ambiguous names list candidates.
- `list_cpg_jobs` and `cancel_cpg_job`; `generate_cpg` returns a `job_id`.
- `get_backend_status` returns full `codebase_hash` values and filters by
  `language`/`project`, paged in Postgres.
- Server instructions, tool group tags, and `CODEBADGER_DISABLED_TOOL_TAGS`.
- PHP builds (the Joern image now ships PHP), and exclusions for Go, Rust, and Swift.
- A retention sweep for expired cache rows and old finished jobs.

### Fixes

- `get_call_graph` compiles under Scala 3 again; compile errors fail queries.
- Raw queries report server-side truncation; `validate=true` blocks invalid queries.
- GitHub builds apply `include_globs`, `include_paths`, and `compile_commands`
  inside the clone.
- A compilation database from another machine no longer shrinks the graph.
- Dependencies are bounded and locked (`requirements.lock`); GitHub clones use
  GitPython 3.2.1 with its security fixes.
