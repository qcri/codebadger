# Tool Reliability TODO

This backlog turns the live tool audit into small, reviewable changes. Each
checked item should land as its own commit with focused regression coverage and,
where practical, a live MCP check against `libxml2` or the bundled `core`
fixture.

## Working agreement

- Keep one tool-level behavior change per commit.
- Add a regression test that fails before the fix.
- Re-run the tool through MCP, not only through a mocked service.
- Do not mix worker lifecycle, protocol, or documentation cleanup into detector
  commits.
- Record newly discovered cross-cutting problems in the general section.

## Tool fixes

- [ ] **T01 — `list_parameters` result decoding (broken).** Accept both Joern's
  method-keyed tuple JSON and legacy `_1`/`_2` tuple JSON, preserve parameter
  name/type/index, and invalidate cached empty results produced by the old
  decoder. Verify with `xmlReadMemory` in libxml2.
- [ ] **T02 — `find_use_after_free` false negatives (broken).** Detect the
  bundled `dma_detach_buffer` and `dma_shadow_refresh` cases without reporting
  `dma_remap_buffer`, whose pointer is reassigned after `free`.
- [ ] **T03 — `find_uninitialized_reads` false positives.** Exclude language
  literals, macros/constants, and type names such as `true`, `false`,
  `AF_INET`, `SOCK_STREAM`, and `NetworkContext` from variable findings.
- [ ] **T04 — `list_methods` result totals.** Separate the number of matches
  available from the caller's response cap and make truncation explicit.
- [ ] **T05 — `list_calls` result totals.** Apply the same total/truncation
  contract as `list_methods`.
- [ ] **T06 — `get_type_definition` duplicate types.** Deduplicate Joern
  `<duplicate>N` entries while retaining the canonical definition and members.
- [ ] **T07 — `find_taint_sources` precision.** Do not classify setup calls
  such as `socket`, `bind`, and `listen` as input-bearing values by default.
- [ ] **T08 — `find_taint_sinks` precision.** Separate memory-management and
  other broad operations from high-signal data sinks, with an opt-in broad mode
  if needed.
- [ ] **T09 — `find_taint_flows` result contract.** Make matched, confirmed,
  emitted, unique, and truncated counts internally consistent.
- [ ] **T10 — `get_program_slice` relevance.** Stop linking unrelated methods
  solely through common variable names; preserve useful local and explicit
  interprocedural dependencies.
- [ ] **T11 — `get_backend_status` bounded output.** Add pagination or summary
  defaults so a large historical CPG catalog does not dominate LLM context.
- [ ] **T12 — `get_cpg_status` stable status fields.** Make elapsed/deadline
  fields meaningful outside active builds and sanitize recovery failures.

## Confirmation passes for tools currently scored as working

- [ ] Add or strengthen focused live coverage for CPG generation/removal,
  call graphs, raw CPGQL, bounds checks, syntax help, CFGs, variable flow,
  double-free, null-dereference, integer-overflow, format-string, heap-overflow,
  stack-overflow, TOCTOU, and command-sink tools as their neighboring code is
  changed.

## General CodeBadger fixes — after tool fixes

- [ ] Make ordinary queries reliably reactivate sleeping/evicted CPG workers.
- [ ] Make stale-worker/container recovery idempotent and race-safe.
- [ ] Return real MCP errors for failed tool calls instead of successful
  envelopes containing error text.
- [ ] Add strict output schemas and consistent structured error envelopes.
- [ ] Add MCP server instructions plus useful tool annotations.
- [ ] Evaluate MCP resources/prompts for codebase discovery and common analysis
  workflows.
- [ ] Reconcile tool documentation and argument names with the live schemas.
- [ ] Fix clean-clone container builds that currently depend on ignored local
  configuration files.
- [ ] Add CI coverage for the clean-clone Docker and live MCP smoke paths.

