CREATE EXTENSION IF NOT EXISTS vector;

-- 1. 规则包
CREATE TABLE IF NOT EXISTS rule_packages (
    id BIGSERIAL PRIMARY KEY,

    package_key VARCHAR(150) NOT NULL UNIQUE,
    package_name VARCHAR(255) NOT NULL,

    object_scope VARCHAR(100) NOT NULL,
    document_role VARCHAR(100) NOT NULL,

    status VARCHAR(30) NOT NULL DEFAULT 'active',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. 逻辑规则
-- 一条记录代表“净水器 / 专用部分 / 2 / 标准技术参数”这一规则身份，
-- 不代表某一个具体版本。
CREATE TABLE IF NOT EXISTS rules (
    id BIGSERIAL PRIMARY KEY,

    rule_id VARCHAR(120) NOT NULL UNIQUE,
    package_id BIGINT NOT NULL
        REFERENCES rule_packages(id)
        ON DELETE CASCADE,

    object_scope VARCHAR(100) NOT NULL,
    document_role VARCHAR(100) NOT NULL,

    part VARCHAR(100) NOT NULL,
    section_number VARCHAR(50) NOT NULL,
    section_title VARCHAR(255) NOT NULL,

    rule_kind VARCHAR(30) NOT NULL
        CHECK (rule_kind IN ('structure', 'chapter')),

    current_version_id BIGINT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (
        object_scope,
        document_role,
        part,
        section_number,
        rule_kind
    )
);

-- 3. 具体规则版本
CREATE TABLE IF NOT EXISTS rule_versions (
    id BIGSERIAL PRIMARY KEY,

    rule_id BIGINT NOT NULL
        REFERENCES rules(id)
        ON DELETE CASCADE,

    version INTEGER NOT NULL,

    document_name VARCHAR(500) NOT NULL,
    rule_status VARCHAR(30) NOT NULL
        CHECK (
            rule_status IN (
                'candidate',
                'active',
                'superseded',
                'archived'
            )
        ),

    content TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,

    source VARCHAR(100),
    published_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (rule_id, version),
    UNIQUE (document_name, version)
);

-- current_version_id 外键后补，解决建表顺序问题
ALTER TABLE rules
DROP CONSTRAINT IF EXISTS fk_rules_current_version;

ALTER TABLE rules
ADD CONSTRAINT fk_rules_current_version
FOREIGN KEY (current_version_id)
REFERENCES rule_versions(id)
ON DELETE SET NULL;

-- 4. 发布操作日志
CREATE TABLE IF NOT EXISTS rule_publish_logs (
    id BIGSERIAL PRIMARY KEY,

    rule_id VARCHAR(120),
    version INTEGER,

    action VARCHAR(50) NOT NULL,
    source VARCHAR(100),

    content_hash VARCHAR(64),
    detail JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 查询索引
CREATE INDEX IF NOT EXISTS idx_rules_identity
ON rules (
    object_scope,
    document_role,
    part,
    section_number,
    rule_kind
);

CREATE INDEX IF NOT EXISTS idx_rule_versions_status
ON rule_versions (rule_status);

CREATE INDEX IF NOT EXISTS idx_rule_versions_document_name
ON rule_versions (document_name);
