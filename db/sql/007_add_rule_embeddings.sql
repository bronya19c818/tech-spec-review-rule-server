-- 007: 本地语义检索表 —— 替代 Dify 知识库
--
-- 原 Dify 链路：clean_rule 生成 cleaned_text（分块 + ROUTE 注释）→ Dify 父子分块
--              → qwen3-embedding-0.6b 向量化 → hybrid_search + bge-reranker-base。
-- 现落库：每个可检索子块一行，存 ROUTE 身份字段（结构化列）+ 向量 + 原文分块正文，
--          检索直接在 pgvector 上做，不再依赖 Dify。
BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rule_embeddings (
    id BIGSERIAL PRIMARY KEY,

    -- 归属的规则版本（章节域）或基础规则
    rule_version_id BIGINT REFERENCES rule_versions(id) ON DELETE CASCADE,
    basic_rule_id   BIGINT REFERENCES basic_rules(id)   ON DELETE CASCADE,

    rule_domain VARCHAR(40) NOT NULL
        CHECK (rule_domain IN ('chapter_review', 'basic_check')),

    -- ROUTE 身份（与 Dify cleaned_text 里 <!-- ROUTE: ... --> 同名字段）
    document_role  VARCHAR(100) NOT NULL,
    object_scope   VARCHAR(100) NOT NULL DEFAULT '',
    part           VARCHAR(100) NOT NULL DEFAULT '',
    section_number VARCHAR(50)  NOT NULL DEFAULT '',
    section_title  VARCHAR(255) NOT NULL DEFAULT '',
    rule_kind      VARCHAR(30)  NOT NULL DEFAULT '',
    scope_level    VARCHAR(20)  NOT NULL DEFAULT '',
    stage          VARCHAR(40)  NOT NULL DEFAULT '',
    rule_code      VARCHAR(40)  NOT NULL DEFAULT '',
    rule_title     VARCHAR(255) NOT NULL DEFAULT '',
    rule_status    VARCHAR(30)  NOT NULL DEFAULT 'active',

    -- 分块正文（不含 ROUTE 注释；身份在结构化列里）
    chunk_index INT NOT NULL DEFAULT 0,
    content     TEXT NOT NULL,

    -- 向量（维度与模型对齐；qwen3-embedding-0.6b 原生 1024 维）
    embedding   vector(1024),

    content_hash VARCHAR(64) NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (rule_domain, document_role, object_scope, part, section_number, rule_kind, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_rule_embeddings_filter
    ON rule_embeddings (rule_domain, document_role, rule_status, object_scope);

-- 向量近邻索引（余弦）
CREATE INDEX IF NOT EXISTS idx_rule_embeddings_vec
    ON rule_embeddings USING hnsw (embedding vector_cosine_ops);

COMMIT;
