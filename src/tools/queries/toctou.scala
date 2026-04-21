{
  import io.shiftleft.codepropertygraph.generated.nodes._
  import io.shiftleft.semanticcpg.language._
  import scala.collection.mutable

  val fileFilter = "{{filename}}"
  val maxResults = {{limit}}

  val output = new StringBuilder()

  def pathBoundaryRegex(f: String): String = {
    val escaped = java.util.regex.Pattern.quote(f)
    "(^|.*/)" + escaped + "$"
  }

  output.append("TOCTOU (Time-of-Check-Time-of-Use) Analysis\n")
  output.append("=" * 60 + "\n\n")

  // Check functions (CWE-367): functions that inspect a path without opening it
  val checkFuncs = Set("access", "stat", "lstat", "fstat", "faccessat", "euidaccess",
    "eaccess", "statx", "access64", "stat64", "lstat64")

  // Use functions: functions that act on a path after a prior check
  val useFuncs = Set("open", "fopen", "freopen", "creat", "openat", "open64", "fopen64",
    "rename", "unlink", "unlinkat", "rmdir", "mkdir", "mkdirat", "chmod", "chown",
    "lchown", "fchmodat", "fchownat", "execve", "execvp", "execvpe", "execl", "execlp",
    "link", "linkat", "symlink", "symlinkat", "truncate", "truncate64")

  val checkPattern = checkFuncs.mkString("|")
  val usePattern   = useFuncs.mkString("|")

  // Collect all methods that contain at least one check call
  val methodsWithChecks = cpg.call.name(checkPattern).method.dedup.l

  val filteredMethods = if (fileFilter.nonEmpty) {
    val pattern = pathBoundaryRegex(fileFilter)
    methodsWithChecks.filter(m => m.file.name.headOption.exists(_.matches(pattern)))
  } else methodsWithChecks

  if (filteredMethods.isEmpty) {
    output.append("No calls to file-check functions (access, stat, lstat, …) found.\n")
  } else {
    output.append(s"Found ${filteredMethods.size} method(s) containing file-check calls. Analyzing for TOCTOU...\n\n")

    // (file, method, checkLine, checkCode, checkPathArg, useLine, useCode, usePathArg)
    val issues = mutable.ListBuffer[(String, String, Int, String, String, Int, String, String)]()

    filteredMethods.foreach { method =>
      val methName = method.name
      val methFile = method.file.name.headOption.getOrElse("unknown")

      // All check calls in this method, keyed by the path argument
      val checkCalls = method.call.name(checkPattern).l.flatMap { chk =>
        // First argument is the path
        val pathArg = chk.argument.order(1).l.headOption.map(_.code.trim).getOrElse("")
        if (pathArg.nonEmpty) Some((chk, pathArg)) else None
      }

      // All use calls in this method
      val useCalls = method.call.name(usePattern).l.flatMap { use =>
        val pathArg = use.argument.order(1).l.headOption.map(_.code.trim).getOrElse("")
        if (pathArg.nonEmpty) Some((use, pathArg)) else None
      }

      checkCalls.foreach { case (chkCall, chkPath) =>
        val chkLine = chkCall.lineNumber.getOrElse(-1)

        useCalls.foreach { case (useCall, usePath) =>
          val useLine = useCall.lineNumber.getOrElse(-1)

          // Must be: check appears before use on a feasible path (line order heuristic)
          if (chkLine > 0 && useLine > 0 && chkLine < useLine) {
            // Path arguments must refer to the same value:
            // exact match OR the use path argument is derived from the check path
            // (e.g. check uses `path`, open uses `path` or a variable holding `path`)
            val sameTarget = chkPath == usePath || usePath.startsWith(chkPath)

            if (sameTarget) {
              issues += ((methFile, methName, chkLine, chkCall.code, chkPath,
                          useLine, useCall.code, usePath))
            }
          }
        }
      }
    }

    val dedupIssues = issues.toList.distinct

    if (dedupIssues.isEmpty) {
      output.append("No TOCTOU patterns detected.\n")
      output.append("\nNote: This analysis looks for:\n")
      output.append("  - A call to access()/stat()/lstat() (or similar) followed by open()/fopen()\n")
      output.append("    (or another file-operation call) on the same path argument\n")
      output.append("  - Both calls must appear in the same function\n")
      output.append("  - The check must textually precede the use (line-number order)\n")
    } else {
      output.append(s"Found ${dedupIssues.size} potential TOCTOU issue(s):\n\n")

      dedupIssues.take(maxResults).zipWithIndex.foreach { case ((file, meth, chkLine, chkCode, chkPath, useLine, useCode, usePath), idx) =>
        val chkSnippet = if (chkCode.length > 70) chkCode.take(67) + "..." else chkCode
        val useSnippet = if (useCode.length > 70) useCode.take(67) + "..." else useCode

        output.append(s"--- Issue ${idx + 1} ---\n")
        output.append(s"Confidence:   HIGH\n")
        output.append(s"CWE:          CWE-367 (Use of Device File in Sensitive Operation)\n")
        output.append(s"Function:     $meth()  [$file]\n")
        output.append(s"Path arg:     $chkPath\n")
        output.append(s"\n  CHECK  [$file:$chkLine]  $chkSnippet\n")
        output.append(s"  USE    [$file:$useLine]  $useSnippet\n")
        output.append(s"\n  Window: ${useLine - chkLine} line(s) between check and use\n")
        output.append(s"  Risk:   An attacker may replace/symlink the file between the check and\n")
        output.append(s"          the subsequent operation, bypassing the access control decision.\n")
        output.append("\n")
      }

      if (dedupIssues.size > maxResults)
        output.append(s"(Showing $maxResults of ${dedupIssues.size} issues. Use limit parameter to see more.)\n\n")

      output.append(s"Total: ${dedupIssues.size} potential TOCTOU issue(s) found\n")
    }
  }

  "<codebadger_result>\n" + output.toString() + "</codebadger_result>"
}
