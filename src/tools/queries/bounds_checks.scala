{
  val filename = "{{filename}}"
  val lineNum = {{line_num}}
  val output = new StringBuilder()

  // --- Helper Functions ---

  def extractVariable(expr: String): String = {
    Option(expr).getOrElse("").replaceAll("[^a-zA-Z0-9_].*", "").trim
  }

  // --- Target Identification ---

  // 1. Buffer Access (buf[i])
  val bufferAccessOpt = cpg.call
    .name("<operator>.indirectIndexAccess")
    .filter(c => {
      val f = c.file.name.headOption.getOrElse("")
      f.endsWith("/" + filename) || f == filename
    })
    .filter(c => c.lineNumber.getOrElse(-1) == lineNum)
    .l.headOption

  // 2. Memory Copy / String Copy (memcpy, strncpy)
  val memCopyOpt = cpg.call
    .name("memcpy", "strncpy", "memmove")
    .filter(c => {
      val f = c.file.name.headOption.getOrElse("")
      f.endsWith("/" + filename) || f == filename
    })
    .filter(c => c.lineNumber.getOrElse(-1) == lineNum)
    .l.headOption

   // 3. Malloc (malloc(size))
   val mallocOpt = cpg.call
    .name("malloc", "calloc", "realloc")
     .filter(c => {
      val f = c.file.name.headOption.getOrElse("")
      f.endsWith("/" + filename) || f == filename
    })
    .filter(c => c.lineNumber.getOrElse(-1) == lineNum)
    .l.headOption


  // --- Analysis Logic ---

  var foundTarget = false

  // Handle Buffer Access
  bufferAccessOpt.foreach(ba => {
    foundTarget = true
    val method = ba.method
    val args = ba.argument.l
    val bufferArg = args.find(_.order == 1)
    val indexArg = args.find(_.order == 2)
    val bufferName = bufferArg.map(_.code).getOrElse("unknown")
    val indexExpr = indexArg.map(_.code).getOrElse("unknown")
    val indexVar = extractVariable(indexExpr)

    output.append(s"Bounds Check Analysis for $filename:$lineNum\n")
    output.append("=" * 60 + "\n")
    output.append(s"Type:          Buffer Access (buf[i])\n")
    output.append(s"Operation:     ${ba.code}\n")
    output.append(s"Buffer:        $bufferName\n")
    output.append(s"Index:         $indexExpr (Variable: $indexVar)\n\n")

    analyzeChecks(method, ba, indexVar, output)
  })
  
  // Handle Memcpy/Strncpy
  if (!foundTarget) {
      memCopyOpt.foreach(mc => {
        foundTarget = true
        val method = mc.method
        val args = mc.argument.l
        // memcpy(dst, src, len) -> len is 3rd arg
        val lenArg = args.find(_.order == 3)
        val dstArg = args.find(_.order == 1)
        
        val lenExpr = lenArg.map(_.code).getOrElse("unknown")
        val dstName = dstArg.map(_.code).getOrElse("unknown")
        val lenVar = extractVariable(lenExpr)

        output.append(s"Bounds Check Analysis for $filename:$lineNum\n")
        output.append("=" * 60 + "\n")
        output.append(s"Type:          Memory/String Copy\n")
        output.append(s"Operation:     ${mc.code}\n")
        output.append(s"Destination:   $dstName\n")
        output.append(s"Length:        $lenExpr (Variable: $lenVar)\n\n")

        analyzeChecks(method, mc, lenVar, output)
      })
  }
  
   // Handle Malloc
  if (!foundTarget) {
      mallocOpt.foreach(ma => {
        foundTarget = true
        val method = ma.method
        val args = ma.argument.l
        // malloc(size) -> size is 1st arg. calloc(num, size) -> checks differ, assume size for now
        val sizeArg = args.find(_.order == 1) // simplifiction for malloc
        
        val sizeExpr = sizeArg.map(_.code).getOrElse("unknown")
        val sizeVar = extractVariable(sizeExpr)

        output.append(s"Bounds Check Analysis for $filename:$lineNum\n")
        output.append("=" * 60 + "\n")
        output.append(s"Type:          Memory Allocation\n")
        output.append(s"Operation:     ${ma.code}\n")
        output.append(s"Size:          $sizeExpr (Variable: $sizeVar)\n\n")

        analyzeChecks(method, ma, sizeVar, output)
      })
  }

  if (!foundTarget) {
     output.append(s"ERROR: No supported operation found at $filename:$lineNum\n")
     output.append("Supported: buf[i], memcpy/strncpy(dst, src, len), malloc(size)\n")
  }


  // --- Common Analysis Method ---
  
  def analyzeChecks(method: Method, targetCall: Call, variable: String, out: StringBuilder): Unit = {
      
      // 1. Control Dependence Checks
      // Use Joern steps to filter for Calls and then filter by name
      val guardingChecks = targetCall.controlledBy.isCall.filter(c => {
         c.code.contains(variable) && 
         (c.name.contains("less") || c.name.contains("greater") || c.name.contains("quals") || c.name.contains("otEquals"))
      }).l
      
      out.append("LOCAL CHECKS\n")
      out.append("-" * 30 + "\n")
      
      var guarded = false
      
      if (guardingChecks.isEmpty) {
         out.append("  None found.\n")
      } else {
         guardingChecks.foreach(cmp => {
             // Restart traversal from cmp to use .astParent steps
             val isLoop = cmp.start.astParent.isControlStructure.controlStructureType.l.exists(t => t == "WHILE" || t == "FOR" || t == "DO")
             val typeLabel = if (isLoop) "[LOOP GUARD]" else "[GUARD]"
             guarded = true
             out.append(f"  Line ${cmp.lineNumber.getOrElse(0)}%-4d: ${cmp.code}%-20s | $typeLabel\n")
         })
      }
      out.append("\n")

      // 2. Inter-procedural Checks
      val paramOpt = method.parameter.filter(_.name == variable).l.headOption
      out.append("INTER-PROCEDURAL CHECKS\n")
      out.append("-" * 30 + "\n")
      
      paramOpt match {
        case Some(pNode) =>
          val pIndex = pNode.order
          val callSites = cpg.call.methodFullNameExact(method.fullName).l
          
          if (callSites.isEmpty) {
            out.append("  No callers found.\n")
          } else {
            var foundInter = false
            callSites.foreach(callSite => {
              val callerMethod = callSite.method
              val argAtCallSite = callSite.argument.l.find(_.order == pIndex)
              
              argAtCallSite.foreach(arg => {
                val argVarInCaller = extractVariable(arg.code)
                // Check controllers for the call site!
                val callerGuards = callSite.controlledBy.isCall.filter(c => {
                    c.code.contains(argVarInCaller) && 
                    (c.name.contains("less") || c.name.contains("greater") || c.name.contains("quals") || c.name.contains("otEquals"))
                }).l
                
                if (callerGuards.nonEmpty) {
                   foundInter = true
                   guarded = true
                   out.append(s"  In Caller: ${callerMethod.name} (${callerMethod.file.name.headOption.getOrElse("?")})\n")
                   callerGuards.foreach(cmp => {
                     out.append(f"    Line ${cmp.lineNumber.getOrElse(0)}%-4d: ${cmp.code}\n")
                   })
                }
              })
            })
            if (!foundInter) out.append("  None found in callers.\n")
          }
        case None =>
          out.append("  N/A (Variable is not a method parameter).\n")
      }

      out.append("\nSUMMARY\n")
      out.append("-" * 30 + "\n")
      out.append(s"Overall Status: ${if (guarded) "GUARDED" else "UNGUARDED OR INSUFFICIENT CHECKS"}\n")
  }

  "<codebadger_result>\n" + output.toString() + "</codebadger_result>"
}
