BEGIN;

DO $$
DECLARE
    v_package_id BIGINT;
    v_rule_pk BIGINT;
    v_version_id BIGINT;
BEGIN
    SELECT id
      INTO v_package_id
      FROM rule_packages
     WHERE package_key = 'technical-spec:water-purifier'
       AND status = 'active';

    IF v_package_id IS NULL THEN
        RAISE EXCEPTION '未找到 active 规则包 technical-spec:water-purifier';
    END IF;

    INSERT INTO rules (
        rule_id,
        package_id,
        object_scope,
        document_role,
        part,
        section_number,
        section_title,
        rule_kind
    )
    VALUES (
        'TSR-WATERPURIFIER-STRUCTURE-00',
        v_package_id,
        '净水器',
        '采购技术规范书',
        '全文',
        '00',
        '全文章节结构',
        'structure'
    )
    ON CONFLICT (rule_id) DO UPDATE
       SET package_id = EXCLUDED.package_id,
           object_scope = EXCLUDED.object_scope,
           document_role = EXCLUDED.document_role,
           part = EXCLUDED.part,
           section_number = EXCLUDED.section_number,
           section_title = EXCLUDED.section_title,
           rule_kind = EXCLUDED.rule_kind,
           updated_at = NOW()
    RETURNING id INTO v_rule_pk;

    INSERT INTO rule_versions (
        rule_id,
        version,
        document_name,
        rule_status,
        content,
        content_hash,
        source,
        published_at
    )
    VALUES (
        v_rule_pk,
        1,
        '净水器—00全文章节结构—审查规则.md',
        'active',
        $rule$
# 净水器｜全文章节结构｜审查规则

- 适用对象：净水器采购技术规范书
- 对象范围：净水器
- 文档角色：采购技术规范书
- 适用审查章节：全文（通用部分及专用部分）
- 所属部分：全文
- 章节编号：00
- 章节标题：全文章节结构
- 标题层级：全文结构
- 规则类型：structure
- 规则状态：active
- 审查任务：先核对一级、二级有标题章节，再沿下列导航逐章读取内容规则。

## 标准章节树及内容规则导航

| 部分     | 编号 | 标题                       | 内容规则文件                                                  |
| -------- | ---- | -------------------------- | ------------------------------------------------------------- |
| 通用部分 | 1    | 范围                       | 净水器—通用部分—01范围—审查规则.md                            |
| 通用部分 | 2    | 规范性引用文件             | 净水器—通用部分—02规范性引用文件—审查规则.md                  |
| 通用部分 | 3    | 术语和定义                 | 净水器—通用部分—03术语和定义—审查规则.md                      |
| 通用部分 | 4    | 总则                       | 净水器—通用部分—04总则—审查规则.md                            |
| 通用部分 | 5    | 技术要求                   | 由5.1—5.4规则覆盖                                             |
| 通用部分 | 5.1  | 技术原则                   | 净水器—通用部分—05.01技术原则—审查规则.md                     |
| 通用部分 | 5.2  | 功能要求                   | 净水器—通用部分—05.02功能要求—审查规则.md                     |
| 通用部分 | 5.3  | 性能要求                   | 净水器—通用部分—05.03性能要求—审查规则.md                     |
| 通用部分 | 5.4  | 接口要求                   | 净水器—通用部分—05.04接口要求—审查规则.md                     |
| 通用部分 | 6    | 试验                       | 由6.1—6.3规则覆盖                                             |
| 通用部分 | 6.1  | 型式试验/全性能试验        | 净水器—通用部分—06.01型式试验、全性能试验—审查规则.md         |
| 通用部分 | 6.2  | 出厂试验/工厂试验          | 净水器—通用部分—06.02出厂试验、工厂试验—审查规则.md           |
| 通用部分 | 6.3  | 现场交接试验/首检/验收试验 | 净水器—通用部分—06.03现场交接试验、首检、验收试验—审查规则.md |
| 通用部分 | 7    | 设计联络、监造、技术服务   | 由7.1—7.3规则覆盖                                             |
| 通用部分 | 7.1  | 设计联络                   | 净水器—通用部分—07.01设计联络—审查规则.md                     |
| 通用部分 | 7.2  | 工厂检验和监造             | 净水器—通用部分—07.02工厂检验和监造—审查规则.md               |
| 通用部分 | 7.3  | 技术服务                   | 净水器—通用部分—07.03技术服务—审查规则.md                     |
| 通用部分 | 8    | 包装和运输                 | 净水器—通用部分—08包装和运输—审查规则.md                      |
| 专用部分 | 1    | 范围                       | 净水器—专用部分—01范围—审查规则.md                            |
| 专用部分 | 2    | 标准技术参数               | 净水器—专用部分—02标准技术参数—审查规则.md                    |
| 专用部分 | 3    | 使用环境条件               | 净水器—专用部分—03使用环境条件—审查规则.md                    |
| 专用部分 | 4    | 组件材料配置               | 净水器—专用部分—04组件材料配置—审查规则.md                    |
| 专用部分 | 5    | 图纸                       | 净水器—专用部分—05图纸—审查规则.md                            |

## 结构判定口径

## 标题层级约束

一级标题（1.、2.、3.等）固定，须保持模板的编号、名称、所属部分及顺序，不得改名、删除、合并或通过新增一级章改变既有结构。通用部分与专用部分分别核对；自动编号呈现方式、空格等纯排版差异不等于标题变更。内容在其他位置出现不能抵消一级结构不符合。

二级标题（1.1、1.2、1.3等）可适当改名、调整或按需增加，但应归属正确的一级章，编号连续、语义清晰，并完整覆盖规则要求的必要内容。拆分、合并或调整二级标题后，按实际内容建立一对多或多对一映射；不能因标题与模板不一致直接判不符合，也不能据此免除必要内容检查。

新增二级章节纳入本次审查计划，先检查归属和与已有内容的一致性，再检索对应规则；无适用依据时记录规则不足，不自行增加技术门槛。一级标题固定属于用户明确的编制约束，不再作为“是否允许改名待核实”事项。

总则4.1—4.5是正文条款，无独立标题，不虚构章节；4.6“应满足的文件”在总则规则内检查，作为二级标题还是引导语需核准原文版式，暂不单列必需章节。

## 执行台账

逐行登记实际章节位置、结构观察、内容规则取得情况、逐章结论；三个容器章节由子规则覆盖，其余20个章节分别审查。缺失章节仍记入台账，缺整章不重复累计每个子要点。
$rule$,
        '5f730725fb7397fa5c33ddc740aa5e368586b8af79d80d465b3e04e6f6414884',
        'manual_seed',
        NOW()
    )
    ON CONFLICT (rule_id, version) DO UPDATE
       SET document_name = EXCLUDED.document_name,
           rule_status = EXCLUDED.rule_status,
           content = EXCLUDED.content,
           content_hash = EXCLUDED.content_hash,
           source = EXCLUDED.source,
           published_at = EXCLUDED.published_at
    RETURNING id INTO v_version_id;

    UPDATE rules
       SET current_version_id = v_version_id,
           updated_at = NOW()
     WHERE id = v_rule_pk;

    INSERT INTO rule_publish_logs (
        rule_id,
        version,
        action,
        source,
        content_hash,
        detail
    )
    VALUES (
        'TSR-WATERPURIFIER-STRUCTURE-00',
        1,
        'seed',
        'manual_seed',
        '5f730725fb7397fa5c33ddc740aa5e368586b8af79d80d465b3e04e6f6414884',
        '{"document_name":"净水器—00全文章节结构—审查规则.md"}'::jsonb
    );
END $$;

COMMIT;
