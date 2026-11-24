{
  import scala.collection.mutable
  import io.shiftleft.codepropertygraph.generated.nodes._

  def normalizeFilename(path: String, target: String): Boolean = {
    def toPath(p: String) = p.replaceAll("\\\\", "/")
    val p = toPath(path)
    val t = toPath(target)
    p == t || p.endsWith("/" + t) || t.endsWith("/" + p)
  }

  val filename = "{{filename}}"
  val lineNum = {{line_num}}
  val callName = "{{call_name}}"
  val maxDepth = {{max_depth}}
  val includeBackward = {{include_backward}}
  val includeForward = {{include_forward}}
  val includeControlFlow = {{include_control_flow}}
  val direction = "{{direction}}"

  val output = new StringBuilder()
  // A failed selection is returned as plain text (ANCHOR_NOT_FOUND: ...); a slice
  // returns the text summary together with the same rows as structured evidence.
  val errorOut = new StringBuilder()
  val evidence = mutable.LinkedHashMap[String, Any]()
  val warnings = mutable.ListBuffer[String]()

  // Per-node code clip: one macro-expanded statement can be enormous.
  def clip(s: String, n: Int = 200): String =
    if (s != null && s.length > n) s.take(n) + s"…(+${s.length - n} chars)" else s

  // Cap on slice entries per direction; surfaced via a truncation note.
  val maxNodes = 60

  type M = Method
  def fileOf(n: StoredNode): String = n match {
    case c: CfgNode => c.method.file.name.headOption.getOrElse("unknown")
    case _ => "unknown"
  }
  def lineOf(n: StoredNode): Int = n match {
    case c: CfgNode => c.lineNumber.getOrElse(-1)
    case _ => -1
  }
  def isAssignmentOp(name: String): Boolean = name.startsWith("<operator>.assignment")
  // x++ and --x define their operand without an assignment operator.
  val incDecOps = Set("<operator>.postIncrement", "<operator>.preIncrement",
    "<operator>.postDecrement", "<operator>.preDecrement")

  // The statement a node belongs to: the nearest enclosing assignment, else the
  // outermost enclosing call (so `use(f(x))` reports the whole statement).
  def statementOf(n: Expression): Expression = {
    var cur: StoredNode = n
    var best: Expression = n
    var steps = 0
    while (steps < 20 && cur.isInstanceOf[Expression]) {
      cur match {
        case c: Call if isAssignmentOp(c.name) => return c
        case c: Call => best = c
        case _ =>
      }
      cur = cur.asInstanceOf[Expression].astParent
      steps += 1
    }
    best
  }

  // Enclosing method: prefer a real method over <global> (header inlines).
  val targetMethodOpt: Option[M] = {
    def encloses(m: M) = {
      val start = m.lineNumber.getOrElse(-1)
      val end = m.lineNumberEnd.getOrElse(-1)
      start <= lineNum && end >= lineNum
    }
    val inFile = cpg.method.filter(m => normalizeFilename(m.file.name.headOption.getOrElse(""), filename)).l
    inFile.filterNot(_.name == "<global>").find(encloses).orElse(inFile.find(encloses))
  }

  // A write through an index, deref or field (buf[i] = c) also uses the base and
  // index of its target. A plain variable target is the defined value, not a use.
  def assignmentOperands(a: Call): List[Identifier] = {
    val target = a.argument.filter(_.argumentIndex == 1).l
    val targetUses = target match {
      case List(_: Identifier) => Nil
      case _ => target.ast.isIdentifier.l
    }
    (a.argument.filter(_.argumentIndex >= 2).ast.isIdentifier.l ++ targetUses).distinctBy(_.id)
  }

  // Anchor: (node, code, name, line, operand identifiers).
  // An explicitly named call is authoritative: it must be on the requested line.
  // Without a name, try the line, then the nearest line within ±3 (macro
  // expansion), and report the move.
  case class Anchor(node: Expression, code: String, name: String, line: Int, operands: List[Identifier])

  def anchorOnLine(method: M, ln: Int): Option[Anchor] = {
    val calls = method.call.filter(c => c.lineNumber.getOrElse(-1) == ln).l
    if (callName.nonEmpty) {
      calls.find(_.name == callName).map(c =>
        Anchor(c, c.code, c.name, ln, c.argument.ast.isIdentifier.l))
    } else {
      calls.filterNot(_.name.startsWith("<operator>")).headOption
        .map(c => Anchor(c, c.code, c.name, ln, c.argument.ast.isIdentifier.l))
        .orElse(method.assignment.filter(a => a.lineNumber.getOrElse(-1) == ln).headOption
          .map(a => Anchor(a, a.code, "<assignment>", ln, assignmentOperands(a))))
        .orElse(method.controlStructure.filter(c => c.lineNumber.getOrElse(-1) == ln).headOption
          .flatMap(ctrl => ctrl.condition.headOption.map(cond =>
            Anchor(cond, cond.code, "<condition>", ln, cond.ast.isIdentifier.l))))
        .orElse(calls.headOption.map(c => Anchor(c, c.code, c.name, ln, c.argument.ast.isIdentifier.l)))
    }
  }

  def findAnchor(method: M): Option[Anchor] =
    anchorOnLine(method, lineNum).orElse {
      if (callName.nonEmpty) None
      else (1 to 3).flatMap(d => List(lineNum - d, lineNum + d)).flatMap(anchorOnLine(method, _)).headOption
    }

  targetMethodOpt match {
    case Some(method) => {
      if (method.call.size == 0 && (method.lineNumberEnd.getOrElse(0) - method.lineNumber.getOrElse(0)) <= 1) {
        val w = s"method '${method.name}' is present but has no body in this CPG — likely #ifdef/feature-gated. " +
          "Rebuild with generate_cpg(defines=[...], include_paths=[...]) before trusting this slice."
        warnings += w
        output.append(s"WARNING: $w\n")
      }

      findAnchor(method) match {
        case Some(anchor) => {
          val targetFile = method.file.name.headOption.getOrElse("unknown")
          output.append(s"Program Slice for ${anchor.name} at $targetFile:${anchor.line}\n")
          output.append("=" * 60 + "\n")
          output.append(s"Requested: $filename:$lineNum${if (callName.nonEmpty) ":" + callName else ""}\n")
          if (anchor.line != lineNum)
            output.append(s"Selected anchor: line ${anchor.line} (no statement on line $lineNum; nearest within 3 lines)\n")
          output.append(s"Code: ${clip(anchor.code)}\n")
          output.append(s"Method: ${method.fullName}\n")
          val anchorVars = anchor.operands.map(_.name).distinct
          if (anchorVars.nonEmpty) output.append(s"Variables: ${anchorVars.mkString(", ")}\n")
          evidence("anchor") = Map(
            "node_id" -> anchor.node.id.toString, "name" -> anchor.name, "code" -> clip(anchor.code),
            "filename" -> targetFile, "line" -> anchor.line, "requested_line" -> lineNum,
            "moved" -> (anchor.line != lineNum), "variables" -> anchorVars)
          evidence("method") = Map("node_id" -> method.id.toString, "name" -> method.name, "full_name" -> method.fullName)

          // === BACKWARD SLICE: reaching definitions (dataflow overlay) ===
          if (includeBackward) {
            val seen = mutable.Set[Long]()
            // (line, file, variable, code, depends-on, kind)
            val deps = mutable.ListBuffer[(Int, String, String, String, List[String], String)]()

            def backward(uses: List[Identifier], depth: Int): Unit = {
              if (depth <= 0) return
              uses.foreach { use =>
                use.ddgIn.l.foreach { d =>
                  if (!seen.contains(d.id)) {
                    seen.add(d.id)
                    d match {
                      case p: MethodParameterIn =>
                        val m = p.method
                        deps += ((lineOf(p), fileOf(p), p.name, s"parameter ${p.name} of ${m.name}", Nil, "parameter"))
                        // Statically resolved callers pass the argument at the same index.
                        m.callIn.l.foreach { site =>
                          site.argument.filter(_.argumentIndex == p.index).l.foreach { arg =>
                            val argIds = arg.ast.isIdentifier.l
                            deps += ((lineOf(site), fileOf(site), p.name, s"argument to ${m.name}: ${clip(site.code)}",
                              argIds.map(_.name).distinct, "caller"))
                            backward(argIds, depth - 1)
                          }
                        }
                        // Unresolved dynamic calls with the same short name: candidates only.
                        cpg.call.filter(c => c.dispatchType == "DYNAMIC_DISPATCH" && c.name == m.name && c.callee.isEmpty)
                          .l.take(10).foreach { site =>
                            deps += ((lineOf(site), fileOf(site), p.name,
                              s"name-based candidate (unresolved dynamic call): ${clip(site.code)}", Nil, "candidate"))
                          }
                      case e: Expression if (e.astParent match {
                            case c: Call => incDecOps.contains(c.name)
                            case _ => false
                          }) =>
                        val c = e.astParent.asInstanceOf[Call]
                        deps += ((lineOf(c), fileOf(c), e.code, c.code, List(e.code), "definition"))
                        backward(List(e).collect { case i: Identifier => i }, depth - 1)
                      case e: Expression =>
                        val stmt = statementOf(e)
                        val assignTarget = stmt match {
                          case c: Call if isAssignmentOp(c.name) =>
                            c.argument.filter(_.argumentIndex == 1).ast.l.exists(_.id == e.id)
                          case _ => false
                        }
                        val passedToCall = e.astParent match {
                          case call: Call => !call.name.startsWith("<operator>")
                          case _ => false
                        }
                        if (assignTarget) {
                          val c = stmt.asInstanceOf[Call]
                          val rhs = c.argument.filter(_.argumentIndex >= 2).ast.isIdentifier.l
                          deps += ((lineOf(c), fileOf(c), e.code, c.code, rhs.map(_.name).distinct, "definition"))
                          backward(rhs, depth - 1)
                        } else if (passedToCall) {
                          // Joern treats a call argument as possibly written by the callee.
                          val call = e.astParent.asInstanceOf[Call]
                          deps += ((lineOf(call), fileOf(call), e.code, s"may be modified by ${clip(call.code)}", Nil, "call_argument"))
                          backward(List(e).collect { case i: Identifier => i }, depth - 1)
                        } else {
                          // A read (e.g. inside a comparison) that Joern chains reaching
                          // definitions through: not a write, so look through it.
                          backward(List(e).collect { case i: Identifier => i }, depth)
                        }
                      case _ =>
                    }
                  }
                }
              }
            }

            backward(anchor.operands, maxDepth)

            val sortedDeps = deps.toList.distinct.sortBy(d => (d._2, d._1))
            evidence("backward") = sortedDeps.take(maxNodes).map { case (line, file, vn, code, depVars, kind) =>
              Map("filename" -> file, "line" -> line, "variable" -> vn, "code" -> clip(code),
                  "depends_on" -> depVars, "kind" -> kind) }
            evidence("backward_total") = sortedDeps.size
            output.append(s"\n[BACKWARD SLICE] (${sortedDeps.size} data dependencies, reaching definitions)\n")
            if (sortedDeps.nonEmpty) {
              output.append("\n  Data Dependencies:\n")
              sortedDeps.take(maxNodes).groupBy(_._2).toList.sortBy(_._1).foreach { case (file, ds) =>
                output.append(s"  File: $file\n")
                ds.sortBy(_._1).foreach { case (line, _, vn, code, depVars, kind) =>
                  val lineInfo = if (line != -1) s"[$file:$line]" else "[Local]"
                  val tag = if (kind == "candidate") " [CANDIDATE]" else ""
                  output.append(s"    $lineInfo $vn: ${clip(code)}$tag\n")
                  if (depVars.nonEmpty) output.append(s"      <- depends on: ${depVars.mkString(", ")}\n")
                }
              }
              if (sortedDeps.size > maxNodes)
                output.append(
                  s"  ... TRUNCATED: ${sortedDeps.size - maxNodes} more data dependencies omitted " +
                  s"(showing $maxNodes of ${sortedDeps.size}). Narrow with a smaller max_depth or a more specific line.\n")
            }

            if (includeControlFlow) {
              // Control dependence (CDG): only conditions that decide whether the
              // anchor executes, not every earlier branch in the method.
              val controllers = anchor.node.controlledBy.l
                .map(c => (lineOf(c), fileOf(c), c.code)).distinct.sortBy(_._1).take(30)
              evidence("control") = controllers.map { case (line, file, cond) =>
                Map("filename" -> file, "line" -> line, "condition" -> clip(cond)) }
              output.append("\n  Control Dependencies (Target Method):\n")
              if (controllers.isEmpty) output.append("    (none: the anchor runs on every path through the method)\n")
              controllers.foreach { case (line, file, cond) =>
                output.append(s"    [$file:$line] ${clip(cond)}\n")
              }
            }

            val params = method.parameter.filter(p => anchorVars.contains(p.name)).l
            if (params.nonEmpty)
              output.append(s"\n  Parameters: ${params.map(p => s"${p.name} (${p.typeFullName})").mkString(", ")}\n")
          }

          // === FORWARD SLICE: uses reached by the anchor's definitions ===
          if (includeForward) {
            // The values the anchor defines: the target of the assignment that
            // contains it (x = read_value()), or the assignment's own target.
            val definedAt: List[Identifier] = statementOf(anchor.node) match {
              case c: Call if isAssignmentOp(c.name) => c.argument.filter(_.argumentIndex == 1).ast.isIdentifier.l
              case _ => Nil
            }
            val seenDefs = mutable.Set[Long]()
            // (line, file, kind, variable, code)
            val props = mutable.ListBuffer[(Int, String, String, String, String)]()
            val reachedUses = mutable.Set[Long]()

            def usesOf(d: StoredNode, m: M): List[Identifier] =
              m.ast.isIdentifier.filter(i => i.ddgIn.l.exists(_.id == d.id)).l

            def forward(defs: List[CfgNode], depth: Int): Unit = {
              if (depth <= 0) return
              defs.foreach { d =>
                if (!seenDefs.contains(d.id)) {
                  seenDefs.add(d.id)
                  val m = d.method
                  val varName = d match { case p: MethodParameterIn => p.name; case other => other.code }
                  usesOf(d, m).foreach { use =>
                    reachedUses.add(use.id)
                    val stmt = statementOf(use)
                    stmt match {
                      case c: Call if isAssignmentOp(c.name) && !c.argument.filter(_.argumentIndex == 1).ast.isIdentifier.l.exists(_.id == use.id) =>
                        props += ((lineOf(c), fileOf(c), "propagation", varName, c.code))
                        forward(c.argument.filter(_.argumentIndex == 1).ast.isIdentifier.l, depth - 1)
                      case c: Call =>
                        props += ((lineOf(c), fileOf(c), "usage", varName, c.code))
                      case other =>
                        props += ((lineOf(other), fileOf(other), "usage", varName, other.code))
                    }
                    // Passed as an argument to a resolved callee: continue at its parameter.
                    use.astParent match {
                      case call: Call if !call.name.startsWith("<operator>") =>
                        call.callee.l.foreach { callee =>
                          callee.parameter.filter(_.index == use.argumentIndex).l.foreach { param =>
                            props += ((lineOf(callee), fileOf(callee), "passed_to_func", varName,
                              s"Passed to ${callee.name} as ${param.name}"))
                            forward(List(param), depth - 1)
                          }
                        }
                      case _ =>
                    }
                  }
                }
              }
            }

            forward(definedAt, maxDepth)

            val sortedProps = props.toList.distinct.sortBy(p => (p._2, p._1))
            evidence("forward") = sortedProps.take(maxNodes).map { case (line, file, kind, vn, code) =>
              Map("filename" -> file, "line" -> line, "kind" -> kind, "variable" -> vn, "code" -> clip(code)) }
            evidence("forward_total") = sortedProps.size
            evidence("stored_in") = definedAt.map(_.code).distinct
            output.append(s"\n[FORWARD SLICE] (${sortedProps.size} propagations, uses reached by the anchor's value)\n")
            if (definedAt.nonEmpty) output.append(s"  Result stored in: ${definedAt.map(_.code).distinct.mkString(", ")}\n")
            if (sortedProps.nonEmpty) {
              output.append("\n  Propagations:\n")
              sortedProps.take(maxNodes).groupBy(_._2).toList.sortBy(_._1).foreach { case (file, ps) =>
                output.append(s"  File: $file\n")
                ps.sortBy(_._1).foreach { case (line, _, kind, vn, code) =>
                  output.append(s"    [$file:$line] $kind ($vn): ${clip(code)}\n")
                }
              }
              if (sortedProps.size > maxNodes)
                output.append(
                  s"  ... TRUNCATED: ${sortedProps.size - maxNodes} more propagations omitted " +
                  s"(showing $maxNodes of ${sortedProps.size}). Narrow with a smaller max_depth.\n")
            }

            if (includeControlFlow) {
              val affected = method.controlStructure
                .filter(cs => cs.condition.ast.isIdentifier.l.exists(i => reachedUses.contains(i.id)))
                .map(cs => (lineOf(cs), fileOf(cs), cs.controlStructureType, cs.condition.code.headOption.getOrElse("")))
                .l.distinct.sortBy(_._1).take(20)
              evidence("affected_control") = affected.map { case (line, file, ctrlType, cond) =>
                Map("filename" -> file, "line" -> line, "type" -> ctrlType, "condition" -> cond) }
              if (affected.nonEmpty) {
                output.append("\n  Control Flow Affected (Target Method):\n")
                affected.foreach { case (line, file, ctrlType, cond) =>
                  output.append(s"    [$file:$line] $ctrlType: $cond\n")
                }
              }
            }
          }
        }

        case None => {
          val callsOnLine = method.call.filter(c => c.lineNumber.getOrElse(-1) == lineNum).l
          val callNames = callsOnLine.map(_.name).filterNot(_.startsWith("<operator>")).distinct
          if (callName.nonEmpty)
            errorOut.append(s"ANCHOR_NOT_FOUND: no call to '$callName' on line $lineNum in method ${method.name}\n")
          else
            errorOut.append(s"ANCHOR_NOT_FOUND: no call, assignment, or condition on or within 3 lines of line $lineNum in method ${method.name}\n")
          if (callNames.nonEmpty) errorOut.append(s"Calls on line $lineNum: ${callNames.mkString(", ")}\n")
          val nearbyLines = method.call.lineNumber.l.filter(l => Math.abs(l - lineNum) <= 5).distinct.sorted
          if (nearbyLines.nonEmpty) errorOut.append(s"Nearby lines with calls: ${nearbyLines.mkString(", ")}\n")
        }
      }
    }

    case None => {
      val matchingFiles = cpg.file.name.l.filter(f => f.contains(filename) || filename.split("/").lastOption.exists(f.endsWith(_))).distinct.take(10)
      val methodsInFile = cpg.method.filter(m => normalizeFilename(m.file.name.headOption.getOrElse(""), filename)).l.take(10)
      errorOut.append(s"ANCHOR_NOT_FOUND: no method contains line $lineNum in '$filename'\n")
      if (matchingFiles.nonEmpty) {
        errorOut.append(s"Matching files in CPG:\n")
        matchingFiles.foreach(f => errorOut.append(s"  - $f\n"))
      }
      if (methodsInFile.nonEmpty) {
        errorOut.append(s"Methods in matching file(s):\n")
        methodsInFile.foreach(m => errorOut.append(s"  - ${m.name}: lines ${m.lineNumber.getOrElse(-1)}-${m.lineNumberEnd.getOrElse(-1)}\n"))
      }
      if (matchingFiles.isEmpty && methodsInFile.isEmpty) {
        errorOut.append(s"Sample files in CPG (first 5):\n")
        cpg.file.name.l.distinct.take(5).foreach(f => errorOut.append(s"  - $f\n"))
      }
    }
  }

  if (errorOut.nonEmpty) "<codebadger_result>\n" + errorOut.toString() + "</codebadger_result>"
  else {
    val backwardTotal = evidence.getOrElse("backward_total", 0).asInstanceOf[Int]
    val forwardTotal = evidence.getOrElse("forward_total", 0).asInstanceOf[Int]
    evidence("truncated") = backwardTotal > maxNodes || forwardTotal > maxNodes
    evidence("warnings") = warnings.toList
    evidence("summary") = output.toString()
    "<codebadger_result>\n" + List(evidence.toMap).toJson + "\n</codebadger_result>"
  }
}
