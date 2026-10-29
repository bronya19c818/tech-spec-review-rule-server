BEGIN;

-- 1. 注册一个正式规则包
INSERT INTO rule_packages (
    package_key,
    package_name,
    object_scope,
    document_role,
    status
)
VALUES (
    'technical-spec:water-purifier',
    '净水器采购技术规范书',
    '净水器',
    '采购技术规范书',
    'active'
)
ON CONFLICT (package_key) DO UPDATE
SET
    package_name = EXCLUDED.package_name,
    object_scope = EXCLUDED.object_scope,
    document_role = EXCLUDED.document_role,
    status = EXCLUDED.status,
    updated_at = NOW();


-- 2. 注册对象标准名/别名
-- contains 的意思是：
-- “XX净水器”“商用净水器”“某品牌净水器”
-- 都可以归一化到 object_scope = 净水器

INSERT INTO object_scope_aliases (
    object_scope,
    alias,
    match_type,
    priority,
    status
)
VALUES
(
    '净水器',
    '净水器',
    'contains',
    100,
    'active'
)
ON CONFLICT (alias) DO UPDATE
SET
    object_scope = EXCLUDED.object_scope,
    match_type = EXCLUDED.match_type,
    priority = EXCLUDED.priority,
    status = EXCLUDED.status,
    updated_at = NOW();

COMMIT;