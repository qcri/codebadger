{
  import scala.util.Try
  val methodName = "{{method_name}}"
  val maxNodes = {{max_nodes}}
  val sb = new StringBuilder()

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
  def describeCandidates(sb: StringBuilder): Unit = {
    sb.append(s"Ambiguous method name '$methodName': ${candidates.size} matches. Re-run with method_id set to one of:\n")
    candidates.sortBy(_.id).take(10).foreach { c =>
      val loc = s"${c.file.name.headOption.getOrElse("unknown")}:${c.lineNumber.getOrElse(-1)}"
      sb.append(s"  method_id=${c.id}  ${c.fullName}  ($loc)\n")
    }
    if (candidates.size > 10) sb.append(s"  ... and ${candidates.size - 10} more; narrow method_name.\n")
  }
  def describeMissing(sb: StringBuilder): Unit = {
    if (methodId >= 0) sb.append(s"Method not found for method_id $methodId\n")
    else {
      sb.append(s"Method not found: $methodName\n")
      val similar = cpg.method.name(s".*$methodName.*").name.l.distinct.take(10)
      if (similar.nonEmpty) {
        sb.append(s"\nDid you mean one of these?\n")
        similar.foreach(m => sb.append(s"  - $m\n"))
      }
    }
  }

  candidates match {
    case List(method) =>
      sb.append(s"Control Flow Graph for ${method.name}\n")
      sb.append("=" * 60 + "\n")

      val nodes = method.cfgNode.take(maxNodes).l
      val nodeIds = nodes.map(_.id).toSet
      
      sb.append("Nodes:\n")
      nodes.foreach { n =>
        val id = n.id
        val code = n.code.take(50).replaceAll("\n", " ")
        val typeName = n.getClass.getSimpleName
        sb.append(s"  [$id] $typeName: $code\n")
      }
      
      sb.append("\nEdges:\n")
      val edges = nodes.flatMap(n => 
        n.outE("CFG")
          .filter(e => nodeIds.contains(e.dst.id))
          .map(e => (n.id, e.dst.id, Option(e.property).map(_.toString).getOrElse("")))
      ).distinct

      if (edges.isEmpty) {
        sb.append("  (No control flow edges found)\n")
      } else {
        edges.foreach { case (from, to, label) =>
          val labelStr = if (label.toString.nonEmpty) s" [Label: $label]" else ""
          sb.append(s"  [$from] -> [$to]$labelStr\n")
        }
      }

    case Nil => describeMissing(sb)
    case _ => describeCandidates(sb)
  }
  
  "<codebadger_result>\n" + sb.toString() + "</codebadger_result>"
}
