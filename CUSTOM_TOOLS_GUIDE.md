# CodeBadger Custom Tools Guide

Add your own vulnerability detectors and code-quality checks by:

1. Writing a Scala query template in `src/tools/queries/`.
2. Adding a Python tool function in `src/tools/custom_tools.py`.
3. Restarting the server.

The server auto-registers everything in `custom_tools.py` on start.

---

## Project Structure

```
src/tools/
├── custom_tools.py          ← Python tool definitions (edit this)
├── mcp_tools.py             ← Auto-loads custom_tools.py
└── queries/
    ├── command_injection_sinks.scala   ← Example query (bundled)
    ├── taint_flows_auto.scala          ← Built-in queries …
    └── your_query_name.scala           ← Your new query goes here
```

---

## Step 1 — Write a Query Template

Create `src/tools/queries/your_query_name.scala`.

Every query file is a Scala block.  Template variables use `{{double_braces}}`
and are substituted at runtime by `QueryLoader.load()`.  The result must be
wrapped in `<codebadger_result>` tags so the parser extracts it cleanly.

### Standard template

```scala
{
  import io.shiftleft.codepropertygraph.generated.nodes._
  import io.shiftleft.semanticcpg.language._
  import scala.collection.mutable

  val myPattern  = "{{my_pattern}}"    // string variable — keep the quotes
  val maxResults = {{max_results}}     // numeric variable — no quotes

  val output = new StringBuilder()

  output.append("My Analysis\n")
  output.append("=" * 60 + "\n\n")

  val results = cpg.call
    .name(myPattern)
    .take(maxResults)
    .l

  if (results.isEmpty) {
    output.append("Nothing found.\n")
  } else {
    results.zipWithIndex.foreach { case (c, idx) =>
      output.append(s"--- Result ${idx + 1} ---\n")
      output.append(s"Location: ${c.location.filename}:${c.location.lineNumber.getOrElse(-1)}\n")
      output.append(s"Code:     ${c.code}\n\n")
    }
    output.append(s"Total: ${results.size}\n")
  }

  "<codebadger_result>\n" + output.toString() + "</codebadger_result>"
}
```

### Template variable rules

| Variable kind | Scala declaration | Python call |
|---|---|---|
| String | `val x = "{{x}}"` | `QueryLoader.load("q", x="value")` |
| Integer | `val n = {{n}}` | `QueryLoader.load("q", n=50)` |
| Long (node ID) | `val id = {{id}}L` | `QueryLoader.load("q", id=12345)` |

User-supplied values are sanitised against template injection before
substitution — `{{` inside a value is escaped automatically.

### File filtering (path-boundary anchored)

Copy this helper whenever you need to filter by filename:

```scala
def pathBoundaryRegex(f: String): String = {
  val escaped = java.util.regex.Pattern.quote(f)
  "(^|.*/)" + escaped + "$"
}

val filtered = if (fileFilter.nonEmpty) {
  val pattern = pathBoundaryRegex(fileFilter)
  cpg.call.filter(_.file.name.headOption.exists(_.matches(pattern)))
} else {
  cpg.call
}
```

This anchors the match to a path boundary so `"parser.c"` matches
`/src/parser.c` but not `/src/myparser.c`.

---

## Step 2 — Write the Python Tool

Add your tool inside `register_custom_tools()` in `src/tools/custom_tools.py`.

### Full annotated example

```python
@mcp.tool(
    description="""One-line summary shown in client listings.

Longer explanation of what the tool finds and why it matters.

Args:
    codebase_hash: Hash returned by generate_cpg.
    my_param:      What this parameter controls (default "value").
    max_results:   Upper bound on returned findings (default 50).

Returns:
    Text report with findings, locations, and a suggested next step.

Notes:
    - Any caveats or false-positive warnings.
    - Suggested follow-up tool calls.

Examples:
    my_tool(codebase_hash="abc123")
    my_tool(codebase_hash="abc123", my_param="custom")
""",
    tags={"security", "CWE-NNN", "my-category"},
)
def my_tool(
    codebase_hash: Annotated[str, Field(description="Codebase hash from generate_cpg")],
    my_param: Annotated[str, Field(description="Controls the detection pattern")] = "default",
    max_results: Annotated[int, Field(description="Max findings", ge=1, le=500)] = 50,
) -> str:
    try:
        info = _get_codebase(services, codebase_hash)

        query = QueryLoader.load(
            "your_query_name",       # matches src/tools/queries/your_query_name.scala
            my_pattern=my_param,
            max_results=max_results,
        )

        return _run_query(
            services, codebase_hash, info.cpg_path, query,
            timeout=60,
            tool_name="my_tool",
            cache_params={"my_param": my_param, "max_results": max_results},
        )

    except (ValueError, RuntimeError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"my_tool: {e}", exc_info=True)
        return f"Internal Error: {e}"
```

