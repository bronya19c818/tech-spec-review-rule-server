import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from psycopg.types.json import Jsonb
from pydantic import BaseModel


DATABASE_URL = os.environ["DATABASE_URL"]

VALID_BASIC_STAGES = {
    "index",
    "layout",
    "text_quality",
    "numbering_reference",
    "consistency",
}
VALID_BASIC_RULE_KINDS = {"basic_structure", "basic"}
VALID_BASIC_SCOPE_LEVELS = {"global", "object"}


@dataclass
class RegistryError(Exception):
    status_code: int
    code: str
    detail: dict[str, Any]

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def get_conn():
    return psycopg.connect(DATABASE_URL)


def _resolve_object_scope_impl(name: str) -> dict[str, Any]:
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise RegistryError(
            status_code=400,
            code="EMPTY_OBJECT_NAME",
            detail={"input": normalized_name},
        )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    object_scope,
                    alias,
                    match_type,
                    priority
                FROM object_scope_aliases
                WHERE status = 'active'
                  AND alias = %s
                ORDER BY priority DESC
                LIMIT 2;
                """,
                (normalized_name,),
            )
            rows = cur.fetchall()

    if not rows:
        raise RegistryError(
            status_code=404,
            code="OBJECT_SCOPE_NOT_FOUND",
            detail={"input": normalized_name},
        )

    if len(rows) > 1:
        raise RegistryError(
            status_code=409,
            code="OBJECT_SCOPE_AMBIGUOUS",
            detail={"input": normalized_name},
        )

    object_scope, alias, match_type, priority = rows[0]
    return {
        "input": normalized_name,
        "object_scope": object_scope,
        "matched_alias": alias,
        "match_type": match_type,
        "priority": priority,
    }


def _get_rule_impl(
    *,
    object_scope: str,
    document_role: str,
    part: str,
    section_number: str,
    rule_kind: str,
) -> dict[str, Any]:
    query = {
        "object_scope": (object_scope or "").strip(),
        "document_role": (document_role or "").strip(),
        "part": (part or "").strip(),
        "section_number": (section_number or "").strip(),
        "rule_kind": (rule_kind or "").strip(),
    }

    empty_fields = [key for key, value in query.items() if not value]
    if empty_fields:
        raise RegistryError(
            status_code=400,
            code="MISSING_RULE_IDENTITY_FIELDS",
            detail={"fields": empty_fields, "query": query},
        )

    if query["rule_kind"] not in {"structure", "chapter"}:
        raise RegistryError(
            status_code=400,
            code="INVALID_RULE_KIND",
            detail={"rule_kind": query["rule_kind"]},
        )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    p.package_key,
                    p.package_name,

                    r.rule_id,
                    r.object_scope,
                    r.document_role,
                    r.part,
                    r.section_number,
                    r.section_title,
                    r.rule_kind,

                    rv.version,
                    rv.rule_status,
                    rv.document_name,
                    rv.content,
                    rv.content_hash,
                    rv.published_at

                FROM rule_packages p
                JOIN rules r
                  ON r.package_id = p.id
                JOIN rule_versions rv
                  ON rv.id = r.current_version_id

                WHERE p.status = 'active'
                  AND r.object_scope = %s
                  AND r.document_role = %s
                  AND r.part = %s
                  AND r.section_number = %s
                  AND r.rule_kind = %s
                  AND rv.rule_status = 'active'

                LIMIT 2;
                """,
                (
                    query["object_scope"],
                    query["document_role"],
                    query["part"],
                    query["section_number"],
                    query["rule_kind"],
                ),
            )
            rows = cur.fetchall()

    if not rows:
        raise RegistryError(
            status_code=404,
            code="ACTIVE_RULE_NOT_FOUND",
            detail={"query": query},
        )

    if len(rows) > 1:
        raise RegistryError(
            status_code=409,
            code="ACTIVE_RULE_CONFLICT",
            detail={"query": query},
        )

    row = rows[0]
    published_at = row[14]
    if published_at is not None:
        published_at = published_at.isoformat()

    return {
        "package": {
            "package_key": row[0],
            "package_name": row[1],
        },
        "rule": {
            "rule_id": row[2],
            "object_scope": row[3],
            "document_role": row[4],
            "part": row[5],
            "section_number": row[6],
            "section_title": row[7],
            "rule_kind": row[8],
            "version": row[9],
            "status": row[10],
            "document_name": row[11],
            "content": row[12],
            "content_hash": row[13],
            "published_at": published_at,
        },
    }


