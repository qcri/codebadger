# LLM workflow guide

Use CodeBadger in this order so requests stay bounded and results remain
actionable:

1. Call `get_backend_status` before submitting builds. Respect
   `recommended_max_concurrent_builds` and page through CPG summaries only when
   needed.
2. Call `generate_cpg` once for the repository, or select an existing
   `codebase_hash`. Poll `get_cpg_status` until `status` is `ready`; stop when it
   is `failed` and use its `error_code`/`error` to recover.
3. Start with narrow browsing: `list_methods`, `list_calls`,
   `get_type_definition`, or `get_cfg` using a filename/name filter and a small
   limit. Check `truncated` and pagination fields before broadening.
4. Expand only after locating a relevant function or line. Use
   `get_program_slice`/`get_variable_flow` for local context, then taint or
   detector tools for a focused hypothesis.
5. For taint analysis, use `find_taint_sources` and `find_taint_sinks` first.
   `find_taint_sinks` is focused by default; use `broad=true` or explicit
   patterns when an exhaustive audit is intended.
6. Treat every result as bounded. Follow `total_pages`, `available`,
   `returned`, `has_more`, or `truncated` instead of assuming the first page is
   complete.

`remove_cpg(delete_files=false)` only releases the Joern worker and preserves
   the CPG. `delete_files=true` removes the CPG, copied source, and catalog
   record; use it only when permanent deletion is intended.
