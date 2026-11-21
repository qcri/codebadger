{
  val methodName = "{{method_name}}"
  val maxDepth = {{depth}}
  val direction = "{{direction}}"
  val maxEdges = 500
  val fanoutLimit = 50

  type M = io.shiftleft.codepropertygraph.generated.nodes.Method

  // Resolve the root: an exact graph node ID wins; otherwise the name pattern must
  // identify one method (internal definitions preferred over external stubs).
  val methodId = {{method_id}}L
  val candidates =
    if (methodId >= 0) cpg.method.filter(_.id == methodId).l
    else {
      val all = cpg.method.name(methodName).l
      val internal = all.filterNot(_.isExternal)
      if (internal.nonEmpty) internal else all
    }
  def methodInfo(m: M) = Map(
    "node_id" -> m.id.toString, "name" -> m.name, "full_name" -> m.fullName,
    "filename" -> m.file.name.headOption.getOrElse("unknown"), "line" -> m.lineNumber.getOrElse(-1),
    "external" -> m.isExternal)
  def describeCandidates() = Map(
    "status" -> "ambiguous", "count" -> candidates.size,
    "candidates" -> candidates.sortBy(_.id).take(10).map(methodInfo))
  def describeMissing() = Map(
    "status" -> "not_found",
    "suggestions" -> (if (methodId >= 0) List.empty[String]
                      else cpg.method.name(s".*${java.util.regex.Pattern.quote(methodName)}.*").name.l.distinct.take(10)))

  val result: Map[String, Any] = candidates match {
    case List(root) =>
      val outgoing = direction == "outgoing"
      def neighbors(m: M): List[M] =
        (if (outgoing) m.call.callee.l else m.caller.l)
          .filterNot(_.name.startsWith("<operator>")).distinctBy(_.id).sortBy(_.id)

      // Breadth-first over methods (by node ID: overloads and file-static helpers
      // share short names). Edges are stored caller -> callee in both directions.
      val byId = scala.collection.mutable.Map[Long, M](root.id -> root)
      val edges = scala.collection.mutable.LinkedHashMap[(Long, Long), Int]() // (caller, callee) -> depth
      val capped = scala.collection.mutable.ListBuffer[Map[String, Any]]()
      val visited = scala.collection.mutable.Set[Long]()
      val queue = scala.collection.mutable.Queue[(M, Int)]((root, 0))
      var edgeCapHit = false
      while (queue.nonEmpty && !edgeCapHit) {
        val (current, d) = queue.dequeue()
        if (d < maxDepth && visited.add(current.id)) {
          val all = neighbors(current)
          if (all.size > fanoutLimit)
            capped += Map("method_id" -> current.id.toString, "name" -> current.name,
                          "shown" -> fanoutLimit, "total" -> all.size)
          all.take(fanoutLimit).foreach { n =>
            val key = if (outgoing) (current.id, n.id) else (n.id, current.id)
            if (!edges.contains(key)) {
              if (edges.size >= maxEdges) edgeCapHit = true
              else {
                edges(key) = d + 1
                byId(n.id) = n
                if (!visited.contains(n.id)) queue.enqueue((n, d + 1))
              }
            }
          }
        }
      }

      // An edge is recursive when its callee can reach its caller again through
      // the edges found — not merely because the callee was seen before (a callee
      // shared by two callers is not a cycle).
      val succ: Map[Long, List[Long]] = edges.keys.toList.groupBy(_._1).view.mapValues(_.map(_._2)).toMap
      def reaches(from: Long, target: Long): Boolean = {
        val seen = scala.collection.mutable.Set[Long](from)
        val work = scala.collection.mutable.Stack[Long](from)
        var found = from == target
        while (!found && work.nonEmpty) {
          succ.getOrElse(work.pop(), Nil).foreach { n =>
            if (n == target) found = true
            else if (seen.add(n)) work.push(n)
          }
        }
        found
      }

      def callSites(caller: M, callee: M) =
        caller.call.filter(_.callee.id.l.contains(callee.id)).l.sortBy(_.id).take(10).map(c => Map(
          "node_id" -> c.id.toString, "filename" -> c.file.name.headOption.getOrElse("unknown"),
          "line" -> c.lineNumber.getOrElse(-1), "column" -> c.columnNumber.getOrElse(-1)))

      val edgeRows = edges.toList.map { case ((from, to), d) =>
        val caller = byId(from)
        val callee = byId(to)
        Map(
          "from_id" -> from.toString, "from" -> caller.name,
          "to_id" -> to.toString, "to" -> callee.name,
          "depth" -> d,
          "call_sites" -> callSites(caller, callee),
          "definition" -> Map("filename" -> callee.file.name.headOption.getOrElse("unknown"),
                              "line" -> callee.lineNumber.getOrElse(-1)),
          "recursive" -> reaches(to, from))
      }

      // Gated-body probe: a method with zero calls and a <=1-line span almost
      // always had its body preprocessed out (#ifdef / feature macro).
      val gatedBody = root.call.size == 0 &&
        (root.lineNumberEnd.getOrElse(0) - root.lineNumber.getOrElse(0)) <= 1
      val warnings = if (gatedBody) List(
        s"method '${root.name}' is present but has no body in this CPG (likely #ifdef/feature-gated); " +
        "rebuild with generate_cpg(defines=[...], include_paths=[...]) before trusting an empty graph") else Nil

      // Indirect-dispatch fallback: Joern adds no caller edge for a call through a
      // function pointer, vtable or callback, so list where the address is taken.
      val addressTaken =
        if (!outgoing && edgeRows.isEmpty)
          // The C frontend also emits a reference at each function's own definition
          // in <global>; that is not an address-taken use.
          cpg.methodRef.where(_.referencedMethod.filter(_.id == root.id))
            .filterNot(mr => mr.lineNumber == root.lineNumber &&
                             mr.file.name.headOption == root.file.name.headOption)
            .map(mr => Map("method" -> mr.method.fullName, "filename" -> mr.file.name.headOption.getOrElse("?"),
                           "line" -> mr.lineNumber.getOrElse(-1)))
            .l.distinct.take(50)
        else Nil

      Map(
        "status" -> "ok", "root" -> methodInfo(root), "direction" -> direction, "depth" -> maxDepth,
        "edges" -> edgeRows, "truncated" -> (edgeCapHit || capped.nonEmpty),
        "edge_cap" -> maxEdges, "edge_cap_hit" -> edgeCapHit, "capped_methods" -> capped.toList,
        "warnings" -> warnings, "address_taken_sites" -> addressTaken)
    case Nil => describeMissing()
    case _ => describeCandidates()
  }

  "<codebadger_result>\n" + List(result).toJson + "\n</codebadger_result>"
}