def _basic_rule_record(
    row: tuple[Any, ...],
    *,
    scope_level: str,
    object_scope: str | None,
) -> dict[str, Any]:
    published_at = row[10]
    if published_at is not None:
        published_at = published_at.isoformat()

    return {
        "rule_id": row[0],
        "stage": row[1],
        "rule_code": row[2],
        "rule_title": row[3],
        "rule_kind": row[4],
        "version": row[5],
        "status": row[6],
        "document_name": row[7],
        "content": row[8],
        "content_hash": row[9],
        "published_at": published_at,
        "scope_level": scope_level,
        "object_scope": object_scope,
    }


def _get_basic_rules_impl(
    *,
    document_role: str,
    stage: str,
    object_scope: str | None = None,
) -> dict[str, Any]:
    document_role = (document_role or "").strip()
    stage = (stage or "").strip()
    normalized_object_scope = (object_scope or "").strip() or None

    if not document_role:
        raise RegistryError(
            status_code=400,
            code="MISSING_DOCUMENT_ROLE",
            detail={"document_role": document_role},
        )

    if stage not in VALID_BASIC_STAGES:
        raise RegistryError(
            status_code=400,
            code="INVALID_BASIC_STAGE",
            detail={"stage": stage, "allowed": sorted(VALID_BASIC_STAGES)},
        )

    select_sql = """
        SELECT
            r.rule_id,
            r.stage,
            r.rule_code,
            r.rule_title,
            r.rule_kind,
            rv.version,
            rv.rule_status,
            rv.document_name,
            rv.content,
            rv.content_hash,
            rv.published_at
        FROM basic_rule_packages p
        JOIN basic_rules r
          ON r.package_id = p.id
        JOIN basic_rule_versions rv
          ON rv.id = r.current_version_id
        WHERE p.status = 'active'
          AND p.document_role = %s
          AND p.scope_level = %s
          AND r.stage = %s
          AND rv.rule_status = 'active'
    """

    global_rules: list[dict[str, Any]] = []
    object_rules: list[dict[str, Any]] = []

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                select_sql + """
                  AND p.object_scope IS NULL
                ORDER BY r.rule_code;
                """,
                (document_role, "global", stage),
            )
            for row in cur.fetchall():
                global_rules.append(
                    _basic_rule_record(
                        row,
                        scope_level="global",
                        object_scope=None,
                    )
                )

            if normalized_object_scope is not None:
                cur.execute(
                    select_sql + """
                      AND p.object_scope = %s
                    ORDER BY r.rule_code;
                    """,
                    (
                        document_role,
                        "object",
                        stage,
                        normalized_object_scope,
                    ),
                )
                for row in cur.fetchall():
                    object_rules.append(
                        _basic_rule_record(
                            row,
                            scope_level="object",
                            object_scope=normalized_object_scope,
                        )
                    )

    if not global_rules and not object_rules:
        raise RegistryError(
            status_code=404,
            code="ACTIVE_BASIC_RULE_NOT_FOUND",
            detail={
                "document_role": document_role,
                "stage": stage,
                "object_scope": normalized_object_scope,
            },
        )

    # 同 rule_code 时 object 级规则覆盖 global；不同 rule_code 则作为补充。
    effective_by_code: dict[str, dict[str, Any]] = {
        item["rule_code"]: item for item in global_rules
    }
    for item in object_rules:
        effective_by_code[item["rule_code"]] = item

    effective_rules = [
        effective_by_code[code]
        for code in sorted(effective_by_code)
    ]

    return {
        "query": {
            "document_role": document_role,
            "stage": stage,
            "object_scope": normalized_object_scope,
        },
        "merge_policy": "object_overrides_global_by_rule_code",
        "global_rules": global_rules,
        "object_rules": object_rules,
        "effective_rules": effective_rules,
    }


