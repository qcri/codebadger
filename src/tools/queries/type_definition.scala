cpg.typeDecl
  .name("{{type_name}}")
  .filter(_.member.nonEmpty)
  .l
  .groupBy(t => t.fullName.replaceAll("<duplicate>[0-9]+$", ""))
  .values
  .map { variants =>
    variants.toSeq.sortBy { t =>
      val isDuplicate = if (t.fullName.matches(".*<duplicate>[0-9]+$")) 1 else 0
      (isDuplicate, -t.member.size)
    }.head
  }
  .take({{limit}})
  .map { t =>
  Map(
    "_1" -> t.name,
    "_2" -> t.fullName.replaceAll("<duplicate>[0-9]+$", ""),
    "_3" -> t.file.name.headOption.getOrElse("unknown"),
    "_4" -> t.lineNumber.getOrElse(-1),
    "_5" -> t.member.take(20).map(m => Map("name" -> m.name, "type" -> m.typeFullName)).l
  )
}.toJsonPretty
