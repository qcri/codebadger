{
  import io.shiftleft.codepropertygraph.generated.nodes._

  val filename = "{{filename}}"
  val lineNum = {{line_num}}

  def pathBoundaryRegex(f: String): String = "(^|.*/)" + java.util.regex.Pattern.quote(f) + "$"
  val filePattern = pathBoundaryRegex(filename)

  /** (label, size argument index, destination argument index) for sized calls. */
  def classifySizedOp(callName: String): Option[(String, Int, Option[Int])] = callName match {
    case "memcpy" | "memmove"                          => Some(("Memory Copy", 3, Some(1)))
    case "memset"                                      => Some(("Memory Set", 3, Some(1)))
    case "strncpy" | "strlcpy"                         => Some(("String Copy", 3, Some(1)))
    case "strncat" | "strlcat"                         => Some(("String Concat", 3, Some(1)))
    case "snprintf" | "vsnprintf"                      => Some(("Formatted Output", 2, Some(1)))
    case "read" | "recv"                               => Some(("I/O Read", 3, Some(2)))
    case "fread"                                       => Some(("File Read", 3, Some(1)))
    case "realloc" | "reallocarray"                    => Some(("Memory Reallocation", 2, None))
    case "calloc"                                      => Some(("Memory Allocation", 1, None))
    case "aligned_alloc" | "memalign" | "posix_memalign" => Some(("Memory Allocation", 2, None))
    case "malloc" | "valloc" | "pvalloc"               => Some(("Memory Allocation", 1, None))
    case "strndup"                                     => Some(("String Duplication", 2, None))
    case _                                             => None
  }

  val comparisons = Map(
    "<operator>.lessThan" -> "<", "<operator>.lessEqualsThan" -> "<=",
    "<operator>.greaterThan" -> ">", "<operator>.greaterEqualsThan" -> ">=",
    "<operator>.equals" -> "==", "<operator>.notEquals" -> "!=")
  val negated = Map("<" -> ">=", "<=" -> ">", ">" -> "<=", ">=" -> "<", "==" -> "!=", "!=" -> "==")
  val flipped = Map("<" -> ">", "<=" -> ">=", ">" -> "<", ">=" -> "<=", "==" -> "==", "!=" -> "!=")
  val conditionOps = Set("<operator>.logicalAnd", "<operator>.logicalOr", "<operator>.logicalNot")

  def arg(c: Call, i: Int): Option[Expression] = c.argument.l.find(_.argumentIndex == i)
  def identifiersIn(e: Expression): List[Identifier] = (e match {
    case i: Identifier => List(i)
    case other => other.ast.isIdentifier.l
  })

  // Facts a condition guarantees on one of its branches: `!(a || b)` gives `!a` and
  // `!b`; `a && b` gives `a` and `b`. Each fact is (comparison, holds).
  def facts(e: Expression, holds: Boolean): List[(Call, Boolean)] = e match {
    case c: Call if c.name == "<operator>.logicalNot" => arg(c, 1).toList.flatMap(facts(_, !holds))
    case c: Call if c.name == "<operator>.logicalAnd" && holds => c.argument.l.flatMap(facts(_, true))
    case c: Call if c.name == "<operator>.logicalOr" && !holds => c.argument.l.flatMap(facts(_, false))
    case c: Call if comparisons.contains(c.name) => List((c, holds))
    case _ => Nil
  }

  // Render a fact about `v` with v on the left: ("i", ">=", "0").
  def factAbout(cmp: Call, holds: Boolean, v: String): Option[(String, String)] = {
    val op0 = comparisons(cmp.name)
    val op = if (holds) op0 else negated(op0)
    val (l, r) = (arg(cmp, 1), arg(cmp, 2))
    def isVar(e: Option[Expression]) = e.exists { case i: Identifier => i.name == v; case _ => false }
    if (isVar(l)) Some((op, r.map(_.code).getOrElse("?")))
    else if (isVar(r)) Some((flipped(op), l.map(_.code).getOrElse("?")))
    else None
  }

  // Which branch of the controlling condition reaches `target`.
  def branchTo(cond: Expression, target: Call): Option[(ControlStructure, String, Boolean)] = {
    var top: Expression = cond
    var steps = 0
    while (steps < 20 && (top.astParent match { case c: Call => conditionOps.contains(c.name); case _ => false })) {
      top = top.astParent.asInstanceOf[Expression]; steps += 1
    }
    top.astParent match {
      case cs: ControlStructure =>
        val inside = (t: Iterator[AstNode]) => t.ast.id.l.contains(target.id)
        val kind = cs.controlStructureType
        if (Set("WHILE", "FOR", "DO").contains(kind)) {
          // FOR keeps its body as a plain AST child, not under whenTrue.
          val inCondition = cs.condition.ast.id.l.contains(target.id)
          if (!inCondition && cs.ast.id.l.contains(target.id)) Some((cs, "loop", true))
          else Some((cs, "after_loop", false))
        } else if (kind == "IF") {
          def terminates(t: Iterator[AstNode]) = {
            val nodes = t.ast.l
            nodes.exists(_.isInstanceOf[Return]) ||
              nodes.collect { case c: ControlStructure => c.controlStructureType }.exists(Set("BREAK", "CONTINUE", "GOTO"))
          }
          if (inside(cs.whenTrue)) Some((cs, "if_true", true))
          else if (inside(cs.whenFalse)) Some((cs, "if_false", false))
          else if (terminates(cs.whenTrue)) Some((cs, "early_return", false))
          else if (terminates(cs.whenFalse)) Some((cs, "early_return", true))
          else None
        } else None
      case _ => None
    }
  }

  def isUnsigned(v: String, at: Call): Boolean =
    identifiersIn(at).filter(_.name == v).map(_.typeFullName).exists(t => t.contains("unsigned") || t == "size_t")

  /** Guards on `v` that control `target`, with branch polarity and established facts. */
  def guardsFor(target: Call, v: String): List[Map[String, Any]] =
    target.controlledBy.collect { case e: Expression => e }.l.distinctBy(_.id).flatMap { cond =>
      branchTo(cond, target).toList.flatMap { case (cs, how, holds) =>
        val condition = cs.condition.headOption.getOrElse(cond)
        val established = facts(condition, holds).flatMap { case (cmp, h) => factAbout(cmp, h, v) }
        if (established.isEmpty) Nil
        else List(Map(
          "line" -> cs.lineNumber.getOrElse(-1),
          "condition" -> condition.code,
          "branch" -> (if (holds) "true" else "false"),
          "kind" -> how,
          "establishes" -> established.map { case (op, rhs) => s"$v $op $rhs" }))
      }
    }.distinctBy(m => (m("line"), m("condition")))

  def bounds(guards: List[Map[String, Any]], v: String): (Boolean, Boolean) = {
    val ops = guards.flatMap(_("establishes").asInstanceOf[List[String]]).map(_.stripPrefix(v + " ").split(" ", 2))
    val upper = ops.exists(p => Set("<", "<=", "==").contains(p(0)))
    val lower = ops.exists(p => Set(">", ">=", "==").contains(p(0)))
    (lower, upper)
  }

  val atLine = cpg.call.where(_.file.name(filePattern)).filter(_.lineNumber.contains(lineNum)).l
  val access = atLine.filter(_.name == "<operator>.indirectIndexAccess").sortBy(_.columnNumber.getOrElse(0)).headOption
  val sized = atLine.filter(c => classifySizedOp(c.name).nonEmpty).sortBy(_.columnNumber.getOrElse(0)).headOption

  val target: Option[(Call, Map[String, Any], Option[Expression], Boolean)] = access match {
    case Some(a) =>
      Some((a, Map("kind" -> "buffer_access", "code" -> a.code, "buffer" -> arg(a, 1).map(_.code).getOrElse("?"),
                   "index" -> arg(a, 2).map(_.code).getOrElse("?")), arg(a, 2), true))
    case None => sized.map { c =>
      val (label, sizeIdx, dstIdx) = classifySizedOp(c.name).get
      (c, Map("kind" -> "sized_call", "label" -> label, "function" -> c.name, "code" -> c.code,
              "size" -> arg(c, sizeIdx).map(_.code).getOrElse("?"),
              "destination" -> dstIdx.flatMap(arg(c, _)).map(_.code).getOrElse("")), arg(c, sizeIdx), false)
    }
  }

  val result: Map[String, Any] = target match {
    case None =>
      Map("status" -> "unsupported",
          "calls_on_line" -> atLine.filterNot(_.name.startsWith("<operator>")).map(_.code).distinct.take(10))
    case Some((call, info, valueExpr, needsLower)) =>
      // The variable checked is the first identifier in the index / size argument.
      val variable = valueExpr.toList.flatMap(identifiersIn).headOption.map(_.name)
      variable match {
        case None =>
          Map("status" -> "no_variable", "target" -> (info + ("line" -> lineNum)))
        case Some(v) =>
          val local = guardsFor(call, v)
          // A parameter's checks may live in the callers.
          val method = call.method
          val param = method.parameter.nameExact(v).l.headOption
          val callerGuards = param.toList.flatMap { p =>
            method.callIn.l.take(50).flatMap { site =>
              site.argument.l.find(_.argumentIndex == p.index).toList
                .flatMap(identifiersIn).headOption.toList.flatMap { a =>
                  guardsFor(site, a.name).map(g => g ++ Map(
                    "caller" -> site.method.name, "caller_id" -> site.method.id.toString,
                    "call_line" -> site.lineNumber.getOrElse(-1), "variable" -> a.name))
                }
            }
          }
          val unsigned = isUnsigned(v, call)
          val (lowerL, upperL) = bounds(local, v)
          val callerBounds = callerGuards.map(g => bounds(List(g), g("variable").toString))
          val lower = lowerL || unsigned || callerBounds.exists(_._1)
          val upper = upperL || callerBounds.exists(_._2)
          val status =
            if (upper && (lower || !needsLower)) "guarded"
            else if (upper || lower || local.nonEmpty || callerGuards.nonEmpty) "partially_guarded"
            else "no_guard_found"
          Map("status" -> status,
              "target" -> (info ++ Map("line" -> lineNum, "variable" -> v, "variable_unsigned" -> unsigned,
                                       "method" -> method.name, "method_id" -> method.id.toString)),
              "guards" -> local, "caller_guards" -> callerGuards,
              "lower_bound" -> lower, "upper_bound" -> upper, "lower_bound_required" -> needsLower,
              "is_parameter" -> param.nonEmpty)
      }
  }

  "<codebadger_result>\n" + List(result).toJson + "\n</codebadger_result>"
}
