"""服务端语义检索：查询向量化 + pgvector 召回。

嵌入模型只在服务端配置，客户端不参与：索引是共享资产，查询向量必须落在
同一个向量空间里，否则余弦相似度全是噪声。维度在写库与查询两侧都校验，
不匹配直接报错而不是静默降级。
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import psycopg
from psycopg.rows import dict_row


def embedding_config() -> dict[str, Any]:
    return {
        "base_url": (os.getenv("TSR_EMBEDDING_BASE_URL") or "").rstrip("/"),
        "model": os.getenv("TSR_EMBEDDING_MODEL") or "",
        "api_key": os.getenv("TSR_EMBEDDING_API_KEY") or "",
        "dimensions": int(os.getenv("TSR_EMBEDDING_DIMENSIONS") or "1024"),
    }


def database_url() -> str:
    return os.environ["DATABASE_URL"]


class EmbeddingError(RuntimeError):
    """嵌入调用失败或维度不符。"""


def embed_texts(texts: list[str], *, batch_size: int = 16) -> list[list[float]]:
    """按批向量化，返回与输入同序的向量列表。"""
    if not texts:
        return []
    cfg = embedding_config()
    if not cfg["base_url"] or not cfg["model"]:
        raise EmbeddingError("EMBEDDING_NOT_CONFIGURED")
    url = cfg["base_url"]
    url = url if url.endswith("/embeddings") else url + "/embeddings"
    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg["api_key"] else {}
    out: list[list[float]] = []
    with httpx.Client(trust_env=False, timeout=60) as client:
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            response = client.post(url, json={"model": cfg["model"], "input": batch}, headers=headers)
            if not response.is_success:
                raise EmbeddingError(f"EMBEDDING_HTTP_{response.status_code}")
            vectors = _vectors(response.json())
            if vectors is None or len(vectors) != len(batch):
                raise EmbeddingError("EMBEDDING_RESPONSE_INVALID")
            for vector in vectors:
                if len(vector) != cfg["dimensions"]:
                    raise EmbeddingError(
                        "EMBEDDING_DIMENSION_MISMATCH: expected "
                        f"{cfg['dimensions']}, got {len(vector)}"
                    )
            out.extend(vectors)
    return out


def _vectors(payload: Any) -> list[list[float]] | None:
    """兼容几种常见外壳。"""
    if not isinstance(payload, dict):
        return None
    rows = payload.get("data")
    if isinstance(rows, dict):
        rows = [rows]
    if isinstance(rows, list):
        out: list[list[float]] = []
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("embedding"), list):
                out.append([float(v) for v in row["embedding"]])
            elif isinstance(row, list):
                out.append([float(v) for v in row])
        return out or None
    rows = payload.get("embeddings")
    if isinstance(rows, list):
        return [[float(v) for v in row] for row in rows if isinstance(row, list)] or None
    return None


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def search(
    query: str,
    *,
    document_role: str,
    object_scope: str = "",
    rule_kind: str = "chapter",
    top_k: int = 24,
) -> list[dict[str, Any]]:
    """向量召回：返回带身份与分块正文的候选，按相似度降序。"""
    vector = embed_texts([query])[0]
    params: list[Any] = [vector]
    where = ["rule_domain = 'chapter_review'", "rule_status = 'active'", "document_role = %s"]
    params.append(document_role)
    if object_scope and object_scope != "UNKNOWN":
        where.append("object_scope = %s")
        params.append(object_scope)
    if rule_kind:
        where.append("rule_kind = %s")
        params.append(rule_kind)
    params.extend([vector, top_k])
    sql = f"""
        SELECT object_scope, document_role, part, section_number, section_title,
               rule_kind, scope_level, stage, rule_code, rule_title, content,
               1 - (embedding <=> %s::vector) AS score
        FROM rule_embeddings
        WHERE {' AND '.join(where)}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    with psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=10) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def upsert_chunks(rows: list[dict[str, Any]]) -> int:
    """写入语义索引分块。行里带身份字段与 content，向量在服务端算。

    按规则身份整组替换：规则正文变短时，残留的高位 chunk 必须删掉，
    否则旧内容还能被检索到。
    """
    if not rows:
        return 0
    vectors = embed_texts([row["content"] for row in rows])
    identities = {
        (row["rule_domain"], row["document_role"], row["object_scope"],
         row["part"], row["section_number"], row["rule_kind"])
        for row in rows
    }
    with psycopg.connect(database_url(), connect_timeout=10) as conn, conn.cursor() as cur:
        for domain, role, scope, part, number, kind in sorted(identities):
            cur.execute(
                """
                DELETE FROM rule_embeddings
                WHERE rule_domain = %s AND document_role = %s AND object_scope = %s
                  AND part = %s AND section_number = %s AND rule_kind = %s
                """,
                (domain, role, scope, part, number, kind),
            )
        for row, vector in zip(rows, vectors):
            cur.execute(
                """
                INSERT INTO rule_embeddings (
                    rule_domain, document_role, object_scope, part, section_number,
                    section_title, rule_kind, scope_level, stage, rule_code,
                    rule_title, rule_status, chunk_index, content, embedding, content_hash
                ) VALUES (
                    %(rule_domain)s, %(document_role)s, %(object_scope)s, %(part)s, %(section_number)s,
                    %(section_title)s, %(rule_kind)s, %(scope_level)s, %(stage)s, %(rule_code)s,
                    %(rule_title)s, %(rule_status)s, %(chunk_index)s, %(content)s, %(embedding)s::vector,
                    %(content_hash)s
                )
                ON CONFLICT (rule_domain, document_role, object_scope, part,
                             section_number, rule_kind, chunk_index)
                DO UPDATE SET
                    content = EXCLUDED.content,
                    embedding = EXCLUDED.embedding,
                    content_hash = EXCLUDED.content_hash
                """,
                {**row, "embedding": _literal(vector)},
            )
        conn.commit()
    return len(rows)


def service_info() -> dict[str, Any]:
    """给客户端展示的只读服务信息：嵌入配置与索引规模。"""
    cfg = embedding_config()
    counts: dict[str, Any] = {}
    try:
        with psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=10) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*) AS chunks,
                           count(*) FILTER (WHERE rule_domain = 'chapter_review') AS chapter_chunks,
                           count(*) FILTER (WHERE rule_domain = 'basic_check') AS basic_chunks,
                           max(created_at) AS indexed_at
                    FROM rule_embeddings
                    """
                )
                counts = dict(cur.fetchone() or {})
                cur.execute(
                    """
                    SELECT count(*) FILTER (WHERE rule_status = 'active') AS active_rules
                    FROM rule_versions v
                    JOIN rules r ON r.id = v.rule_id AND r.current_version_id = v.id
                    """
                )
                counts.update(dict(cur.fetchone() or {}))
    except Exception as exc:  # noqa: BLE001 - 服务信息读不到不该让整个接口失败
        counts = {"error": type(exc).__name__}
    return {
        "embedding_model": cfg["model"],
        "embedding_dimensions": cfg["dimensions"],
        "embedding_configured": bool(cfg["model"] and cfg["base_url"]),
        **counts,
    }
