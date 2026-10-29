CREATE TABLE IF NOT EXISTS object_scope_aliases (
    id BIGSERIAL PRIMARY KEY,

    object_scope VARCHAR(100) NOT NULL,
    alias VARCHAR(255) NOT NULL,

    match_type VARCHAR(30) NOT NULL DEFAULT 'exact'
        CHECK (match_type IN ('exact', 'contains')),

    priority INTEGER NOT NULL DEFAULT 100,
    status VARCHAR(30) NOT NULL DEFAULT 'active',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (alias)
);

CREATE INDEX IF NOT EXISTS idx_object_scope_aliases_scope
ON object_scope_aliases (object_scope);

CREATE INDEX IF NOT EXISTS idx_object_scope_aliases_status
ON object_scope_aliases (status);