# ---------------------------------------------------------------------------
# MCP: deterministic runtime rule access
# ---------------------------------------------------------------------------

mcp = MCPServer("SI-SYNC Technical Spec Rule Registry")


@mcp.tool(name="resolve_object_scope", structured_output=True)
def mcp_resolve_object_scope(object_name: str) -> dict[str, Any]:
    """将技术规范书中的物资品类叫法精确归一化为规则库标准 object_scope。

    仅使用 active 的对象别名词典做 exact 匹配，不做模糊猜测，也不创造新的 object_scope。
    例如：滤水器 -> 净水器。
    """
    try:
        return _resolve_object_scope_impl(object_name)
    except RegistryError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(name="technical_spec_review_rule_get", structured_output=True)
def mcp_technical_spec_review_rule_get(
    object_scope: str,
    part: str,
    section_number: str,
    rule_kind: str = "chapter",
    document_role: str = "采购技术规范书",
) -> dict[str, Any]:
    """按完整规则身份精确取得当前 active 技术规范书审查规则。

    这是权威规则取得工具，不执行语义检索。
    object_scope 应先通过 resolve_object_scope 标准化。
    structure 规则使用 part='全文'、section_number='00'、rule_kind='structure'。
    chapter 规则使用实际所属部分和章节编号，例如 part='通用部分'、section_number='5.1'。
    """
    try:
        return _get_rule_impl(
            object_scope=object_scope,
            document_role=document_role,
            part=part,
            section_number=section_number,
            rule_kind=rule_kind,
        )
    except RegistryError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(name="technical_spec_basic_rule_get", structured_output=True)
def mcp_technical_spec_basic_rule_get(
    stage: str,
    document_role: str = "采购技术规范书",
    object_scope: str = "",
) -> dict[str, Any]:
    """取得当前 active 的技术规范书基础检查规则。

    stage 仅允许：
    index / layout / text_quality / numbering_reference / consistency。

    默认返回 document_role 对应的 global 通用基础规则。
    如提供已经标准化的 object_scope，则同时取得该品类的 object 级规则；
    相同 rule_code 时 object 规则覆盖 global，不同 rule_code 作为补充。
    """
    try:
        return _get_basic_rules_impl(
            document_role=document_role,
            stage=stage,
            object_scope=object_scope,
        )
    except RegistryError as exc:
        raise ToolError(str(exc)) from exc


from .catalog import register_catalog

register_catalog(mcp, get_conn)


# streamable_http_path="/" means the mounted public endpoint is exactly /mcp.
# Stateless JSON mode is sufficient for the current read-only MCP tools.
mcp_http_app = mcp.streamable_http_app(
    streamable_http_path="/",
    json_response=True,
    stateless_http=True,
    host="0.0.0.0",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Mounted MCP apps do not run their own lifespan, so the parent app must
    # explicitly run the MCP session manager.
    async with mcp.session_manager.run():
        yield


app = FastAPI(
    title="SI-SYNC Rule Registry",
    version="0.4.0",
    lifespan=lifespan,
)

app.mount("/mcp", mcp_http_app)


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

class ResolveObjectScopeRequest(BaseModel):
    name: str


class RuleGetRequest(BaseModel):
    object_scope: str
    document_role: str = "采购技术规范书"
    part: str
    section_number: str
    rule_kind: str


class RulePublishRequest(BaseModel):
    document_name: str
    object_scope: str
    document_role: str = "采购技术规范书"
    part: str
    section_number: str
    section_title: str
    rule_kind: str
    content: str
    source: str = "dify_pipeline"



class BasicRuleGetRequest(BaseModel):
    document_role: str = "采购技术规范书"
    stage: str
    object_scope: str | None = None


class BasicRulePublishRequest(BaseModel):
    document_name: str
    document_role: str = "采购技术规范书"
    scope_level: str = "global"
    object_scope: str | None = None
    stage: str
    rule_code: str
    rule_title: str
    rule_kind: str = "basic"
    content: str
    source: str = "dify_pipeline"


@app.get("/health")
def health():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()

    return {
        "status": "ok",
        "database": "connected",
        "version": "0.4.0",
        "mcp_endpoint": "/mcp",
        "mcp_tools": [
            "resolve_object_scope",
            "technical_spec_review_rule_get",
            "technical_spec_basic_rule_get",
        ],
    }


@app.post("/v1/object-scopes/resolve")
def resolve_object_scope(req: ResolveObjectScopeRequest):
    try:
        return _resolve_object_scope_impl(req.name)
    except RegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, **exc.detail},
        ) from exc


