BEGIN;

CREATE TABLE IF NOT EXISTS basic_rule_packages (
    id BIGSERIAL PRIMARY KEY,

    package_key VARCHAR(160) NOT NULL UNIQUE,
    package_name VARCHAR(255) NOT NULL,

    document_role VARCHAR(100) NOT NULL,

    scope_level VARCHAR(20) NOT NULL
        CHECK (scope_level IN ('global', 'object')),

    object_scope VARCHAR(100),

    status VARCHAR(30) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CHECK (
        (scope_level = 'global' AND object_scope IS NULL)
        OR
        (scope_level = 'object' AND object_scope IS NOT NULL)
    )
);


CREATE TABLE IF NOT EXISTS basic_rules (
    id BIGSERIAL PRIMARY KEY,

    rule_id VARCHAR(120) NOT NULL UNIQUE,

    package_id BIGINT NOT NULL
        REFERENCES basic_rule_packages(id)
        ON DELETE CASCADE,

    stage VARCHAR(40) NOT NULL
        CHECK (
            stage IN (
                'index',
                'layout',
                'text_quality',
                'numbering_reference',
                'consistency'
            )
        ),

    rule_code VARCHAR(50) NOT NULL,
    rule_title VARCHAR(255) NOT NULL,

    rule_kind VARCHAR(30) NOT NULL
        CHECK (
            rule_kind IN (
                'basic_structure',
                'basic'
            )
        ),

    current_version_id BIGINT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (package_id, stage, rule_code)
);


CREATE TABLE IF NOT EXISTS basic_rule_versions (
    id BIGSERIAL PRIMARY KEY,

    rule_id BIGINT NOT NULL
        REFERENCES basic_rules(id)
        ON DELETE CASCADE,

    version INTEGER NOT NULL,

    document_name VARCHAR(255) NOT NULL,

    rule_status VARCHAR(30) NOT NULL DEFAULT 'active'
        CHECK (
            rule_status IN (
                'active',
                'superseded',
                'inactive'
            )
        ),

    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,

    source VARCHAR(100),

    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (rule_id, version)
);


ALTER TABLE basic_rules
ADD CONSTRAINT fk_basic_rules_current_version
FOREIGN KEY (current_version_id)
REFERENCES basic_rule_versions(id)
ON DELETE SET NULL;


CREATE INDEX IF NOT EXISTS idx_basic_packages_lookup
ON basic_rule_packages (
    document_role,
    scope_level,
    object_scope,
    status
);


CREATE INDEX IF NOT EXISTS idx_basic_rules_stage
ON basic_rules (
    package_id,
    stage,
    rule_kind
);


CREATE INDEX IF NOT EXISTS idx_basic_versions_status
ON basic_rule_versions (
    rule_id,
    rule_status
);


CREATE UNIQUE INDEX IF NOT EXISTS uq_basic_global_active_package
ON basic_rule_packages (document_role)
WHERE scope_level = 'global'
  AND status = 'active';


CREATE UNIQUE INDEX IF NOT EXISTS uq_basic_object_active_package
ON basic_rule_packages (document_role, object_scope)
WHERE scope_level = 'object'
  AND status = 'active';


COMMIT;
