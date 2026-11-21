{
  val methodName = "{{method_name}}"
  val maxNodes = {{max_nodes}}

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
  def methodInfo(m: io.shiftleft.codepropertygraph.generated.nodes.Method) = Map(
    "node_id" -> m.id.toString, "name" -> m.name, "full_name" -> m.fullName,
    "filename" -> m.file.name.headOption.getOrElse("unknown"), "line" -> m.lineNumber.getOrElse(-1))
  def describeCandidates() = Map(
    "status" -> "ambiguous", "count" -> candidates.size,
    "candidates" -> candidates.sortBy(_.id).take(10).map(methodInfo))
  def describeMissing() = Map(
    "status" -> "not_found",
    "suggestions" -> (if (methodId >= 0) List.empty[String]
                      else cpg.method.name(s".*${java.util.regex.Pattern.quote(methodName)}.*").name.l.distinct.take(10)))

  val result: Map[String, Any] = candidates match {
    case List(method) =>
      // Walk the CFG breadth-first from the method entry so a capped view is the
      // connected prefix of the graph; AST-only nodes (BLOCK, CONTROL_STRUCTURE)
      // carry no CFG edges and are left out.
      val inMethod = (List(method) ++ method.cfgNode.l ++ List(method.methodReturn)).distinctBy(_.id)
      val ids = inMethod.map(_.id).toSet
      val succ: Map[Long, List[Long]] = inMethod.map(n =>
        n.id -> n._cfgOut.map(_.id).filter(ids.contains).l.distinct).toMap
      val hasIn = succ.values.flatten.toSet
      val graph = inMethod.filter(n => n.id == method.id || succ(n.id).nonEmpty || hasIn.contains(n.id))
      val byId = graph.map(n => n.id -> n).toMap

      val order = scala.collection.mutable.LinkedHashSet[Long](method.id)
      val queue = scala.collection.mutable.Queue[Long](method.id)
      while (queue.nonEmpty) {
        succ(queue.dequeue()).filter(byId.contains).foreach(s => if (order.add(s)) queue.enqueue(s))
      }
      graph.map(_.id).sorted.foreach(order.add) // unreachable nodes last

      val visible = order.toList.take(maxNodes)
      val visibleSet = visible.toSet
      val totalEdges = graph.map(n => succ(n.id).count(byId.contains)).sum
      Map(
        "status" -> "ok",
        "method" -> methodInfo(method),
        "total_nodes" -> graph.size,
        "total_edges" -> totalEdges,
        "nodes" -> visible.map { id =>
          val n = byId(id)
          Map("node_id" -> id.toString, "kind" -> n.label,
              "code" -> (if (id == method.id) n.code.takeWhile(_ != '{') else n.code).replaceAll("\\s+", " ").trim.take(80),
              "line" -> n.lineNumber.getOrElse(-1),
              "out_degree" -> succ(id).count(byId.contains))
        },
        "edges" -> visible.flatMap(from => succ(from).filter(visibleSet.contains).map(to =>
          Map("from" -> from.toString, "to" -> to.toString, "branch" -> (succ(from).size > 1))))
      )
    case Nil => describeMissing()
    case _ => describeCandidates()
  }

  "<codebadger_result>\n" + List(result).toJson + "\n</codebadger_result>"
}
