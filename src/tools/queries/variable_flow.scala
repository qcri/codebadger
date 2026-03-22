{
  val targetLine = {{line_num}}
  val targetVar = "{{variable}}"
  val filename = "{{filename}}"
  val direction = "{{direction}}"
  val maxResults = 50

  val targetMethodOpt = cpg.method
    .filter(m => {
      val f = m.file.name.headOption.getOrElse("")
      f.endsWith(filename) || f.contains(filename)
    })
    .filterNot(_.name == "<global>")
    .filter(m => {
      val start = m.lineNumber.getOrElse(-1)
      val end = m.lineNumberEnd.getOrElse(-1)
      start <= targetLine && end >= targetLine
    })
    .headOption

  val result = targetMethodOpt match {
    case Some(method) => {
      val sb = new StringBuilder
      val methodName = method.name
      val methodFile = method.file.name.headOption.getOrElse("unknown")
      
      sb.append(s"Variable Flow Analysis\n")
      sb.append(s"======================\n")
      sb.append(s"Target: variable '$targetVar' at $filename:$targetLine\n")
      sb.append(s"Method: $methodName\n")
      sb.append(s"Direction: $direction\n")

      // 1. Identify Aliases
      // Find local variables that are assigned the address of targetVar (e.g. p = &x)
      val pointerAliases = method.assignment
        .filter(_.source.code.contains("&" + targetVar))
        .map(_.target.code)
        .l.distinct

      // Combined set of variables to track (target + aliases)
      val monitoredVars = (targetVar :: pointerAliases).distinct

      if (pointerAliases.nonEmpty) {
        sb.append(s"Aliases detected: ${pointerAliases.mkString(", ")}\n")
      }

      sb.append("\nDependencies:\n")

      // Helper to match code against a set of tracked variables
      def isRelevantVar(code: String, vars: List[String]): Boolean = {
        vars.exists(v =>
          code == v ||
          code.startsWith(v + ".") ||
          code.startsWith(v + "[") ||
          code.startsWith("*" + v) ||
          code.startsWith(v + "->") ||
          code == "&" + v
        )
      }

      val dependencies = scala.collection.mutable.ListBuffer[(String, Int, String, String)]()
      val visited = scala.collection.mutable.Set[String]()

      def trace(currMethod: io.shiftleft.codepropertygraph.generated.nodes.Method,
                currVar: String,
                trackedVars: List[String],
                scopeLine: Int,
                depth: Int): Unit = {

        val methodId = currMethod.fullName
        val uniqueId = s"$methodId:$currVar"
        if (depth > 5 || visited.contains(uniqueId)) return
        visited.add(uniqueId)

        val currFile = currMethod.file.name.headOption.getOrElse("unknown")

        if (direction == "backward") {
          // 0. Parameters (Inter-procedural)
          currMethod.parameter.nameExact(currVar).l.foreach { param =>
             if (depth == 0) dependencies += ((currFile, param.lineNumber.getOrElse(-1), s"${param.typeFullName} ${param.name}", "parameter"))

             currMethod.callIn.foreach { call =>
                 val caller = call.method
                 val callFile = call.file.name.headOption.getOrElse("unknown")
                 call.argument.filter(_.argumentIndex == param.order).foreach { arg =>
                     val argLine = call.lineNumber.getOrElse(-1)
                     val argCode = arg.code
                     val argIdentifiers = arg.ast.isIdentifier.name.l.distinct
                     if (argIdentifiers.nonEmpty) {
                         dependencies += ((callFile, argLine, s"Passed '$argCode' to ${currMethod.name}", "call_site_arg"))
                         argIdentifiers.foreach(argId => trace(caller, argId, List(argId), argLine, depth + 1))
                     } else {
                         dependencies += ((callFile, argLine, s"Passed '$argCode' to ${currMethod.name}", "call_site_const"))
                     }
                 }
             }
          }

          // 1. Initializations
          currMethod.local.nameExact(currVar).l.foreach { local =>
            dependencies += ((currFile, local.lineNumber.getOrElse(-1), s"${local.typeFullName} ${local.code}", "initialization"))
          }

          // 2. Assignments
          currMethod.assignment
            .filter(_.lineNumber.getOrElse(-1) <= scopeLine)
            .filter(a => isRelevantVar(a.target.code, trackedVars))
            .take(maxResults)
            .foreach { assign =>
               dependencies += ((currFile, assign.lineNumber.getOrElse(-1), assign.code, "assignment"))
            }

          // 3. Modifications
          currMethod.call
            .name("<operator>.(postIncrement|preIncrement|postDecrement|preDecrement|assignmentPlus|assignmentMinus|assignmentMultiplication|assignmentDivision)")
            .filter(_.lineNumber.getOrElse(-1) <= scopeLine)
            .filter(c => c.argument.code.l.exists(arg => isRelevantVar(arg, trackedVars)))
            .take(maxResults)
            .foreach { call =>
              dependencies += ((currFile, call.lineNumber.getOrElse(-1), call.code, "modification"))
            }

          // 4. Function Calls
          currMethod.call
            .filter(_.lineNumber.getOrElse(-1) <= scopeLine)
            .filter(c => c.argument.code.l.exists(arg => trackedVars.exists(v => arg.contains(v))))
            .take(maxResults)
            .foreach { call =>
               if (!call.name.startsWith("<operator>"))
                  dependencies += ((currFile, call.lineNumber.getOrElse(-1), call.code, "function_call"))
            }

        } else { // forward
          // 1. Usages
          currMethod.call
            .filter(_.lineNumber.getOrElse(-1) >= scopeLine)
            .filter(c => c.argument.code.l.exists(arg => isRelevantVar(arg, trackedVars) || trackedVars.exists(v => arg.contains(v))))
            .take(maxResults)
            .foreach { call =>
               dependencies += ((currFile, call.lineNumber.getOrElse(-1), call.code, "usage"))
            }

          // 2. Propagations
          currMethod.assignment
            .filter(_.lineNumber.getOrElse(-1) >= scopeLine)
            .filter(a => isRelevantVar(a.source.code, trackedVars) || trackedVars.exists(v => a.source.code.contains(v)))
            .take(maxResults)
            .foreach { assign =>
               dependencies += ((currFile, assign.lineNumber.getOrElse(-1), assign.code, "propagation"))
            }
        }
      }

      trace(method, targetVar, monitoredVars, targetLine, 0)

      val sortedDeps = dependencies.sortBy(_._2) // Sort by line
      if (sortedDeps.isEmpty) {
        sb.append("(No dependencies found)\n")
      } else {
        // Deduplicate output
        val uniqueDeps = sortedDeps.distinct
        uniqueDeps.foreach { case (file, line, code, typeName) =>
          sb.append(s"[$file:$line] $code ($typeName)\n")
        }
      }
      
      sb.toString()
    }
    case None => {
      s"Error: No method found containing line $targetLine in file '$filename'"
    }
  }

  "<codebadger_result>\n" + result + "\n</codebadger_result>"
}
