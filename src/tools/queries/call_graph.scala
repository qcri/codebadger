{
  val methodName = "{{method_name}}"
  val maxDepth = {{depth}}
  val direction = "{{direction}}"
  val maxResults = 500

  val output = new StringBuilder()
  
  val rootMethodOpt = cpg.method.name(methodName).headOption

  rootMethodOpt match {
    case Some(rootMethod) => {
      val rootName = rootMethod.name
      val rootFile = rootMethod.file.name.headOption.getOrElse("unknown")
      val rootLine = rootMethod.lineNumber.getOrElse(-1)
      
      // Header
      output.append(s"Call Graph for $rootName ($direction)\n")
      output.append("=" * 60 + "\n")
      output.append(s"Root: $rootName at $rootFile:$rootLine\n")
      
      // BFS helper: returns (edgesByDepth, totalEdges)
      // Each edge is (fromName, toName, toFile, toLine, isCycle)
      def bfsEdges(
        seed: io.shiftleft.codepropertygraph.generated.nodes.Method,
        seedDepth: Int,
        nextNodes: io.shiftleft.codepropertygraph.generated.nodes.Method =>
                   List[io.shiftleft.codepropertygraph.generated.nodes.Method],
        edgeDir: (String, String) => (String, String)   // (from, to) ordering
      ): (scala.collection.mutable.Map[Int, scala.collection.mutable.ListBuffer[(String, String, String, Int, Boolean)]], Int) = {

        val toVisit     = scala.collection.mutable.Queue[(io.shiftleft.codepropertygraph.generated.nodes.Method, Int)]()
        var visited     = Set[String]()
        var edgesVisited= Set[(String, String)]()
        val edgesByDepth= scala.collection.mutable.Map[Int, scala.collection.mutable.ListBuffer[(String, String, String, Int, Boolean)]]()

        toVisit.enqueue((seed, seedDepth))
        var totalEdges = 0

        while (toVisit.nonEmpty && totalEdges < maxResults) {
          val (current, currentDepth) = toVisit.dequeue()
          val currentName = current.name

          if (!visited.contains(currentName) && currentDepth < maxDepth) {
            visited = visited + currentName

            for (neighbor <- nextNodes(current).take(50)) {
              val neighborName = neighbor.name
              val neighborFile = neighbor.file.name.headOption.getOrElse("unknown")
              val neighborLine = neighbor.lineNumber.getOrElse(-1)
              val depth        = currentDepth + 1
              val (eFrom, eTo) = edgeDir(currentName, neighborName)
              val edgeKey      = (eFrom, eTo)
              val isCycle      = visited.contains(neighborName)

              if (!edgesVisited.contains(edgeKey)) {
                edgesVisited = edgesVisited + edgeKey
                totalEdges  += 1
                if (!edgesByDepth.contains(depth))
                  edgesByDepth(depth) = scala.collection.mutable.ListBuffer()
                // Store (from, to) always as caller->callee for display
                edgesByDepth(depth) += ((eFrom, eTo, neighborFile, neighborLine, isCycle))
                if (!isCycle && depth < maxDepth)
                  toVisit.enqueue((neighbor, depth))
              }
            }
          }
        }
        (edgesByDepth, totalEdges)
      }

      def printEdges(
        edgesByDepth: scala.collection.mutable.Map[Int, scala.collection.mutable.ListBuffer[(String, String, String, Int, Boolean)]],
        totalEdges: Int
      ): Unit = {
        val cycleCount = edgesByDepth.values.flatten.count(_._5)
        for (depth <- edgesByDepth.keys.toList.sorted) {
          output.append(s"\n[DEPTH $depth]\n")
          for ((from, to, file, line, isCycle) <- edgesByDepth(depth)) {
            val location = if (line > 0) s"$file:$line" else file
            val tag = if (isCycle) " [CYCLE]" else ""
            output.append(s"  $from → $to ($location)$tag\n")
          }
        }
        output.append(s"\nTotal: $totalEdges edges")
        if (cycleCount > 0) output.append(s" ($cycleCount cyclic)")
        output.append("\n")
      }

      if (direction == "outgoing") {
        val (edgesByDepth, totalEdges) = bfsEdges(
          rootMethod,
          0,
          m => m.call.callee.l.filterNot(_.name.startsWith("<operator>")),
          (cur, nei) => (cur, nei)   // from=caller, to=callee
        )
        printEdges(edgesByDepth, totalEdges)

      } else if (direction == "incoming") {
        val (edgesByDepth, totalEdges) = bfsEdges(
          rootMethod,
          0,
          m => m.caller.l.filterNot(_.name.startsWith("<operator>")),
          (cur, nei) => (nei, cur)   // from=caller(neighbor), to=callee(current)
        )
        printEdges(edgesByDepth, totalEdges)

      } else {
        output.append(s"ERROR: Direction must be 'outgoing' or 'incoming', got: '$direction'\n")
      }
    }
    case None => {
      output.append(s"ERROR: Method not found: $methodName\n")
      
      // Show similar method names as suggestions
      val similar = cpg.method.name(s".*$methodName.*").name.l.distinct.take(10)
      if (similar.nonEmpty) {
        output.append(s"\nDid you mean one of these?\n")
        similar.foreach(m => output.append(s"  - $m\n"))
      }
    }
  }
  
  // Return with markers for easy extraction
  "<codebadger_result>\n" + output.toString() + "</codebadger_result>"
}
