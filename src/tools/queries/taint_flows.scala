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
  val heapBridge = {{heap_bridge}}
  val maxStores = 5000
  val maxReachedStores = 200
  val maxBridgedFields = 20
  val maxLoadsPerField = 200
  val maxBridgesPerStore = 3
  val maxControlConditions = 30
  
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

  // Libraries often store a value into a struct field in one API call and load it in
  // another, joined only by a caller outside the codebase, so no flow connects them.
  // When there is no direct flow, stitch source to a store of T.f and a load of T.f
  // to the sink, matching the base struct type when it is known.
  def isFieldAccess(c: Call): Boolean =
    c.name == "<operator>.indirectFieldAccess" || c.name == "<operator>.fieldAccess"
  def fieldOf(c: Call): String = c.argument.filter(_.argumentIndex == 2).code.headOption.getOrElse("")
  def baseTypeOf(c: Call): String = {
    val t = c.argument.filter(_.argumentIndex == 1).headOption match {
      case Some(i: Identifier) => i.typeFullName
      case Some(x: Call) => x.typeFullName
      case _ => ""
    }
    t.replaceAll("[*&\\[\\]]", "").replace("const ", "").replace("struct ", "").trim
  }
  def typesMatch(a: String, b: String): Boolean =
    a.isEmpty || b.isEmpty || a == "ANY" || b == "ANY" || a == b
  def loc(n: CfgNode): Map[String, Any] = Map(
    "filename" -> n.file.name.headOption.getOrElse("?"), "line" -> n.lineNumber.getOrElse(-1),
    "code" -> n.code.take(120), "method" -> getMethodName(n))

  // (assignment, field access in its target, stored value)
  lazy val stores = cpg.assignment.l.flatMap { a =>
    a.argument.filter(_.argumentIndex == 1).ast.isCall.filter(isFieldAccess).headOption
      .map(fa => (a, fa, a.argument.filter(_.argumentIndex == 2).l))
  }.take(maxStores)
  // (store, field, struct type) for the stores whose value the source reaches
  lazy val reachedStores = {
    val values = stores.flatMap(_._3).collect { case e: CfgNode => e }
    val reached = values.reachableByFlows(sources).l.take(maxReachedStores)
      .flatMap(_.elements.lastOption).map(_.id).toSet
    stores.filter(_._3.exists(v => reached.contains(v.id)))
      .map(s => (s._1, fieldOf(s._2), baseTypeOf(s._2))).distinctBy(s => (s._2, s._3)).take(maxBridgedFields)
  }
  lazy val storeTargetIds = stores.flatMap(_._1.argument.filter(_.argumentIndex == 1).ast.map(_.id)).toSet

  // Flows start at whole operands (d->val[j]), never at an inner field access, so a
  // load is lifted through its enclosing operator expressions.
  def lift(n: CfgNode): CfgNode = n.astParent match {
    case c: Call if c.name.startsWith("<operator>.") && !c.name.startsWith("<operator>.assignment") => lift(c)
    case _ => n
  }

  // (store, field, struct type, load, target). Joern reports no flow from a
  // sub-expression to the expression containing it, so a load inside a target counts.
  def bridgeTo(targets: List[CfgNode]): List[(Call, String, String, CfgNode, CfgNode)] = {
    val targetAst = targets.flatMap(_.ast.map(_.id)).toSet
    reachedStores.flatMap { case (store, field, structType) =>
      val loads = cpg.call.filter(isFieldAccess)
        .filter(c => fieldOf(c) == field && typesMatch(baseTypeOf(c), structType))
        .filterNot(c => storeTargetIds.contains(c.id)).l.take(maxLoadsPerField)
      val direct = loads.filter(l => targetAst.contains(l.id)).take(maxBridgesPerStore)
        .map(l => (l: CfgNode, targets.find(_.ast.exists(_.id == l.id)).get))
      val viaFlow = if (direct.nonEmpty || loads.isEmpty) Nil else {
        val loadOf = loads.map(l => lift(l).id -> l).toMap
        targets.reachableByFlows(loads.map(lift)).l.take(maxBridgesPerStore)
          .flatMap(p => (p.elements.headOption, p.elements.lastOption) match {
            case (Some(h: CfgNode), Some(t: CfgNode)) => List((loadOf.getOrElse(h.id, h): CfgNode, t))
            case _ => Nil
          })
      }
      (direct ++ viaFlow).map { case (load, target) => (store, field, structType, load, target) }
    }.take(maxResults)
  }

  val bridged: List[Map[String, Any]] =
    if (!heapBridge || flows.nonEmpty) Nil
    else bridgeTo(effectiveSinks).map { case (store, field, structType, load, sink) =>
      Map("field" -> field, "struct_type" -> structType, "store" -> loc(store), "load" -> loc(load),
        "sink" -> loc(sink))
    }

  // A tainted value often bounds a loop while the sink writes a constant at an index
  // advanced inside it, so no data reaches the sink. Report flows from the source into
  // the conditions that control the sink, directly or through the heap bridge.
  val controlFlows: List[Map[String, Any]] = if (flows.nonEmpty || bridged.nonEmpty) Nil else {
    val conditions = sinks.flatMap(_.controlledBy.l).collect { case c: CfgNode => c }
      .distinctBy(_.id).take(maxControlConditions)
    // A comparison's result is not tainted by its operands, so aim at the operands.
    val conditionOf = conditions.flatMap(c => c.ast.collect { case n: CfgNode => n.id -> c }.l).toMap
    val operands = conditions.flatMap(_.ast.collect { case n: CfgNode => n }.l).distinctBy(_.id)
    val direct = operands.reachableByFlows(sources).l.take(maxResults)
      .flatMap(_.elements.lastOption).collect { case n: CfgNode => conditionOf.getOrElse(n.id, n) }
      .distinctBy(_.id).map(c => Map("condition" -> loc(c), "via" -> "data"))
    if (operands.isEmpty || direct.nonEmpty || !heapBridge) direct
    else bridgeTo(operands).map { case (store, field, structType, load, operand) =>
      Map("condition" -> loc(conditionOf.getOrElse(operand.id, operand)), "via" -> "heap",
        "field" -> field, "struct_type" -> structType, "store" -> loc(store), "load" -> loc(load))
    }.distinctBy(_("condition"))
  }
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
      if (controlFlows.nonEmpty) {
        output.append(s"\nControl-dependent influence (${controlFlows.size}): the source reaches a condition\n")
        output.append("that controls the sink, so it decides whether and how often the sink runs.\n")
        controlFlows.foreach { c =>
          val condition = c("condition").asInstanceOf[Map[String, Any]]
          val via = if (c("via") == "heap") s" via heap ${c("struct_type")}.${c("field")}" else ""
          output.append(s"  [${condition("filename")}:${condition("line")}] ${condition("code")} " +
            s"in ${condition("method")}()$via\n")
        }
      }
      if (bridged.nonEmpty) {
        output.append(s"\nHeap-bridged flows (${bridged.size}): the source is stored into a struct field\n")
        output.append("and a load of the same field reaches the sink. No call path joins them here.\n")
        bridged.foreach { b =>
          val store = b("store").asInstanceOf[Map[String, Any]]
          val load = b("load").asInstanceOf[Map[String, Any]]
          output.append(s"  [heap ${b("struct_type")}.${b("field")}] store ${store("filename")}:${store("line")} " +
            s"in ${store("method")}() => load ${load("filename")}:${load("line")} in ${load("method")}() -> sink\n")
        }
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
    "heap_bridged_flows" -> bridged,
    "control_flows" -> controlFlows,
    "truncated" -> (rawFlows.size > maxResults),
    "summary" -> output.toString)
  "<codebadger_result>\n" + List(result).toJson + "\n</codebadger_result>"
  }
}
