{
  import io.shiftleft.codepropertygraph.generated.nodes._

  val targetLine = {{line_num}}
  val targetVar  = "{{variable}}"
  val filename   = "{{filename}}"
  val direction  = "{{direction}}"
  val maxResults = {{max_results}}

  val output = new StringBuilder

  def pathBoundaryRegex(f: String): String = "(^|.*/)" + java.util.regex.Pattern.quote(f) + "$"
  val filePattern = pathBoundaryRegex(filename)

  def isAssignmentOp(name: String): Boolean = name.startsWith("<operator>.assignment")
  def lineOf(n: CfgNode): Int = n.lineNumber.getOrElse(-1)

  // The statement around a reference: nearest enclosing assignment, else the
  // outermost enclosing call.
  def statementOf(n: Expression): Expression = {
    var cur: StoredNode = n
    var best: Expression = n
    var steps = 0
    while (steps < 20 && cur.isInstanceOf[Expression]) {
      cur match {
        case c: Call if isAssignmentOp(c.name) => return c
        case c: Call => best = c
        case r: Return => return r
        case _ =>
      }
      cur = cur.asInstanceOf[Expression].astParent
      steps += 1
    }
    best
  }
  def isWriteOf(ref: Identifier): Boolean = statementOf(ref) match {
    case c: Call if isAssignmentOp(c.name) => c.argument.filter(_.argumentIndex == 1).ast.l.exists(_.id == ref.id)
    case _ => false
  }

  val targetMethodOpt = cpg.method
    .where(_.file.name(filePattern))
    .filterNot(_.name == "<global>")
    .filter(m => m.lineNumber.getOrElse(-1) <= targetLine && m.lineNumberEnd.getOrElse(-1) >= targetLine)
    .headOption

  targetMethodOpt match {
    case None =>
      output.append(s"ANCHOR_NOT_FOUND: no method contains line $targetLine in '$filename'\n")

    case Some(method) =>
      // Resolve the variable by declaration (local or parameter), not by name
      // matching: `x` must never pick up `x_count` or `*copy`.
      val decls: List[Declaration] = method.local.nameExact(targetVar).l ++ method.parameter.nameExact(targetVar).l
      if (decls.isEmpty) {
        val known = (method.local.name.l ++ method.parameter.name.l).distinct.sorted.take(30)
        output.append(s"VARIABLE_NOT_FOUND: no local or parameter '$targetVar' in method ${method.name}\n")
        if (known.nonEmpty) output.append(s"Variables in ${method.name}: ${known.mkString(", ")}\n")
      } else {
        output.append("Variable Flow Analysis\n")
        output.append("======================\n")
        output.append(s"Target: variable '$targetVar' at $filename:$targetLine\n")
        output.append(s"Method: ${method.name}\n")
        output.append(s"Direction: $direction\n")
        if (method.call.size == 0 && (method.lineNumberEnd.getOrElse(0) - method.lineNumber.getOrElse(0)) <= 1)
          output.append(
            s"WARNING: method '${method.name}' is present but has no body in this CPG — likely #ifdef/feature-gated. " +
            "Rebuild with generate_cpg(defines=[...], include_paths=[...]) before trusting this flow.\n")

        def refsOf(d: Declaration): List[Identifier] = d match {
          case l: Local => l.referencingIdentifiers.l
          case p: MethodParameterIn => p.referencingIdentifiers.l
          case _ => Nil
        }
        val refs = decls.flatMap(refsOf)
        val refIds = refs.map(_.id).toSet

        // Aliases: pointers assigned the address of THIS variable (p = &x).
        val aliasDecls: List[Local] = method.call.name("<operator>.addressOf")
          .filter(a => a.argument.isIdentifier.l.exists(i => refIds.contains(i.id)))
          .l.flatMap(a => statementOf(a) match {
            case c: Call if isAssignmentOp(c.name) =>
              c.argument.filter(_.argumentIndex == 1).isIdentifier.l.flatMap(t =>
                method.local.nameExact(t.name).l)
            case _ => Nil
          }).distinct
        if (aliasDecls.nonEmpty)
          output.append(s"Aliases (&$targetVar): ${aliasDecls.map(_.name).mkString(", ")}\n")
        val aliasRefs = aliasDecls.flatMap(refsOf)

        // Reaching definitions from the dataflow overlay. Joern conservatively
        // treats any call argument as a possible redefinition, so a plain read
        // such as emit(x) is looked through; &x stays a possible modification.
        def takesAddress(r: Identifier): Boolean = r.astParent match {
          case c: Call => c.name == "<operator>.addressOf"
          case _ => false
        }
        val plainReadIds: Set[Long] = refs.filterNot(r => isWriteOf(r) || takesAddress(r)).map(_.id).toSet
        val ddgSources: Map[Long, List[Long]] = refs.map(r => r.id -> r.ddgIn.l.map(_.id)).toMap

        val refsAtLine = refs.filter(r => lineOf(r) == targetLine)
        val reachingDefIds: Set[Long] = {
          val seen = scala.collection.mutable.Set[Long]()
          val defs = scala.collection.mutable.Set[Long]()
          var frontier = refsAtLine.flatMap(r => ddgSources.getOrElse(r.id, Nil))
          while (frontier.nonEmpty) {
            val next = frontier.filter(seen.add)
            next.filterNot(plainReadIds.contains).foreach(defs += _)
            frontier = next.filter(plainReadIds.contains).flatMap(id => ddgSources.getOrElse(id, Nil))
          }
          defs.toSet
        }
        val definedAtLine = refsAtLine.filter(isWriteOf)
        val reachedByLineDef: Set[Long] = {
          val reached = scala.collection.mutable.Set[Long]()
          var carriers = definedAtLine.map(_.id).toSet
          var grew = true
          while (grew) {
            val hit = refs.filter(r => !reached.contains(r.id) && ddgSources.getOrElse(r.id, Nil).exists(carriers.contains))
            hit.foreach(r => reached += r.id)
            carriers = carriers ++ hit.map(_.id).filter(plainReadIds.contains)
            grew = hit.nonEmpty
          }
          reached.toSet
        }

        // (line, code, kind, note) — every row refers to this declaration or its aliases.
        val rows = scala.collection.mutable.ListBuffer[(Int, String, String, String)]()
        decls.foreach {
          case l: Local => rows += ((l.lineNumber.getOrElse(-1), s"${l.typeFullName} ${l.name}", "declaration", ""))
          case p: MethodParameterIn => rows += ((p.lineNumber.getOrElse(-1), s"${p.typeFullName} ${p.name}", "parameter", ""))
          case _ =>
        }

        def describe(ref: Identifier, viaAlias: Boolean): (Int, String, String, String) = {
          val stmt = statementOf(ref)
          val kind =
            if (isWriteOf(ref)) (if (stmt.asInstanceOf[Call].name == "<operator>.assignment") "assignment" else "modification")
            else if (viaAlias) "alias_use"
            else stmt match { case c: Call if !c.name.startsWith("<operator>") => "use_in_call"; case _ => "use" }
          val note =
            if (direction == "backward" && reachingDefIds.contains(ref.id)) s"reaches line $targetLine"
            else if (direction == "forward" && reachedByLineDef.contains(ref.id)) s"value from line $targetLine"
            else if (direction == "forward" && definedAtLine.nonEmpty && !isWriteOf(ref)) "name only: a later value"
            else ""
          (lineOf(stmt), stmt.code.trim, kind, note)
        }

        val inScope: Identifier => Boolean =
          if (direction == "backward") r => lineOf(r) <= targetLine else r => lineOf(r) >= targetLine
        refs.filter(inScope).foreach(r => rows += describe(r, viaAlias = false))
        aliasRefs.filter(inScope).foreach(r => rows += describe(r, viaAlias = true))

        // A parameter's history continues at its callers' arguments.
        if (direction == "backward") decls.collect { case p: MethodParameterIn => p }.foreach { p =>
          method.callIn.l.foreach { call =>
            call.argument.filter(_.argumentIndex == p.index).l.foreach { arg =>
              rows += ((call.lineNumber.getOrElse(-1), s"${call.method.name}(... '${arg.code.trim}' ...)", "call_site_arg",
                s"${call.method.file.name.headOption.getOrElse("unknown")}"))
            }
          }
        }

        // Filter first (rows are already this variable's), then sort and cap.
        val uniq = rows.toList.distinct.sortBy(r => (r._1, !Set("declaration", "parameter").contains(r._3), r._3))
        output.append(s"Tracked: ${(targetVar :: aliasDecls.map(_.name)).distinct.mkString(", ")}\n")
        output.append("\nDependencies:\n")
        if (uniq.isEmpty) output.append("(none in this direction)\n")
        uniq.take(maxResults).foreach { case (line, code, kind, note) =>
          val suffix = if (note.nonEmpty) s" [$note]" else ""
          output.append(s"[$filename:$line] $code ($kind)$suffix\n")
        }
        if (uniq.size > maxResults)
          output.append(s"... TRUNCATED: showing $maxResults of ${uniq.size} entries. Raise the limit or narrow the line.\n")
      }
  }

  "<codebadger_result>\n" + output.toString() + "</codebadger_result>"
}