@app.post("/v1/rules/get")
def get_rule(req: RuleGetRequest):
    try:
        return _get_rule_impl(
            object_scope=req.object_scope,
            document_role=req.document_role,
            part=req.part,
            section_number=req.section_number,
            rule_kind=req.rule_kind,
        )
    except RegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, **exc.detail},
        ) from exc


@app.post("/v1/basic-rules/get")
def get_basic_rules(req: BasicRuleGetRequest):
    try:
        return _get_basic_rules_impl(
            document_role=req.document_role,
            stage=req.stage,
            object_scope=req.object_scope,
        )
    except RegistryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, **exc.detail},
        ) from exc


@app.post("/v1/basic-rules/publish")
def publish_basic_rule(req: BasicRulePublishRequest):
    document_name = req.document_name.strip()
    document_role = req.document_role.strip()
    scope_level = req.scope_level.strip()
    object_scope = (req.object_scope or "").strip() or None
    stage = req.stage.strip()
    rule_code = req.rule_code.strip()
    rule_title = req.rule_title.strip()
    rule_kind = req.rule_kind.strip()
    source = req.source.strip() or "dify_pipeline"
    content = req.content.strip()

    required_fields = {
        "document_name": document_name,
        "document_role": document_role,
        "scope_level": scope_level,
        "stage": stage,
        "rule_code": rule_code,
        "rule_title": rule_title,
        "rule_kind": rule_kind,
        "content": content,
    }
    empty_fields = [
        field_name
        for field_name, value in required_fields.items()
        if not value
    ]
    if empty_fields:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "MISSING_REQUIRED_FIELDS",
                "fields": empty_fields,
            },
        )

    if scope_level not in VALID_BASIC_SCOPE_LEVELS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_BASIC_SCOPE_LEVEL",
                "scope_level": scope_level,
                "allowed": sorted(VALID_BASIC_SCOPE_LEVELS),
            },
        )

    if scope_level == "global" and object_scope is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "GLOBAL_RULE_MUST_NOT_HAVE_OBJECT_SCOPE",
                "object_scope": object_scope,
            },
        )

    if scope_level == "object" and object_scope is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "OBJECT_RULE_REQUIRES_OBJECT_SCOPE",
            },
        )

    if stage not in VALID_BASIC_STAGES:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_BASIC_STAGE",
                "stage": stage,
                "allowed": sorted(VALID_BASIC_STAGES),
            },
        )

    if rule_kind not in VALID_BASIC_RULE_KINDS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_BASIC_RULE_KIND",
                "rule_kind": rule_kind,
                "allowed": sorted(VALID_BASIC_RULE_KINDS),
            },
        )

    if stage == "index" and rule_kind != "basic_structure":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INDEX_STAGE_REQUIRES_BASIC_STRUCTURE",
                "stage": stage,
                "rule_kind": rule_kind,
            },
        )

    if stage != "index" and rule_kind != "basic":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "NON_INDEX_STAGE_REQUIRES_BASIC",
                "stage": stage,
                "rule_kind": rule_kind,
            },
        )

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 先寻找唯一 active package；如果不存在则自动创建。
            if scope_level == "global":
                cur.execute(
                    """
                    SELECT id, package_key, package_name
                    FROM basic_rule_packages
                    WHERE document_role = %s
                      AND scope_level = 'global'
                      AND object_scope IS NULL
                      AND status = 'active'
                    LIMIT 2;
                    """,
                    (document_role,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, package_key, package_name
                    FROM basic_rule_packages
                    WHERE document_role = %s
                      AND scope_level = 'object'
                      AND object_scope = %s
                      AND status = 'active'
                    LIMIT 2;
                    """,
                    (document_role, object_scope),
                )

            packages = cur.fetchall()

            if len(packages) > 1:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "ACTIVE_BASIC_PACKAGE_CONFLICT",
                        "document_role": document_role,
                        "scope_level": scope_level,
                        "object_scope": object_scope,
                    },
                )

            if packages:
                package_id, package_key, package_name = packages[0]
            else:
                if scope_level == "global":
                    package_key = f"basic:{document_role}:global"
                    package_name = f"{document_role}基础检查通用规则"
                else:
                    package_key = f"basic:{document_role}:object:{object_scope}"
                    package_name = f"{object_scope}{document_role}基础检查专用规则"

                cur.execute(
                    """
                    INSERT INTO basic_rule_packages (
                        package_key,
                        package_name,
                        document_role,
                        scope_level,
                        object_scope,
                        status
                    )
                    VALUES (%s, %s, %s, %s, %s, 'active')
                    RETURNING id;
                    """,
                    (
                        package_key,
                        package_name,
                        document_role,
                        scope_level,
                        object_scope,
                    ),
                )
                package_id = cur.fetchone()[0]

            cur.execute(
                """
                SELECT id, rule_id, current_version_id
                FROM basic_rules
                WHERE package_id = %s
                  AND stage = %s
                  AND rule_code = %s
                FOR UPDATE;
                """,
                (package_id, stage, rule_code),
            )
            rule_row = cur.fetchone()

            if rule_row is None:
                stable_rule_id = "TSB-" + uuid.uuid4().hex.upper()

                cur.execute(
                    """
                    INSERT INTO basic_rules (
                        rule_id,
                        package_id,
                        stage,
                        rule_code,
                        rule_title,
                        rule_kind
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, rule_id, current_version_id;
                    """,
                    (
                        stable_rule_id,
                        package_id,
                        stage,
                        rule_code,
                        rule_title,
                        rule_kind,
                    ),
                )
                rule_pk, stable_rule_id, current_version_id = cur.fetchone()
            else:
                rule_pk, stable_rule_id, current_version_id = rule_row

                cur.execute(
                    """
                    UPDATE basic_rules
                    SET rule_title = %s,
                        rule_kind = %s,
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (rule_title, rule_kind, rule_pk),
                )

            if current_version_id is not None:
                cur.execute(
                    """
                    SELECT version, content_hash, document_name, rule_status
                    FROM basic_rule_versions
                    WHERE id = %s;
                    """,
                    (current_version_id,),
                )
                current = cur.fetchone()

                if current is not None:
                    (
                        current_version,
                        current_hash,
                        current_document_name,
                        current_status,
                    ) = current

                    if current_hash == content_hash and current_status == "active":
                        return {
                            "success": True,
                            "action": "unchanged",
                            "package": {
                                "package_key": package_key,
                                "package_name": package_name,
                            },
                            "rule_id": stable_rule_id,
                            "scope_level": scope_level,
                            "object_scope": object_scope,
                            "stage": stage,
                            "rule_code": rule_code,
                            "rule_title": rule_title,
                            "rule_kind": rule_kind,
                            "version": current_version,
                            "status": "active",
                            "document_name": current_document_name,
                            "content_hash": content_hash,
                        }

            cur.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1
                FROM basic_rule_versions
                WHERE rule_id = %s;
                """,
                (rule_pk,),
            )
            next_version = cur.fetchone()[0]

            cur.execute(
                """
                UPDATE basic_rule_versions
                SET rule_status = 'superseded'
                WHERE rule_id = %s
                  AND rule_status = 'active';
                """,
                (rule_pk,),
            )

            cur.execute(
                """
                INSERT INTO basic_rule_versions (
                    rule_id,
                    version,
                    document_name,
                    rule_status,
                    content,
                    content_hash,
                    source,
                    published_at
                )
                VALUES (%s, %s, %s, 'active', %s, %s, %s, NOW())
                RETURNING id;
                """,
                (
                    rule_pk,
                    next_version,
                    document_name,
                    content,
                    content_hash,
                    source,
                ),
            )
            new_version_id = cur.fetchone()[0]

            cur.execute(
                """
                UPDATE basic_rules
                SET current_version_id = %s,
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (new_version_id, rule_pk),
            )

        conn.commit()

    return {
        "success": True,
        "action": "published",
        "package": {
            "package_key": package_key,
            "package_name": package_name,
        },
        "rule_id": stable_rule_id,
        "scope_level": scope_level,
        "object_scope": object_scope,
        "stage": stage,
        "rule_code": rule_code,
        "rule_title": rule_title,
        "rule_kind": rule_kind,
        "version": next_version,
        "status": "active",
        "document_name": document_name,
        "content_hash": content_hash,
    }


@app.post("/v1/rules/publish")
def publish_rule(req: RulePublishRequest):
    document_name = req.document_name.strip()
    object_scope = req.object_scope.strip()
    document_role = req.document_role.strip()
    part = req.part.strip()
    section_number = req.section_number.strip()
    section_title = req.section_title.strip()
    rule_kind = req.rule_kind.strip()
    source = req.source.strip() or "dify_pipeline"
    content = req.content.strip()

    required_fields = {
        "document_name": document_name,
        "object_scope": object_scope,
        "document_role": document_role,
        "part": part,
        "section_number": section_number,
        "section_title": section_title,
        "rule_kind": rule_kind,
        "content": content,
    }
    empty_fields = [
        field_name
        for field_name, value in required_fields.items()
        if not value
    ]

    if empty_fields:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "MISSING_REQUIRED_FIELDS",
                "fields": empty_fields,
            },
        )

    if rule_kind not in {"structure", "chapter"}:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_RULE_KIND",
                "rule_kind": rule_kind,
            },
        )

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, package_key, package_name
                FROM rule_packages
                WHERE object_scope = %s
                  AND document_role = %s
                  AND status = 'active'
                LIMIT 2;
                """,
                (object_scope, document_role),
            )
            packages = cur.fetchall()

            if not packages:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "ACTIVE_PACKAGE_NOT_FOUND",
                        "object_scope": object_scope,
                        "document_role": document_role,
                    },
                )

            if len(packages) > 1:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "ACTIVE_PACKAGE_CONFLICT",
                        "object_scope": object_scope,
                        "document_role": document_role,
                    },
                )

            package_id, package_key, package_name = packages[0]

            cur.execute(
                """
                SELECT id, rule_id, current_version_id
                FROM rules
                WHERE object_scope = %s
                  AND document_role = %s
                  AND part = %s
                  AND section_number = %s
                  AND rule_kind = %s
                FOR UPDATE;
                """,
                (
                    object_scope,
                    document_role,
                    part,
                    section_number,
                    rule_kind,
                ),
            )
            rule_row = cur.fetchone()

            if rule_row is None:
                stable_rule_id = "TSR-" + uuid.uuid4().hex.upper()

                cur.execute(
                    """
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
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, rule_id, current_version_id;
                    """,
                    (
                        stable_rule_id,
                        package_id,
                        object_scope,
                        document_role,
                        part,
                        section_number,
                        section_title,
                        rule_kind,
                    ),
                )
                rule_pk, stable_rule_id, current_version_id = cur.fetchone()
            else:
                rule_pk, stable_rule_id, current_version_id = rule_row

                cur.execute(
                    """
                    UPDATE rules
                    SET package_id = %s,
                        section_title = %s,
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (package_id, section_title, rule_pk),
                )

            if current_version_id is not None:
                cur.execute(
                    """
                    SELECT version, content_hash, document_name, rule_status
                    FROM rule_versions
                    WHERE id = %s;
                    """,
                    (current_version_id,),
                )
                current = cur.fetchone()

                if current is not None:
                    (
                        current_version,
                        current_hash,
                        current_document_name,
                        current_status,
                    ) = current

                    if current_hash == content_hash and current_status == "active":
                        return {
                            "success": True,
                            "action": "unchanged",
                            "package": {
                                "package_key": package_key,
                                "package_name": package_name,
                            },
                            "rule_id": stable_rule_id,
                            "version": current_version,
                            "status": "active",
                            "document_name": current_document_name,
                            "content_hash": content_hash,
                        }

            cur.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1
                FROM rule_versions
                WHERE rule_id = %s;
                """,
                (rule_pk,),
            )
            next_version = cur.fetchone()[0]

            cur.execute(
                """
                UPDATE rule_versions
                SET rule_status = 'superseded'
                WHERE rule_id = %s
                  AND rule_status = 'active';
                """,
                (rule_pk,),
            )

            cur.execute(
                """
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
                VALUES (%s, %s, %s, 'active', %s, %s, %s, NOW())
                RETURNING id;
                """,
                (
                    rule_pk,
                    next_version,
                    document_name,
                    content,
                    content_hash,
                    source,
                ),
            )
            new_version_id = cur.fetchone()[0]

            cur.execute(
                """
                UPDATE rules
                SET current_version_id = %s,
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (new_version_id, rule_pk),
            )

            publish_detail = {
                "document_name": document_name,
                "object_scope": object_scope,
                "document_role": document_role,
                "part": part,
                "section_number": section_number,
                "section_title": section_title,
                "rule_kind": rule_kind,
            }

            cur.execute(
                """
                INSERT INTO rule_publish_logs (
                    rule_id,
                    version,
                    action,
                    source,
                    content_hash,
                    detail
                )
                VALUES (%s, %s, 'publish', %s, %s, %s);
                """,
                (
                    stable_rule_id,
                    next_version,
                    source,
                    content_hash,
                    Jsonb(publish_detail),
                ),
            )

        conn.commit()

    return {
        "success": True,
        "action": "published",
        "package": {
            "package_key": package_key,
            "package_name": package_name,
        },
        "rule_id": stable_rule_id,
        "version": next_version,
        "status": "active",
        "document_name": document_name,
        "content_hash": content_hash,
    }

