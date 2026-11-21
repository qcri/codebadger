{
  def canonical(fullName: String): String = fullName.replaceAll("<duplicate>[0-9]+$", "")
  // `struct Opaque;` declares a name without a body; it is listed, but a definition
  // of the same type wins over it.
  def isForward(t: io.shiftleft.codepropertygraph.generated.nodes.TypeDecl): Boolean =
    t.member.isEmpty && !t.code.contains("{") && t.code.trim.matches("(struct|union|enum|class)\\s+\\S+")

  // Internal declarations only: external stubs (int, Foo*) and the per-function
  // and per-file type declarations the C frontend emits are not user types.
  val candidates = cpg.typeDecl.name("{{type_name}}").isExternal(false).filterNot(_.name == "<global>").l
  val methodNames = if (candidates.isEmpty) Set.empty[String] else cpg.method.fullName.toSet
  val types = candidates
    .filterNot(t => methodNames.contains(t.fullName))
    .groupBy(t => canonical(t.fullName))
    .values
    .map { variants =>
      variants.sortBy { t =>
        val isDuplicate = if (t.fullName.matches(".*<duplicate>[0-9]+$")) 1 else 0
        (isDuplicate, if (isForward(t)) 1 else 0, -t.member.size)
      }.head
    }
    .toList
    .sortBy(t => (t.filename, t.lineNumber.getOrElse(-1), t.fullName))

  val result = Map(
    "total" -> types.size,
    "types" -> types.take({{limit}}).map { t =>
      val members = t.member.l.sortBy(_.order)
      Map(
        "name" -> t.name,
        "fullName" -> canonical(t.fullName),
        "filename" -> t.filename,
        "lineNumber" -> t.lineNumber.getOrElse(-1),
        "aliasOf" -> t.aliasTypeFullName.getOrElse(""),
        "forward_declaration" -> isForward(t),
        "member_total" -> members.size,
        "members" -> members.take({{member_limit}}).map(m => Map("name" -> m.name, "type" -> m.typeFullName))
      )
    }
  )
  "<codebadger_result>\n" + List(result).toJson + "\n</codebadger_result>"
}
