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

- [x] **T01 — `list_parameters` result decoding (broken).** Accept both Joern's
  method-keyed tuple JSON and legacy `_1`/`_2` tuple JSON, preserve parameter
  name/type/index, and invalidate cached empty results produced by the old
  decoder. Verify with `xmlReadMemory` in libxml2.
- [x] **T02 — `find_use_after_free` false negatives (broken).** Detect the
  bundled `dma_detach_buffer` and `dma_shadow_refresh` cases without reporting
  `dma_remap_buffer`, whose pointer is reassigned after `free`.
- [x] **T03 — `find_uninitialized_reads` false positives.** Exclude language
  literals, macros/constants, and type names such as `true`, `false`,
  `AF_INET`, `SOCK_STREAM`, and `NetworkContext` from variable findings.
- [x] **T04 — `list_methods` result totals.** Separate the number of matches
  available from the caller's response cap and make truncation explicit.
- [x] **T05 — `list_calls` result totals.** Apply the same total/truncation
  contract as `list_methods`.
- [x] **T06 — `get_type_definition` duplicate types.** Deduplicate Joern
  `<duplicate>N` entries while retaining the canonical definition and members.
- [x] **T07 — `find_taint_sources` precision.** Do not classify setup calls
  such as `socket`, `bind`, and `listen` as input-bearing values by default.
- [x] **T08 — `find_taint_sinks` precision.** Separate memory-management and
  other broad operations from high-signal data sinks, with an opt-in broad mode
  if needed.
- [x] **T09 — `find_taint_flows` result contract.** Make matched, confirmed,
  emitted, unique, and truncated counts internally consistent.
- [x] **T10 — `get_program_slice` relevance.** Stop linking unrelated methods
  solely through common variable names; preserve useful local and explicit
  interprocedural dependencies.
- [x] **T11 — `get_backend_status` bounded output.** Add pagination or summary
  defaults so a large historical CPG catalog does not dominate LLM context.
- [x] **T12 — `get_cpg_status` stable status fields.** Make elapsed/deadline
  fields meaningful outside active builds and sanitize recovery failures.

## Confirmation passes for tools currently scored as working

- [ ] Add or strengthen focused live coverage for CPG generation/removal,
  call graphs, raw CPGQL, bounds checks, syntax help, CFGs, variable flow,
  double-free, null-dereference, integer-overflow, format-string, heap-overflow,
  stack-overflow, TOCTOU, and command-sink tools as their neighboring code is
  changed.

## LLM and FastMCP presentation — after tool fixes

- [x] Add a root `AGENTS.md` describing architecture, local development,
  validation commands, fixture expectations, commit boundaries, and safe rules
  for working with generated CPG/runtime data. Add narrower nested guidance only
  where a subsystem genuinely needs different instructions.
- [x] Add concise FastMCP server instructions that teach an LLM the intended
  sequence: inspect capacity, generate or select a CPG, wait until ready, browse
  narrowly, then expand into flow or detector analysis.
- [x] Add tool annotations and human-readable titles, including accurate
  read-only, destructive, idempotent, and open-world hints. In particular,
  distinguish `remove_cpg(delete_files=True)` from its non-destructive mode.
- [ ] Define strict input and output schemas for every tool, including common
  pagination, truncation, status, finding, and error models.
- [ ] Replace successful envelopes containing error strings with FastMCP-native
  tool errors while retaining a stable, machine-readable error code and a safe
  recovery hint.
- [ ] Make compact structured output the default and put verbose rendered
  reports behind an explicit detail/format option so routine calls consume less
  LLM context.
- [ ] Publish useful MCP resources or resource templates for the CPG catalog,
  per-codebase metadata/status, supported languages, and CPGQL reference data.
- [ ] Add MCP prompts for common multi-step workflows such as codebase overview,
  call-path exploration, focused data-flow analysis, and detector triage.
- [ ] Standardize tool descriptions around prerequisites, bounded defaults,
  result semantics, follow-up tools, and one minimal valid example. Remove
  duplicated prose that can drift from schemas.
- [ ] Expose pagination and truncation metadata consistently so an LLM knows
  whether it saw all results and how to request the next page.
- [ ] Generate the available-tools reference from the live FastMCP schemas, or
  enforce schema/documentation parity in CI.
- [ ] Add an MCP contract test that inventories tools, validates annotations and
  schemas, invokes representative success/error paths, and snapshots only stable
  protocol fields.

## General CodeBadger fixes — after tool and presentation fixes

- [ ] Make ordinary queries reliably reactivate sleeping/evicted CPG workers.
- [ ] Make stale-worker/container recovery idempotent and race-safe.
- [ ] Reconcile tool documentation and argument names with the live schemas.
- [ ] Fix clean-clone container builds that currently depend on ignored local
  configuration files.
- [ ] Add CI coverage for the clean-clone Docker and live MCP smoke paths.