# ---------------------------------------------------------------------------
# 语义检索：嵌入模型与向量索引都由服务端持有，客户端只发查询文本。
# 客户端不配置嵌入模型，避免各自选模型导致查询向量与索引不在同一空间。
# ---------------------------------------------------------------------------


class SemanticSearchRequest(BaseModel):
    query: str
    object_scope: str = ""
    document_role: str = "采购技术规范书"
    rule_kind: str = "chapter"
    top_k: int = 24


class SemanticChunkRow(BaseModel):
    rule_domain: str
    document_role: str
    object_scope: str = ""
    part: str = ""
    section_number: str = ""
    section_title: str = ""
    rule_kind: str = ""
    scope_level: str = ""
    stage: str = ""
    rule_code: str = ""
    rule_title: str = ""
    rule_status: str = "active"
    chunk_index: int = 0
    content: str
    content_hash: str = ""


class SemanticChunksRequest(BaseModel):
    chunks: list[SemanticChunkRow]


@app.get("/v1/service-info")
def service_info():
    """客户端用来只读展示：嵌入模型、维度、索引规模。"""
    from . import semantic

    return semantic.service_info()


@app.post("/v1/rules/semantic-search")
def semantic_search(req: SemanticSearchRequest):
    """按查询文本召回候选规则分块；身份字段原样返回，客户端回 registry 取全文验真。"""
    from . import semantic

    query = (req.query or "").strip()
    if not 1 <= len(query) <= 250:
        raise HTTPException(status_code=400, detail={"code": "SEARCH_QUERY_LENGTH_INVALID"})
    try:
        hits = semantic.search(
            query,
            document_role=req.document_role,
            object_scope=req.object_scope,
            rule_kind=req.rule_kind,
            top_k=max(1, min(req.top_k, 100)),
        )
    except semantic.EmbeddingError as exc:
        raise HTTPException(status_code=503, detail={"code": str(exc)}) from None
    except psycopg.Error:
        raise HTTPException(status_code=500, detail={"code": "SEMANTIC_DATABASE_ERROR"}) from None
    return {"query": query, "count": len(hits), "candidates": hits}


