import re


NAV_SOFT_LIMIT = 800


def _remove_source_trace_section(text: str) -> str:
    """删除标题为“来源追溯”的章节及其全部下级内容。"""
    lines = (text or "").splitlines()
    out = []
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()
        match = re.match(r"^(#{1,6})\s*来源追溯\s*$", stripped)

        if not match:
            out.append(lines[i])
            i += 1
            continue

        level = len(match.group(1))
        i += 1

        # 跳过本节内容，直到遇到同级或更高级标题；若无则删除到文末。
        while i < len(lines):
            heading = re.match(r"^(#{1,6})\s+\S", lines[i].lstrip())
            if heading and len(heading.group(1)) <= level:
                break
            i += 1

        while out and not out[-1].strip():
            out.pop()

    return "\n".join(out)


def _is_heading_only(block: str) -> bool:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    return len(lines) == 1 and bool(re.match(r"^#{1,6}\s+\S", lines[0]))


def _is_structure_rule(text: str) -> bool:
    """识别“00 全文章节结构规则”。"""
    first_h1 = re.search(r"(?m)^#\s+(.+)$", text or "")
    if first_h1 and "全文章节结构" in first_h1.group(1):
        return True
    return bool(
        re.search(r"(?m)^##\s*标准章节树及内容规则导航\s*$", text or "")
    )


