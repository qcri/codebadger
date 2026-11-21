{
  import io.shiftleft.codepropertygraph.generated.nodes._

  val filename = "{{filename}}"
  val lineNum = {{line_num}}
  val variable = "{{variable}}"
  val maxResults = {{max_results}}

  def pathBoundaryRegex(f: String): String = "(^|.*/)" + java.util.regex.Pattern.quote(f) + "$"
  val filePattern = pathBoundaryRegex(filename)

  val methodOpt = cpg.method.where(_.file.name(filePattern)).filterNot(_.name == "<global>")
    .filter(m => m.lineNumber.getOrElse(-1) <= lineNum && m.lineNumberEnd.getOrElse(-1) >= lineNum)
    .l.sortBy(m => m.lineNumberEnd.getOrElse(0) - m.lineNumber.getOrElse(0)).headOption

  // The declaration in scope: a local or parameter of the enclosing method, else
  // a file-level global of that name. The C frontend gives each function that
  // uses a global its own binding local (code "<global> x", closureBindingId set);
  // the global's references are the union over those bindings.
  def isGlobalBinding(l: Local) = l.closureBindingId.nonEmpty || l.code.startsWith("<global> ")
  def globalDecl = cpg.method.name("<global>").where(_.file.name(filePattern)).local.nameExact(variable).l.headOption
  val decl: Option[(Declaration, String)] = methodOpt.flatMap { m =>
    m.local.nameExact(variable).l.headOption.map { l =>
      if (isGlobalBinding(l)) (globalDecl.getOrElse(l): Declaration, "global") else (l: Declaration, "local")
    }.orElse(m.parameter.nameExact(variable).l.headOption.map(p => (p: Declaration, "parameter")))
  }.orElse(globalDecl.map(g => (g: Declaration, "global")))

  def refs(d: Declaration, scope: String): List[Identifier] = (d, scope) match {
    case (_, "global") =>
      (cpg.local.nameExact(variable).filter(isGlobalBinding).l ++ globalDecl.toList)
        .flatMap(_.referencingIdentifiers.l).distinctBy(_.id)
    case (l: Local, _) => l.referencingIdentifiers.l
    case (p: MethodParameterIn, _) => p.referencingIdentifiers.l
    case _ => Nil
  }

  val readWriteOps = Set("<operator>.preIncrement", "<operator>.postIncrement",
                         "<operator>.preDecrement", "<operator>.postDecrement")
  // How a reference touches the variable.
  def access(i: Identifier): String = i.astParent match {
    case c: Call if c.name == "<operator>.addressOf" => "address_taken"
    case c: Call if readWriteOps.contains(c.name) => "read_write"
    case c: Call if c.name == "<operator>.assignment" && i.argumentIndex == 1 => "write"
    case c: Call if c.name.startsWith("<operator>.assignment") && i.argumentIndex == 1 => "read_write"
    case _ => "read"
  }
  // The statement around a reference, for display.
  def statement(i: Identifier): String = {
    var cur: StoredNode = i
    var best: Expression = i
    var steps = 0
    while (steps < 20 && cur.isInstanceOf[Expression]) {
      cur match {
        case c: Call => best = c
        case r: Return => best = r
        case _ =>
      }
      cur = cur.asInstanceOf[Expression].astParent
      steps += 1
    }
    best.code.replaceAll("\\s+", " ").take(160)
  }

  val result: Map[String, Any] = (methodOpt, decl) match {
    case (None, None) => Map("status" -> "no_method")
    case (_, None) => Map("status" -> "not_found", "method" -> methodOpt.map(_.name).getOrElse(""),
      "variables" -> methodOpt.toList.flatMap(m => m.local.name.l ++ m.parameter.name.l).filter(_.nonEmpty).distinct.sorted.take(30))
    case (_, Some((d, scope))) =>
      val all = refs(d, scope).sortBy(i => (i.method.filename, i.lineNumber.getOrElse(-1), i.columnNumber.getOrElse(-1)))
      val (line, typ) = d match {
        case l: Local => (l.lineNumber.getOrElse(-1), l.typeFullName)
        case p: MethodParameterIn => (p.lineNumber.getOrElse(-1), p.typeFullName)
        case _ => (-1, "")
      }
      val counts = all.groupBy(access).view.mapValues(_.size).toMap
      Map(
        "status" -> "ok",
        "declaration" -> Map("node_id" -> d.id.toString, "name" -> variable, "scope" -> scope,
                             "type" -> typ, "line" -> line,
                             "method" -> (if (scope == "global") "" else methodOpt.map(_.name).getOrElse("")),
                             "method_id" -> (if (scope == "global") "" else methodOpt.map(_.id.toString).getOrElse(""))),
        "total" -> all.size,
        "counts" -> counts,
        "references" -> all.take(maxResults).map(i => Map(
          "node_id" -> i.id.toString, "access" -> access(i), "code" -> statement(i),
          "filename" -> i.method.filename, "line" -> i.lineNumber.getOrElse(-1),
          "column" -> i.columnNumber.getOrElse(-1), "method" -> i.method.name,
          "method_id" -> i.method.id.toString)))
  }
  "<codebadger_result>\n" + List(result).toJson + "\n</codebadger_result>"
}