### Helper reference

**`_get_codebase(services, codebase_hash) → CodebaseInfo`**

Validates the hash and returns a `CodebaseInfo` object.
Raises `ValueError` with a user-friendly message if not found.

```python
info = _get_codebase(services, codebase_hash)
info.cpg_path    # str  — absolute path to cpg.bin on the host
info.language    # str  — "c", "python", …
info.source_path # str  — original source location
info.metadata    # dict — arbitrary metadata stored at generate time
```

**`_run_query(services, codebase_hash, cpg_path, query, *, timeout, tool_name, cache_params) → str`**

Executes the rendered CPGQL string, extracts the `<codebadger_result>` content,
and returns it as a plain string.

- `timeout` — seconds before the query is aborted.
- `tool_name` + `cache_params` — when both are provided, results are cached in
  SQLite (TTL from `config.query.cache_ttl`, default 300 s).
- Raises `RuntimeError` on failure — catch it and return `f"Error: {e}"`.

**`QueryLoader.load(query_name, **kwargs) → str`**

Loads `src/tools/queries/<query_name>.scala`, substitutes every `{{key}}`
placeholder with the matching kwarg, and returns the rendered query string.
The template file is cached in memory after the first load.

```python
query = QueryLoader.load(
    "command_injection_sinks",
    sink_pattern="system|exec",
    file_filter="main.c",
    max_results=50,
)
```

---

## The `services` Object

Every tool closes over `services`.  These are the keys you will use:

| Key | Type | Use for |
|---|---|---|
| `services["query_executor"]` | `QueryExecutor` | Running CPGQL (via `_run_query`) |
| `services["codebase_tracker"]` | `CodebaseTracker` | CPG metadata (via `_get_codebase`) |
| `services["db_manager"]` | `DBManager` | Cache reads/writes (via `_run_query`) |
| `services["config"]` | `Config` | Reading `config.yaml` values |

---

## CPGQL Quick Reference

### Core node types

| Expression | What it selects |
|---|---|
| `cpg.method` | Function/method definitions |
| `cpg.call` | Call sites |
| `cpg.literal` | Literal values (`"hello"`, `42`, …) |
| `cpg.assignment` | Assignment statements |
| `cpg.parameter` | Function parameters |
| `cpg.local` | Local variable declarations |
| `cpg.controlStructure` | `if`, `for`, `while`, `switch`, … |

### Common filters

```scala
cpg.method.name("main")                          // exact name
cpg.call.name("(?i).*exec.*")                    // regex
cpg.method.filename(".*auth.*")                  // file path regex
cpg.call.where(_.argument.order(1).isLiteral)    // first arg is literal
cpg.call.whereNot(_.argument.order(1).isLiteral) // first arg is NOT literal
cpg.method.whereNot(_.isExternal)                // defined in this codebase
```

### Taint flows

```scala
val sources = cpg.call.name("getenv|fgets|recv").argument
val sinks   = cpg.call.name("system|exec").argument.order(1)
sources.reachableByFlows(sinks).take(20).p.foreach(output.append)
```

---

## Tool Tags

Use tags so MCP clients and agents can filter and discover tools:

| Tag | Meaning |
|---|---|
| `"security"` | Any vulnerability detector |
| `"code-quality"` | Non-security hygiene checks |
| `"taint"` | Taint / data-flow analysis |
| `"memory-safety"` | Memory bugs (UAF, overflow, …) |
| `"injection"` | Injection-family bugs |
| `"CWE-NNN"` | CWE identifier for the detected weakness |
| `"attack-surface"` | Entry-point enumeration |

---

## Walkthrough: Adding a New Tool

The bundled `find_command_injection_sinks` tool is the reference implementation.
Use it as the starting point for any new detector.

### 1. Prototype in `run_cpgql_query`

Use the existing `run_cpgql_query` tool to iterate on your CPGQL before
committing it to a file.  Wrap output in `<codebadger_result>` tags and test
until the results look right.

### 2. Create the query file

`src/tools/queries/my_detector.scala`