def _prepare_normal_rule_chunks(text: str) -> str:
    """普通章节规则：按审查语义单元切子块。"""
    lines = (text or "").splitlines()
    blocks = []
    current = []
    in_fence = False
    fence_mark = None

    def flush():
        nonlocal current
        if current:
            block = "\n".join(current).strip()
            if block:
                blocks.append(block)
            current = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```") or stripped.startswith("~~~"):
            mark = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_mark = mark
            elif fence_mark == mark:
                in_fence = False
                fence_mark = None
            current.append(line)
            continue

        if in_fence:
            current.append(line)
            continue

        # 普通空行只承担排版，不作为语义切分依据。
        if not stripped:
            continue

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        start_new = False

        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()

            if level == 1:
                start_new = bool(current)
            elif level == 2:
                # 两个高度重复的通用尾部说明并入前一规则子块，降低检索噪声。
                start_new = (
                    False
                    if title in {"逐项审查方式", "本章小结"}
                    else bool(current)
                )
            elif level == 3:
                # 主题 H2 与“本章应写明的要求”合并；其他 H3 独立成块。
                start_new = bool(current) and title != "本章应写明的要求"
            else:
                start_new = False

        if start_new:
            flush()

        current.append(line)

    flush()

    # 标题不能成为无正文的低价值子块；自动向后合并。
    merged = []
    pending_headings = []
    for block in blocks:
        if _is_heading_only(block):
            pending_headings.append(block)
            continue

        if pending_headings:
            block = "\n".join(pending_headings + [block])
            pending_headings = []
        merged.append(block)

    if pending_headings:
        if merged:
            merged[-1] = merged[-1] + "\n" + "\n".join(pending_headings)
        else:
            merged.extend(pending_headings)

    return "\n\n".join(merged).rstrip() + "\n"


def _table_cells(line: str):
    """解析简单 Markdown 表格行。规则导航表不允许单元格内再使用未转义竖线。"""
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _top_number(number: str) -> str:
    """5.1 -> 5；6.3 -> 6。"""
    number = (number or "").strip()
    match = re.match(r"^(\d+)", number)
    return match.group(1) if match else number


def _is_integer_text(value: str) -> bool:
    return bool(re.fullmatch(r"\d+", str(value)))


def _range_text(values) -> str:
    """[1,2,3,4] -> 1–4；非连续值使用顿号。"""
    unique = []
    for value in values:
        if value not in unique:
            unique.append(value)

    if not unique:
        return ""

    if all(_is_integer_text(value) for value in unique):
        numbers = [int(value) for value in unique]
        if (
            len(numbers) > 1
            and numbers == list(range(numbers[0], numbers[-1] + 1))
        ):
            return f"{numbers[0]}–{numbers[-1]}"

    return "、".join(unique)


def _nav_label(rows) -> str:
    parsed = []
    for row in rows:
        cells = _table_cells(row)
        if cells and len(cells) >= 2:
            parsed.append((cells[0], _top_number(cells[1])))

    parts = []
    for part, _ in parsed:
        if part not in parts:
            parts.append(part)

    if len(parts) == 1:
        tops = [top for part, top in parsed if part == parts[0]]
        return f"{parts[0]} {_range_text(tops)}".strip()

    return " / ".join(parts)


def _nav_block(header_lines, rows) -> str:
    label = _nav_label(rows)
    title = "## 标准章节树及内容规则导航"
    if label:
        title += f"｜{label}"
    return "\n".join([title] + header_lines + rows)


def _family_key(row):
    """按“部分 + 一级章节号”把 5 与 5.1~5.4 视为同一族。"""
    cells = _table_cells(row)
    if not cells or len(cells) < 2:
        return ("", "")
    return (cells[0], _top_number(cells[1]))


def _group_nav_rows(header_lines, rows, soft_limit: int = NAV_SOFT_LIMIT):
    """
    导航表分组原则：
    - 不跨“通用部分/专用部分”等部分混组；
    - 同一一级章及其二级章尽量保持完整；
    - 按约 800 字符软上限打包，避免父子分块器的最大长度兜底在表格行中间硬切；
    - 每组重新带表头，使每个子块都可独立理解。
    """
    families = []
    current_family = []
    current_key = None

    for row in rows:
        key = _family_key(row)
        if current_family and key != current_key:
            families.append(current_family)
            current_family = []
        current_family.append(row)
        current_key = key

    if current_family:
        families.append(current_family)

    groups = []
    pending = []

    def block_size(group_rows):
        return len(_nav_block(header_lines, group_rows))

    def flush_pending():
        nonlocal pending
        if pending:
            groups.append(pending)
            pending = []

    for family in families:
        family_part = _family_key(family[0])[0] if family else ""
        pending_part = _family_key(pending[0])[0] if pending else ""

        # 不跨“部分”打包。
        if pending and family_part != pending_part:
            flush_pending()

        # 极端情况下单个一级章族本身过长，则只按完整表格行拆，绝不从行中间切。
        if block_size(family) > soft_limit:
            flush_pending()
            row_group = []
            for row in family:
                if row_group and block_size(row_group + [row]) > soft_limit:
                    groups.append(row_group)
                    row_group = []
                row_group.append(row)
            if row_group:
                groups.append(row_group)
            continue

        if pending and block_size(pending + family) > soft_limit:
            flush_pending()

        pending.extend(family)

    flush_pending()
    return groups


def _prepare_structure_rule_chunks(text: str) -> str:
    """
    “00 全文章节结构规则”专用分块：
    1. H1 + 规则身份为一块；
    2. “标准章节树及内容规则导航”表按部分/一级章族分组，每块复制表头；
    3. “结构判定口径”及其后续标题约束整体保留；
    4. “执行台账”单独成块。
    """
    lines = (text or "").splitlines()

    nav_index = None
    for index, line in enumerate(lines):
        if re.match(
            r"^##\s*标准章节树及内容规则导航\s*$",
            line.strip(),
        ):
            nav_index = index
            break

    if nav_index is None:
        return _prepare_normal_rule_chunks(text)

    blocks = []

    # 文档身份块。
    pre_nav = [line.rstrip() for line in lines[:nav_index] if line.strip()]
    if pre_nav:
        blocks.append("\n".join(pre_nav).strip())

    # 找到导航表章节结束位置。
    nav_end = len(lines)
    for index in range(nav_index + 1, len(lines)):
        if re.match(r"^##\s+\S", lines[index].strip()):
            nav_end = index
            break

    nav_lines = [
        line.rstrip()
        for line in lines[nav_index + 1 : nav_end]
        if line.strip()
    ]
    table_lines = [
        line for line in nav_lines
        if line.strip().startswith("|") and line.strip().endswith("|")
    ]

    if len(table_lines) >= 3:
        header_lines = table_lines[:2]
        data_rows = table_lines[2:]
        for group in _group_nav_rows(header_lines, data_rows):
            blocks.append(_nav_block(header_lines, group))
    else:
        # 表结构异常时不丢内容，退化为完整导航章节。
        nav_block = "\n".join(
            [lines[nav_index].strip()] + nav_lines
        ).strip()
        if nav_block:
            blocks.append(nav_block)

    # 尾部：结构判定相关内容合并；执行台账单独切出。
    tail_lines = [
        line.rstrip() for line in lines[nav_end:] if line.strip()
    ]
    current = []

    for line in tail_lines:
        heading = re.match(r"^##\s+(.+?)\s*$", line.strip())
        title = heading.group(1).strip() if heading else None

        if title == "执行台账":
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            current.append(line)
        else:
            current.append(line)

    if current:
        blocks.append("\n".join(current).strip())

    return "\n\n".join(block for block in blocks if block).rstrip() + "\n"



def _line_value(text: str, field: str) -> str:
    if not text:
        return ""
    m = re.search(
        rf"(?m)^\s*[-*+]\s*{re.escape(field)}\s*[：:]\s*(.*?)\s*$",
        text,
    )
    return m.group(1).strip() if m else ""


def _first_heading(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"(?m)^\s*#\s+(.+?)\s*$", text)
    return m.group(1).strip() if m else ""


def _known_document_role(text: str) -> str:
    text = text or ""
    for role in ("采购技术规范书", "投标响应文件", "合同技术附件"):
        if role in text:
            return role
    return ""


def _clean_object_scope(value: str, role: str) -> str:
    value = (value or "").strip()
    if role and value.endswith(role):
        value = value[: -len(role)]
    value = re.sub(r"[\s｜|>]+$", "", value).strip()
    return value


def _split_section_label(label: str):
    label = (label or "").strip()
    m = re.match(r"^\s*(\d+(?:\.\d+)*)\s*[\.、:：\-—]?\s*(.*?)\s*$", label)
    if not m:
        return "", label
    return m.group(1).strip(), m.group(2).strip()



def _derive_professional_identity(text: str) -> dict:
    h1 = _first_heading(text)
    applicable_object = _line_value(text, "适用对象")
    applicable_section = _line_value(text, "适用审查章节")

    rule_kind = (_line_value(text, "规则类型") or "").strip().lower()
    if not rule_kind:
        rule_kind = "structure" if "全文章节结构" in h1 else "chapter"

    document_role = (
        _line_value(text, "文档角色")
        or _known_document_role(applicable_object)
        or _known_document_role(h1)
    )
    if not document_role:
        document_role = "未标明"

    object_scope = _line_value(text, "对象范围")
    if not object_scope:
        object_scope = _clean_object_scope(applicable_object, document_role)
    if not object_scope:
        object_scope = (h1.split("｜", 1)[0] if h1 else "").strip()

    part = _line_value(text, "所属部分")
    section_number = _line_value(text, "章节编号")
    section_title = _line_value(text, "章节标题")

    if rule_kind == "structure":
        part = part or "全文"
        section_number = section_number or "00"
        section_title = section_title or "全文章节结构"
    else:
        path_parts = [
            p.strip()
            for p in re.split(r"\s*>\s*", applicable_section or "")
            if p.strip()
        ]
        if not part and path_parts:
            part = path_parts[0]
        if not part:
            hparts = [p.strip() for p in h1.split("｜") if p.strip()]
            if len(hparts) >= 2:
                part = hparts[1]

        last = path_parts[-1] if path_parts else ""
        n, t = _split_section_label(last)
        section_number = section_number or n
        section_title = section_title or t

        if not section_number or not section_title:
            hparts = [p.strip() for p in h1.split("｜") if p.strip()]
            if len(hparts) >= 3:
                hn, ht = _split_section_label(hparts[2])
                section_number = section_number or hn
                section_title = section_title or ht

    part = (part or "").strip()
    section_number = (section_number or "").strip()
    section_title = (section_title or "").strip()
    object_scope = (object_scope or "").strip()

    missing = []
    for key, value in (
        ("对象范围", object_scope),
        ("所属部分", part),
        ("章节编号", section_number),
        ("章节标题", section_title),
        ("规则类型", rule_kind),
    ):
        if not value:
            missing.append(key)
    if missing:
        raise ValueError("无法建立运行期章节规则身份，缺少：" + "、".join(missing))

    if rule_kind not in {"structure", "chapter"}:
        raise ValueError(f"章节规则的规则类型必须为 structure/chapter，实际为：{rule_kind}")

    return {
        "rule_domain": "chapter_review",
        "object_scope": object_scope,
        "document_role": document_role,
        "part": part,
        "section_number": section_number,
        "section_title": section_title,
        "rule_kind": rule_kind,
        "rule_status": "active",
        "scope_level": "object",
        "stage": "",
        "rule_code": "",
        "rule_title": "",
    }


def _derive_basic_identity(text: str) -> dict:
    h1 = _first_heading(text)
    applicable_object = _line_value(text, "适用对象")
    applicable_scope = _line_value(text, "适用范围")

    document_role = (
        _line_value(text, "文档角色")
        or _known_document_role(applicable_object)
        or _known_document_role(h1)
        or "采购技术规范书"
    )

    rule_kind = (_line_value(text, "规则类型") or "").strip().lower()
    rule_code = (_line_value(text, "规则编号") or "").strip()
    rule_title = (_line_value(text, "规则标题") or "").strip()
    stage = (_line_value(text, "适用阶段") or "").strip().lower()
    object_scope = (_line_value(text, "对象范围") or "").strip()
    explicit_scope_level = (
        _line_value(text, "作用层级")
        or _line_value(text, "范围层级")
        or ""
    ).strip().lower()

    if not rule_kind:
        rule_kind = "basic_structure" if rule_code == "B00" else "basic"

    if not stage and (rule_kind == "basic_structure" or rule_code == "B00"):
        stage = "index"

    allowed_stages = {
        "index",
        "layout",
        "text_quality",
        "numbering_reference",
        "consistency",
    }
    if stage not in allowed_stages:
        raise ValueError(
            "基础规则缺少或包含无效“适用阶段”；允许值："
            + "、".join(sorted(allowed_stages))
            + f"；实际为：{stage or '空'}"
        )

    if rule_kind not in {"basic_structure", "basic"}:
        raise ValueError(
            f"基础规则的规则类型必须为 basic_structure/basic，实际为：{rule_kind}"
        )

    if stage == "index" and rule_kind != "basic_structure":
        raise ValueError("基础规则 index 阶段必须使用 rule_kind=basic_structure。")
    if stage != "index" and rule_kind != "basic":
        raise ValueError("非 index 基础阶段必须使用 rule_kind=basic。")

    if not rule_code:
        raise ValueError("基础规则缺少“规则编号”。")
    if not rule_title:
        raise ValueError("基础规则缺少“规则标题”。")

    if explicit_scope_level:
        scope_level = explicit_scope_level
    elif object_scope:
        scope_level = "object"
    else:
        # 当前 B00~B04 为全部采购技术规范书通用基础规则。
        scope_level = "global"

    if scope_level not in {"global", "object"}:
        raise ValueError(
            f"基础规则作用层级必须为 global/object，实际为：{scope_level}"
        )

    if scope_level == "global":
        object_scope = ""
    elif not object_scope:
        # 为未来 object 级基础规则保留一个可推导入口。
        candidate = _clean_object_scope(applicable_object, document_role)
        if candidate and candidate not in {"通用", "所有已识别物资品类"}:
            object_scope = candidate
        if not object_scope:
            raise ValueError("object 级基础规则必须提供“对象范围”。")

    return {
        "rule_domain": "basic_check",
        "object_scope": object_scope,
        "document_role": document_role,
        "part": "基础检查",
        "section_number": "",
        "section_title": "",
        "rule_kind": rule_kind,
        "rule_status": "active",
        "scope_level": scope_level,
        "stage": stage,
        "rule_code": rule_code,
        "rule_title": rule_title,
    }


def _derive_identity(text: str) -> dict:
    rule_domain = (_line_value(text, "规则域") or "").strip().lower()
    rule_kind = (_line_value(text, "规则类型") or "").strip().lower()

    if rule_domain == "basic_check" or rule_kind in {"basic_structure", "basic"}:
        return _derive_basic_identity(text)

    return _derive_professional_identity(text)


def _ensure_professional_identity_fields(text: str, identity: dict) -> str:
    """保持原有章节规则规范化行为，避免仅因升级 Pipeline 造成内容 Hash 变化。"""
    lines = (text or "").splitlines()
    h1_idx = None
    h2_idx = len(lines)
    for i, line in enumerate(lines):
        if h1_idx is None and re.match(r"^#\s+\S", line.strip()):
            h1_idx = i
            continue
        if h1_idx is not None and re.match(r"^##\s+\S", line.strip()):
            h2_idx = i
            break

    if h1_idx is None:
        raise ValueError("未检测到规则 H1 标题，已拒绝入库。")

    canonical_fields = {
        "对象范围",
        "文档角色",
        "所属部分",
        "章节编号",
        "章节标题",
        "规则类型",
    }
    head = lines[: h1_idx + 1]
    identity_zone = []
    for line in lines[h1_idx + 1 : h2_idx]:
        m = re.match(r"^\s*[-*+]\s*([^：:]+)\s*[：:]", line)
        if m and m.group(1).strip() in canonical_fields:
            continue
        identity_zone.append(line)

    canonical = [
        f"- 对象范围：{identity['object_scope']}",
        f"- 文档角色：{identity['document_role']}",
        f"- 所属部分：{identity['part']}",
        f"- 章节编号：{identity['section_number']}",
        f"- 章节标题：{identity['section_title']}",
        f"- 规则类型：{identity['rule_kind']}",
    ]

    out = head + canonical + identity_zone + lines[h2_idx:]
    return "\n".join(out)

def _ensure_basic_identity_fields(text: str, identity: dict) -> str:
    """规范基础规则机器身份字段；保留“适用范围/审查任务”等人工可读说明。"""
    lines = (text or "").splitlines()
    h1_idx = None
    h2_idx = len(lines)
    for i, line in enumerate(lines):
        if h1_idx is None and re.match(r"^#\s+\S", line.strip()):
            h1_idx = i
            continue
        if h1_idx is not None and re.match(r"^##\s+\S", line.strip()):
            h2_idx = i
            break

    if h1_idx is None:
        raise ValueError("未检测到基础规则 H1 标题，已拒绝入库。")

    canonical_fields = {
        "规则域",
        "文档角色",
        "所属部分",
        "规则编号",
        "规则标题",
        "规则类型",
        "适用阶段",
        "对象范围",
        "作用层级",
    }
    head = lines[: h1_idx + 1]
    identity_zone = []
    for line in lines[h1_idx + 1 : h2_idx]:
        m = re.match(r"^\s*[-*+]\s*([^：:]+)\s*[：:]", line)
        if m and m.group(1).strip() in canonical_fields:
            continue
        identity_zone.append(line)

    canonical = [
        "- 规则域：basic_check",
        f"- 文档角色：{identity['document_role']}",
        "- 所属部分：基础检查",
        f"- 规则编号：{identity['rule_code']}",
        f"- 规则标题：{identity['rule_title']}",
        f"- 规则类型：{identity['rule_kind']}",
        f"- 适用阶段：{identity['stage']}",
        f"- 作用层级：{identity['scope_level']}",
    ]
    if identity["scope_level"] == "object":
        canonical.append(f"- 对象范围：{identity['object_scope']}")

    out = head + canonical + identity_zone + lines[h2_idx:]
    return "\n".join(out)


def _ensure_identity_fields(text: str, identity: dict) -> str:
    if identity["rule_domain"] == "basic_check":
        return _ensure_basic_identity_fields(text, identity)
    return _ensure_professional_identity_fields(text, identity)


def _route_comment(identity: dict) -> str:
    if identity["rule_domain"] == "basic_check":
        scope_piece = (
            f"object_scope={identity['object_scope']} | "
            if identity["scope_level"] == "object"
            else ""
        )
        return (
            "<!-- ROUTE: "
            "rule_domain=basic_check | "
            f"document_role={identity['document_role']} | "
            f"scope_level={identity['scope_level']} | "
            f"{scope_piece}"
            f"stage={identity['stage']} | "
            f"rule_code={identity['rule_code']} | "
            f"rule_title={identity['rule_title']} | "
            f"rule_kind={identity['rule_kind']} | "
            "rule_status=active -->"
        )

    return (
        "<!-- ROUTE: "
        "rule_domain=chapter_review | "
        f"object_scope={identity['object_scope']} | "
        f"document_role={identity['document_role']} | "
        f"rule_kind={identity['rule_kind']} | "
        f"part={identity['part']} | "
        f"section_number={identity['section_number']} | "
        f"section_title={identity['section_title']} | "
        "rule_status=active -->"
    )


def _inject_route_comments(prepared: str, identity: dict) -> str:
    """给每个语义子块增加隐藏路由标签，提高规则域/对象/章节的召回区分度。"""
    comment = _route_comment(identity)
    blocks = [b.strip() for b in re.split(r"\n{2,}", prepared or "") if b.strip()]
    return "\n\n".join(f"{comment}\n{block}" for block in blocks).rstrip() + "\n"


def _safe_filename_piece(value: str) -> str:
    value = (value or "").strip()
    value = value.replace("/", "、").replace("\\", "、")
    value = re.sub(r'[:*?"<>|]', "＿", value)
    return value.strip()


def _filename_section_number(number: str) -> str:
    number = (number or "").strip()
    if not number:
        return ""
    parts = number.split(".")
    if all(part.isdigit() for part in parts):
        return ".".join(part.zfill(2) for part in parts)
    return number


def _canonical_document_name(identity: dict) -> str:
    if identity["rule_domain"] == "basic_check":
        code = _safe_filename_piece(identity["rule_code"])
        numeric = re.sub(r"^[A-Za-z]+", "", code)
        number = numeric.zfill(2) if numeric.isdigit() else code
        title = _safe_filename_piece(identity["rule_title"])
        if identity["rule_code"] == "B00":
            title = "基础规则索引"
        return f"技术规范书—基础检查—{number}{title}—审查规则.md"

    object_scope = _safe_filename_piece(identity["object_scope"])
    if identity["rule_kind"] == "structure":
        return f"{object_scope}—00全文章节结构—审查规则.md"

    part = _safe_filename_piece(identity["part"])
    section_number = _filename_section_number(identity["section_number"])
    section_title = _safe_filename_piece(identity["section_title"])
    return f"{object_scope}—{part}—{section_number}{section_title}—审查规则.md"


def main(text: str) -> dict:
    text = text or ""

    # 防止 Skill、约定说明、预检报告等参考文件误入正式规则库。
    status_pattern = r"(?m)^(\s*[-*+]\s*规则状态\s*[：:]\s*).*$"
    if not re.search(status_pattern, text):
        raise ValueError("未检测到“规则状态”字段：该文件疑似不是正式审查规则，已拒绝入库。")

    # 1. 手工上传正式知识库即视为已核准发布，统一激活。
    text = re.sub(status_pattern, lambda m: m.group(1) + "active", text)

    # 2. 删除核准提示字样。
    text = text.replace("（核准后使用）", "").replace("(核准后使用)", "")

    # 3. 删除“来源追溯”章节及其内容。
    text = _remove_source_trace_section(text)

    # 4. 建立并规范运行期身份字段。
    identity = _derive_identity(text)
    text = _ensure_identity_fields(text, identity)

    # PostgreSQL Rule Registry 保存完整、清洗后的权威正文。
    registry_content = text.strip() + "\n"
    document_name = _canonical_document_name(identity)

    # 5. 章节结构规则使用结构专用分块；其余规则使用通用语义分块。
    if (
        identity["rule_domain"] == "chapter_review"
        and (
            identity["rule_kind"] == "structure"
            or _is_structure_rule(text)
        )
    ):
        prepared = _prepare_structure_rule_chunks(text)
    else:
        prepared = _prepare_normal_rule_chunks(text)

    # 6. 仅给 Dify 向量索引文本增加隐藏机器路由标签。
    cleaned = _inject_route_comments(prepared, identity)

    # 入库前终检。
    if "（核准后使用）" in cleaned or "(核准后使用)" in cleaned:
        raise ValueError("清洗失败：仍存在“核准后使用”字样。")
    if re.search(r"(?m)^#{1,6}\s*来源追溯\s*$", cleaned):
        raise ValueError("清洗失败：仍存在“来源追溯”章节。")
    if not re.search(
        r"(?m)^\s*[-*+]\s*规则状态\s*[：:]\s*active\s*$",
        cleaned,
    ):
        raise ValueError("清洗失败：规则状态未统一为 active。")

    if identity["rule_domain"] == "basic_check":
        for field in (
            "规则域",
            "文档角色",
            "规则编号",
            "规则标题",
            "规则类型",
            "适用阶段",
            "作用层级",
        ):
            if not re.search(
                rf"(?m)^\s*[-*+]\s*{re.escape(field)}\s*[：:]\s*\S",
                cleaned,
            ):
                raise ValueError(f"清洗失败：缺少基础规则身份字段“{field}”。")
    else:
        for field in (
            "对象范围",
            "文档角色",
            "所属部分",
            "章节编号",
            "章节标题",
            "规则类型",
        ):
            if not re.search(
                rf"(?m)^\s*[-*+]\s*{re.escape(field)}\s*[：:]\s*\S",
                cleaned,
            ):
                raise ValueError(f"清洗失败：缺少章节规则身份字段“{field}”。")

    return {
        "cleaned_text": cleaned,
        "document_metadata": dict(identity),
        "rule_status": identity["rule_status"],
        "registry_content": registry_content,
        "document_name": document_name,
        "rule_domain": identity["rule_domain"],
        "object_scope": identity["object_scope"],
        "document_role": identity["document_role"],
        "part": identity["part"],
        "section_number": identity["section_number"],
        "section_title": identity["section_title"],
        "rule_kind": identity["rule_kind"],
        "scope_level": identity["scope_level"],
        "stage": identity["stage"],
        "rule_code": identity["rule_code"],
        "rule_title": identity["rule_title"],
    }
