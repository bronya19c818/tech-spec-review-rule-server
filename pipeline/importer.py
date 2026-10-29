"""规则入库管道：Markdown → 清洗 → 发布 Registry → 建语义索引。

与服务端是 HTTP 关系，不直接连库：数据库只由 server.py 持有，
入库只是它的一个客户端。这样凭据、迁移、连接池都留在服务端一处。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import httpx

from . import rule_clean

_ROUTE_RE = re.compile(r"<!--\s*ROUTE:\s*(.*?)-->", re.S)


def parse_route_chunks(cleaned_text: str) -> list[dict[str, Any]]:
    """从 cleaned_text 的 ROUTE 注释切出每个子块及其身份。"""
    chunks: list[dict[str, Any]] = []
    for block in re.split(r"\n{2,}", cleaned_text or ""):
        block = block.strip()
        if not block:
            continue
        match = _ROUTE_RE.match(block)
        if not match:
            continue
        fields: dict[str, str] = {}
        for piece in match.group(1).split("|"):
            if "=" in piece:
                key, value = piece.split("=", 1)
                fields[key.strip()] = value.strip()
        content = block[match.end():].lstrip("\n").strip()
        if content:
            chunks.append({"identity": fields, "content": content})
    return chunks


def publish_payload(result: dict[str, Any]) -> dict[str, Any]:
    """按 Registry 的字段契约构造发布载荷。"""
    if result["rule_domain"] == "basic_check":
        return {
            "document_name": result["document_name"],
            "document_role": result["document_role"],
            "scope_level": result["scope_level"],
            "object_scope": result["object_scope"] or None,
            "stage": result["stage"],
            "rule_code": result["rule_code"],
            "rule_title": result["rule_title"],
            "rule_kind": result["rule_kind"],
            "content": result["registry_content"],
            "source": "rule_server_import",
        }
    return {
        "document_name": result["document_name"],
        "object_scope": result["object_scope"],
        "document_role": result["document_role"],
        "part": result["part"],
        "section_number": result["section_number"],
        "section_title": result["section_title"],
        "rule_kind": result["rule_kind"],
        "content": result["registry_content"],
        "source": "rule_server_import",
    }


def publish_path(base_url: str, rule_domain: str) -> str:
    base = base_url.rstrip("/")
    if rule_domain == "basic_check":
        return base + "/v1/basic-rules/publish"
    if rule_domain == "chapter_review":
        return base + "/v1/rules/publish"
    raise ValueError(f"未知 rule_domain：{rule_domain}")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def import_file(path: Path, base_url: str, http: httpx.Client) -> dict[str, Any]:
    """导入一份规则 Markdown。"""
    result = rule_clean.main(path.read_text(encoding="utf-8"))

    response = http.post(publish_path(base_url, result["rule_domain"]), json=publish_payload(result))
    if not response.is_success:
        raise RuntimeError(f"RULES_PUBLISH_HTTP_{response.status_code}: {response.text[:300]}")
    published = response.json()
    if not published.get("success"):
        raise RuntimeError(f"RULES_PUBLISH_REJECTED: {published}")

    chunks = parse_route_chunks(result["cleaned_text"])
    rows = []
    for index, chunk in enumerate(chunks):
        identity = chunk["identity"]
        rows.append({
            "rule_domain": result["rule_domain"],
            "document_role": identity.get("document_role", result["document_role"]),
            "object_scope": identity.get("object_scope", result["object_scope"]) or "",
            "part": identity.get("part", result["part"]) or "",
            "section_number": identity.get("section_number", result["section_number"]) or "",
            "section_title": identity.get("section_title", result["section_title"]) or "",
            "rule_kind": identity.get("rule_kind", result["rule_kind"]) or "",
            "scope_level": identity.get("scope_level", result["scope_level"]) or "",
            "stage": identity.get("stage", result["stage"]) or "",
            "rule_code": identity.get("rule_code", result["rule_code"]) or "",
            "rule_title": identity.get("rule_title", result["rule_title"]) or "",
            "rule_status": "active",
            "chunk_index": index,
            "content": chunk["content"],
            "content_hash": _hash(chunk["content"]),
        })
    indexed = 0
    if rows:
        chunk_response = http.post(base_url.rstrip("/") + "/v1/semantic/chunks", json={"chunks": rows})
        if not chunk_response.is_success:
            raise RuntimeError(
                f"SEMANTIC_INDEX_HTTP_{chunk_response.status_code}: {chunk_response.text[:300]}"
            )
        indexed = int(chunk_response.json().get("indexed") or 0)

    return {
        "document_name": result["document_name"],
        "rule_domain": result["rule_domain"],
        "action": published.get("action"),
        "version": published.get("version"),
        "chunks": len(chunks),
        "indexed": indexed,
    }


def import_path(target: Path, base_url: str) -> tuple[int, int]:
    """导入单个文件或目录，返回 (成功数, 失败数)。"""
    files = (
        [target]
        if target.is_file()
        else sorted(p for p in target.rglob("*") if p.suffix.lower() in {".md", ".markdown"})
    )
    ok = failed = 0
    with httpx.Client(trust_env=False, timeout=httpx.Timeout(180, connect=10)) as http:
        for path in files:
            try:
                result = import_file(path, base_url, http)
                ok += 1
                print(
                    f"  [{result['rule_domain']}] {result['document_name']} "
                    f"chunks={result['chunks']} action={result['action']}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001 - 单份规则失败不该中断整批
                failed += 1
                print(f"  FAILED {path.name}: {type(exc).__name__}: {exc}", flush=True)
    return ok, failed
