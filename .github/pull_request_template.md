<!--
Title: type(component): 6-9 word description
Example: fix(cpg): stop excluding bin and target directories everywhere
The title becomes the squash-merge commit subject. See docs/contributing.md.
-->

## What and why

<!-- The behavior change or the repair, and the problem it solves. Link issues: Fixes #123 -->

## How it was tested

- [ ] Focused tests: `docker run --rm -v "$PWD:/workspace" -w /workspace codebadger-mcp:latest pytest -q <tests>`
- [ ] Live MCP call through `http://127.0.0.1:4242/mcp` (or why it was not possible)

## Checklist

- [ ] One behavior change or one tool repair
- [ ] Regression test added or updated
- [ ] Docs under `docs/` updated if behavior changed
- [ ] No `config.yaml`, logs, playground CPGs, or database state committed