```scala
{
  import io.shiftleft.codepropertygraph.generated.nodes._
  import io.shiftleft.semanticcpg.language._
  import scala.collection.mutable

  val myPattern  = "{{my_pattern}}"
  val fileFilter = "{{file_filter}}"
  val maxResults = {{max_results}}

  val output = new StringBuilder()

  def pathBoundaryRegex(f: String): String = {
    val escaped = java.util.regex.Pattern.quote(f)
    "(^|.*/)" + escaped + "$"
  }

  output.append("My Detector Analysis\n")
  output.append("=" * 60 + "\n\n")

  val calls = cpg.call.name(myPattern)
  val filtered = if (fileFilter.nonEmpty) {
    val re = pathBoundaryRegex(fileFilter)
    calls.filter(_.file.name.headOption.exists(_.matches(re)))
  } else { calls }

  val results = filtered.take(maxResults).l

  if (results.isEmpty) {
    output.append("No findings.\n")
  } else {
    results.zipWithIndex.foreach { case (c, i) =>
      output.append(s"--- Finding ${i + 1} ---\n")
      output.append(s"Location: ${c.location.filename}:${c.location.lineNumber.getOrElse(-1)}\n")
      output.append(s"Code:     ${c.code}\n\n")
    }
    output.append(s"Total: ${results.size}\n")
  }

  "<codebadger_result>\n" + output.toString() + "</codebadger_result>"
}
```

### 3. Add the Python tool to `custom_tools.py`

```python
@mcp.tool(
    description="""...""",
    tags={"security", "CWE-NNN"},
)
def my_detector(
    codebase_hash: Annotated[str, Field(description="Codebase hash from generate_cpg")],
    filename: Annotated[Optional[str], Field(description="Optional filename filter")] = None,
    max_results: Annotated[int, Field(description="Max results", ge=1, le=200)] = 50,
) -> str:
    try:
        info = _get_codebase(services, codebase_hash)
        query = QueryLoader.load(
            "my_detector",
            my_pattern="the_pattern",
            file_filter=filename or "",
            max_results=max_results,
        )
        return _run_query(
            services, codebase_hash, info.cpg_path, query,
            timeout=60,
            tool_name="my_detector",
            cache_params={"filename": filename or "", "max_results": max_results},
        )
    except (ValueError, RuntimeError) as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"my_detector: {e}", exc_info=True)
        return f"Internal Error: {e}"
```

### 4. Restart and call

```bash
docker compose restart codebadger
```

The tool is now available in every connected MCP client.

---

## Caching

`_run_query` caches successful results when you provide both `tool_name`
and `cache_params`.  Include every input that affects the result:

```python
cache_params = {
    "language":    lang,
    "filename":    filename or "",
    "max_results": max_results,
}
```

Omit both to always run fresh (e.g., for tools that depend on live state).

---

## Testing

Iterate on your CPGQL using `run_cpgql_query` (already registered) before
committing it to a `.scala` file.  Once the query is stable, move it to
`queries/` and wire it up as a dedicated tool.

---

## Design Decisions

**Queries live in `.scala` files, not inline Python strings.**
Separating the Scala from the Python makes each piece independently editable.
You can prototype a query in `run_cpgql_query`, paste the working version into
a `.scala` file, and never touch the Python wrapper.  The `QueryLoader` caches
templates in memory after the first load so there is no I/O overhead at query
time.

**`<codebadger_result>` wrapping, not `.toJsonPretty`.**
All built-in complex queries (format string, null pointer, taint flows, …) use
a `StringBuilder` + `<codebadger_result>` wrapper to produce readable,
multi-section narrative reports.  The `query_executor` parser extracts the
content between the tags automatically.  `.toJsonPretty` is reserved for
simple collection traversals that need raw JSON back in the caller; for
analysis tools a structured text report is more useful.

**`QueryLoader` sanitises user values against template injection.**
Any `{{` sequence inside a value passed to `QueryLoader.load()` is replaced by
a sentinel before substitution and restored afterward.  This prevents a crafted
value like `"{{sink_pattern}}"` from overwriting another template variable.

**Tools return `str`, not `Dict`.**
Built-in analysis tools (`find_taint_flows`, `find_format_string_vulns`, …)
all return `str` when the backing query produces a text report.  Custom tools
follow the same convention so that MCP clients and agents receive a consistent
type and can display results directly without unwrapping a dict.

**`_get_codebase` and `_run_query` are thin helpers, not abstractions.**
They exist only to eliminate the six-line boilerplate that every tool would
otherwise repeat: hash validation, CPG lookup, cache read, execute, cache
write.  They do not hide the underlying services — use `services[...]`
directly whenever the helpers are not a good fit.
