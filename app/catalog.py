"""Read-only catalog extension for the existing technical-spec Rule Registry MCP."""

import psycopg
from typing import Any


def register_catalog(mcp, get_conn):
    @mcp.tool(name="list_rule_packages", structured_output=True)
    def list_rule_packages(document_role: str = "采购技术规范书") -> dict[str, Any]:
        """列出当前文档角色的 active 物资规则包、别名与规则数量，不返回规则正文。"""
        try:
            return query_catalog(document_role)
        except psycopg.Error:
            raise RuntimeError("CATALOG_DATABASE_ERROR") from None

    def query_catalog(document_role: str) -> dict:
        with get_conn() as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.package_key, p.package_name, p.object_scope,
                           p.document_role, p.status, count(r.id),
                           ARRAY(SELECT a.alias FROM object_scope_aliases a
                                 WHERE a.object_scope = p.object_scope
                                   AND a.status = 'active' AND a.match_type = 'exact'
                                 ORDER BY a.priority DESC, a.alias)
                    FROM rule_packages p
                    JOIN rules r ON r.package_id = p.id
                    JOIN rule_versions v ON v.id = r.current_version_id
                    WHERE p.status = 'active' AND v.rule_status = 'active'
                      AND p.document_role = %s
                    GROUP BY p.id, p.package_key, p.package_name,
                             p.object_scope, p.document_role, p.status
                    ORDER BY p.object_scope, p.package_key
                    """, (document_role,))
                names = ("package_key", "package_name", "object_scope", "document_role",
                         "status", "active_rule_count", "aliases")
                return {"packages": [dict(zip(names, row, strict=True)) for row in cur.fetchall()]}