@app.post("/v1/semantic/chunks")
def semantic_chunks(req: SemanticChunksRequest):
    """入库管道写完规则正文后，把分块推给服务端建索引（向量在服务端算）。"""
    from . import semantic

    try:
        written = semantic.upsert_chunks([row.model_dump() for row in req.chunks])
    except semantic.EmbeddingError as exc:
        raise HTTPException(status_code=503, detail={"code": str(exc)}) from None
    except psycopg.Error:
        raise HTTPException(status_code=500, detail={"code": "SEMANTIC_DATABASE_ERROR"}) from None
    return {"success": True, "indexed": written}

# ---------------------------------------------------------------------------
# 规则目录：客户端只通过 HTTP 读取，不再持有数据库连接串。
# ---------------------------------------------------------------------------


class RulePackagesRequest(BaseModel):
    document_role: str = "采购技术规范书"


@app.post("/v1/rule-packages/list")
def rule_packages(req: RulePackagesRequest):
    """列出已发布且生效的规则包（仅元数据，不含正文）。"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.package_key, p.package_name, p.object_scope, p.document_role,
                           count(r.id) AS active_rule_count
                    FROM rule_packages p
                    JOIN rules r ON r.package_id = p.id
                    JOIN rule_versions v ON v.id = r.current_version_id
                    WHERE p.status = 'active' AND v.rule_status = 'active'
                      AND p.document_role = %s
                    GROUP BY p.id, p.package_key, p.package_name, p.object_scope, p.document_role
                    ORDER BY p.object_scope, p.package_key
                    """,
                    (req.document_role,),
                )
                packages = [
                    {
                        "package_key": row[0],
                        "package_name": row[1],
                        "object_scope": row[2],
                        "document_role": row[3],
                        "active_rule_count": int(row[4]),
                    }
                    for row in cur.fetchall()
                ]
    except psycopg.Error:
        raise HTTPException(status_code=500, detail={"code": "CATALOG_DATABASE_ERROR"}) from None
    return {"document_role": req.document_role, "count": len(packages), "packages": packages}

# ---------------------------------------------------------------------------
# 访问控制：默认只绑本机；一旦对外监听就必须带令牌。
# 写接口（publish / chunks）能改规则与索引，未鉴权的局域网暴露等于把规则库交出去。
# ---------------------------------------------------------------------------

import hmac as _hmac

from fastapi import Request as _Request
from fastapi.responses import JSONResponse as _JSONResponse

_ACCESS_TOKEN = os.getenv("RULE_SERVER_TOKEN", "")
_MAX_BODY_BYTES = int(os.getenv("RULE_SERVER_MAX_BODY_BYTES", str(32 * 1024 * 1024)))
_PUBLIC_PATHS = {"/health"}


@app.middleware("http")
async def _guard(request: _Request, call_next):
    if request.url.path in _PUBLIC_PATHS:
        return await call_next(request)
    if _ACCESS_TOKEN:
        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not _hmac.compare_digest(supplied, _ACCESS_TOKEN):
            return _JSONResponse({"detail": {"code": "AUTH_REQUIRED"}}, status_code=401)
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > _MAX_BODY_BYTES:
        return _JSONResponse({"detail": {"code": "PAYLOAD_TOO_LARGE"}}, status_code=413)
    return await call_next(request)
