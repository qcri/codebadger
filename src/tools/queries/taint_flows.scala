{
  import io.shiftleft.codepropertygraph.generated.nodes._
  import io.shiftleft.semanticcpg.language._
  import scala.collection.mutable
  
  val sourceFile = "{{source_file}}"
  val sourceLine = {{source_line}}
  val sinkFile = "{{sink_file}}"
  val sinkLine = {{sink_line}}
  val sourceNodeId = {{source_node_id}}L
  val sinkNodeId = {{sink_node_id}}L
  val maxResults = {{max_results}}
  
  val output = new StringBuilder()
  
  // Helper to get method name safely from any node
  def getMethodName(node: StoredNode): String = {
    node match {
      case c: Call => c.method.name
      case i: Identifier => i.method.name
      case l: Literal => l.method.name
      case m: MethodParameterIn => m.method.name
      case r: Return => r.method.name
      case b: Block => b.method.name
      case _ => "unknown"
    }
  }

  // Helper to get name safely
  def getName(node: StoredNode): String = {
     node match {
       case c: Call => c.name
       case i: Identifier => i.name
       case l: Literal => "literal" 
       case m: MethodParameterIn => m.name
       case _ => node.label
     }
  }
  
  // Helper: build path-boundary anchored regex from a filename
  // e.g., "parser.c" -> "(^|.*/)parser\\.c$" so it matches "/path/to/parser.c"
  // but NOT "/path/to/myparser.c"
  def pathBoundaryRegex(f: String): String = {
    val escaped = java.util.regex.Pattern.quote(f)
    "(^|.*/)" + escaped + "$"
  }

  // Resolve an endpoint by exact node ID, or by location. At a location, the
  // non-operator calls on the line are the endpoint (read_value(), show_value(0));
  // operator calls (`flag != 0`, `x = ...`) only when the line has no real call,
  // then identifiers/literals.
  def resolve(nodeId: Long, file: String, line: Int): List[CfgNode] = {
    if (nodeId > 0) {
      cpg.call.filter(_.id == nodeId).l ++
      cpg.identifier.filter(_.id == nodeId).l ++
      cpg.literal.filter(_.id == nodeId).l
    } else if (file.nonEmpty && line > 0) {
      val pattern = pathBoundaryRegex(file)
      val calls = cpg.call.where(_.file.name(pattern)).lineNumber(line).l
      val (operators, real) = calls.partition(_.name.startsWith("<operator>"))
      if (real.nonEmpty) real
      else if (operators.nonEmpty) operators
      else cpg.identifier.where(_.file.name(pattern)).lineNumber(line).l ++
           cpg.literal.where(_.file.name(pattern)).lineNumber(line).l
    } else List()
  }
  val sources: List[CfgNode] = resolve(sourceNodeId, sourceFile, sourceLine).sortBy(_.id)
  val sinks: List[CfgNode] = resolve(sinkNodeId, sinkFile, sinkLine).sortBy(_.id)

  def endpointInfo(n: CfgNode) = Map(
    "node_id" -> n.id.toString, "kind" -> n.label, "name" -> getName(n), "code" -> n.code.take(120),
    "filename" -> n.file.name.headOption.getOrElse("?"), "lineNumber" -> n.lineNumber.getOrElse(-1),
    "columnNumber" -> n.columnNumber.getOrElse(-1), "method" -> getMethodName(n))

  // Expand sinks: If a sink is a function call, we usually mean "flow to any argument of this call"
  // e.g. sink = memcpy(...) -> we want to catch taint flowing into 'src' argument.
  val effectiveSinks: List[CfgNode] = sinks.flatMap {
    case c: Call => c :: c.argument.l
    case other => List(other)
  }
  
  def describe(what: String, nodeId: Long, file: String, line: Int): String =
    if (nodeId > 0) s"no $what node with ID $nodeId (use find_taint_${what}s for valid node_id values)"
    else s"no $what node at $file:$line (no call, identifier or literal on that line)"

  if (sources.isEmpty || sinks.isEmpty) {
    val missing = List(
      if (sources.isEmpty) Some(describe("source", sourceNodeId, sourceFile, sourceLine)) else None,
      if (sinks.isEmpty) Some(describe("sink", sinkNodeId, sinkFile, sinkLine)) else None).flatten
    "<codebadger_result>\nENDPOINT_NOT_FOUND: " + missing.mkString("; ") + "\n</codebadger_result>"
  } else {
  // Header
  output.append("Taint Flow Analysis\n")
  output.append("=" * 60 + "\n")
  def listEndpoints(label: String, nodes: List[CfgNode]): Unit = {
    output.append(s"$label (${nodes.size} node${if (nodes.size == 1) "" else "s"} searched):\n")
    nodes.foreach { n =>
      output.append(s"  [${n.id}] ${n.code.take(60)} at ${n.file.name.headOption.getOrElse("?")}:${n.lineNumber.getOrElse(-1)}:${n.columnNumber.getOrElse(-1)}\n")
    }
  }
  listEndpoints("Source", sources)
  listEndpoints("Sink", sinks)
  output.append("\n")

  val rawFlows = {
    // Use Joern's native reachableByFlows for taint tracking; one extra flow
    // tells a complete result from a capped one.
    effectiveSinks.reachableByFlows(sources).l.take(maxResults + 1)
  }
  val flows = rawFlows.take(maxResults)
  val seen = mutable.Set[String]()
  locally {

    if (flows.isEmpty) {
      output.append("No taint flow found: the search completed between the resolved endpoints.\n")
      
      // Diagnostic: Check if they're in the same file at least
      val srcFile = sources.head.file.name.headOption.getOrElse("")
      val snkFile = sinks.head.file.name.headOption.getOrElse("")
      
      if (srcFile != snkFile) {
        output.append("Note: Source and sink are in different files.\n")
        output.append("  Inter-procedural taint tracking requires call graph connection.\n")
      }
      
      // --- Fallback: Check for Call Graph Connection ---
      // If data flow is missing, maybe they are connected via function calls?
      val sourceMethods = sources.flatMap(_.method).dedup.l
      val sinkMethods = sinks.flatMap(_.method).dedup.l
      
      if (sourceMethods.nonEmpty && sinkMethods.nonEmpty) {
        val srcM = sourceMethods.head
        val snkM = sinkMethods.head
        
        if (srcM.fullName != snkM.fullName) {
          // Check if Source Method calls Sink Method (directly or indirectly)
          // Limit depth to avoid performance hit on large graphs
          // Use .start to turn Node into Traversal, and _.call.callee to jump Method -> Method
          val path = srcM.start.repeat(_.call.callee)(_.until(_.id(snkM.id)).maxDepth(5)).l
          
          if (path.nonEmpty) {
             output.append("\nPotential Call Path Found:\n")
             output.append("  (Data flow not detected, but functions are connected in call graph)\n")
             output.append(s"  ${srcM.name} -> ... -> ${snkM.name}\n")
             output.append("  This suggests an Inter-procedural flow is possible.\n")
          }
        }
      }
    } else {
      // Deduplicate: track seen (source_file:line -> sink_file:line) pairs

      flows.foreach { flow =>
        val elements = flow.elements.l
        if (elements.nonEmpty) {
          val source = elements.head
          val sink = elements.last

          val srcFile = source.file.name.headOption.getOrElse("?")
          val srcLine = source.lineNumber.getOrElse(-1)
          val snkFile = sink.file.name.headOption.getOrElse("?")
          val snkLine = sink.lineNumber.getOrElse(-1)

          val key = s"$srcFile:$srcLine->$snkFile:$snkLine"
          if (!seen.contains(key)) {
            seen.add(key)

            output.append(s"--- Flow ${seen.size} ---\n")

            // Source (first element)
            val srcMethod = getMethodName(source)
            output.append(s"Source: ${source.code}\n")
            output.append(s"  Location: $srcFile:$srcLine in $srcMethod()\n")

            // Path elements (intermediate steps)
            if (elements.size > 2) {
              output.append(s"\nPath (${elements.size - 2} intermediate steps):\n")
              elements.slice(1, elements.size - 1).take(15).foreach { elem =>
                val elemFile = elem.file.name.headOption.getOrElse("?")
                val elemLine = elem.lineNumber.getOrElse(-1)
                val elemMethod = getMethodName(elem)
                val codeSnippet = elem.code.take(60).replaceAll("\n", " ")
                output.append(s"  [$elemFile:$elemLine] $codeSnippet\n")
                output.append(s"           in $elemMethod()\n")
              }
              if (elements.size - 2 > 15) {
                output.append(s"  ... and ${elements.size - 17} more steps\n")
              }
            }

            // Sink (last element)
            val snkMethod = getMethodName(sink)
            output.append(s"\nSink: ${sink.code}\n")
            output.append(s"  Location: $snkFile:$snkLine in $snkMethod()\n")

            output.append(s"\nPath length: ${elements.size} nodes\n\n")
          }
        }
      }

      output.append(s"Summary: ${seen.size} unique flow(s)\n")
    }
  }
  if (rawFlows.size > maxResults)
    output.append(s"TRUNCATED: stopped after $maxResults flows; raise limit for more.\n")

  val result = Map(
    "source_nodes" -> sources.map(endpointInfo),
    "sink_nodes" -> sinks.map(endpointInfo),
    "flows" -> seen.size,
    "truncated" -> (rawFlows.size > maxResults),
    "summary" -> output.toString)
  "<codebadger_result>\n" + List(result).toJson + "\n</codebadger_result>"
  }
}
