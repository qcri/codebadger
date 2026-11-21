{
  import io.shiftleft.codepropertygraph.generated.nodes._

  val filename = "{{filename}}"
  val lineNum = {{line_num}}
  val column = {{column}}
  val includeAll = {{include_all}}
  val maxResults = {{max_results}}

  def pathBoundaryRegex(f: String): String = "(^|.*/)" + java.util.regex.Pattern.quote(f) + "$"
  val filePattern = pathBoundaryRegex(filename)
  val files = cpg.file.name(filePattern).name.l.distinct

  // Candidates on the line: calls always (real calls first, then operators such
  // as `x = ...`); with include_all also identifiers, literals, returns and
  // control structures.
  def onLine(n: CfgNode): Boolean = n.lineNumber.contains(lineNum)
  val calls: List[CfgNode] = cpg.call.where(_.file.name(filePattern)).filter(onLine).l
  val extra: List[CfgNode] =
    if (!includeAll) Nil
    else cpg.identifier.where(_.file.name(filePattern)).filter(onLine).l ++
         cpg.literal.where(_.file.name(filePattern)).filter(onLine).l ++
         cpg.ret.where(_.file.name(filePattern)).filter(onLine).l ++
         cpg.controlStructure.where(_.file.name(filePattern)).filter(onLine).l

  def isOperator(n: CfgNode) = n match { case c: Call => c.name.startsWith("<operator>"); case _ => false }
  def rank(n: CfgNode) = n match {
    case c: Call if !isOperator(c) => 0
    case _: Call => 1
    case _: ControlStructure | _: Return => 2
    case _ => 3
  }
  def col(n: CfgNode) = n.columnNumber.getOrElse(-1)
  // A column selects the nodes whose source text covers it on this line.
  def covers(n: CfgNode): Boolean = {
    val start = col(n)
    val firstLine = n.code.takeWhile(_ != '\n')
    start >= 0 && start <= column && column < start + math.max(firstLine.length, 1)
  }

  val all = (calls ++ extra).distinctBy(_.id)
  val selected = if (column > 0) all.filter(covers) else all
  // With a column, the innermost covering node (shortest code) comes first.
  val ordered =
    if (column > 0) selected.sortBy(n => (n.code.length, rank(n), col(n)))
    else selected.sortBy(n => (rank(n), col(n), n.id))

  def name(n: CfgNode): String = n match {
    case c: Call => c.name
    case i: Identifier => i.name
    case c: ControlStructure => c.controlStructureType
    case _ => n.label
  }

  val result = Map(
    "files" -> files,
    "total" -> ordered.size,
    "candidates" -> ordered.take(maxResults).map { n =>
      val m = n.method
      Map(
        "node_id" -> n.id.toString, "kind" -> n.label, "name" -> name(n),
        "code" -> n.code.replaceAll("\\s+", " ").take(160),
        "filename" -> m.filename, "line" -> n.lineNumber.getOrElse(-1), "column" -> col(n),
        "operator" -> isOperator(n),
        "method" -> m.name, "method_id" -> m.id.toString, "method_full_name" -> m.fullName)
    })
  "<codebadger_result>\n" + List(result).toJson + "\n</codebadger_result>"
}
