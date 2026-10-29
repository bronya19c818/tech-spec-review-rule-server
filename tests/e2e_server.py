"""端到端验证：起服务 → 导入规则 → 目录/取规则/语义检索 → 出报告。

只跑一条真实链路，不做单元级拆分：规则服务的行为只有"导入后能被检索到"才算成立。
需要嵌入服务（读 .env 或环境变量）；没有密钥时整体跳过而不是失败。

用法：
    python tests/e2e_server.py            # 跑一次，产物写到 artifacts/
    python tests/e2e_server.py --keep     # 保留临时数据目录便于排查
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "净水器—通用部分—09.01样章—审查规则.md"
ARTIFACTS = ROOT / "artifacts"

sys.path.insert(0, str(ROOT))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


def wait_health(base: str, timeout_s: float = 180.0) -> dict:
    import httpx

    deadline = time.time() + timeout_s
    last: Exception | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(base + "/health", timeout=3, trust_env=False)
            if response.status_code == 200:
                return response.json()
        except Exception as exc:  # noqa: BLE001 - 启动期连接失败是正常的
            last = exc
        time.sleep(1)
    raise RuntimeError(f"SERVER_NOT_READY: {last}")


def stop_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, text=True,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()


def main(argv: list[str]) -> int:
    keep = "--keep" in argv
    load_env()

    if not os.getenv("TSR_EMBEDDING_MODEL") or not os.getenv("TSR_EMBEDDING_API_KEY"):
        print("SKIP: 未配置 TSR_EMBEDDING_MODEL / TSR_EMBEDDING_API_KEY（嵌入服务不可用时无法建索引）")
        return 2

    data_dir = Path(tempfile.mkdtemp(prefix="rule-server-e2e-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "RULE_SERVER_HOST": "127.0.0.1",
        "RULE_SERVER_PORT": str(port),
        "RULE_SERVER_DATA_DIR": str(data_dir),
    }
    env.pop("RULE_SERVER_TOKEN", None)  # 本机回环，不带令牌

    steps: list[dict] = []
    started = time.time()

    def record(name: str, ok: bool, detail: object = "") -> None:
        steps.append({"name": name, "ok": ok, "detail": detail})
        print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f" -> {detail}"))

    process = subprocess.Popen(
        [sys.executable, "server.py", "serve"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    try:
        health = wait_health(base)
        record("服务就绪", health.get("status") == "ok", health)

        from pipeline.importer import import_path

        ok, failed = import_path(FIXTURE, base)
        record("导入规则", ok == 1 and failed == 0, {"ok": ok, "failed": failed})

        import httpx

        client = httpx.Client(trust_env=False, timeout=30)
        info = client.get(base + "/v1/service-info").json()
        record(
            "索引已建立",
            bool(info.get("embedding_configured")) and int(info.get("chunks") or 0) >= 1,
            {"model": info.get("embedding_model"), "chunks": info.get("chunks")},
        )

        packages = client.post(
            base + "/v1/rule-packages/list", json={"document_role": "采购技术规范书"}
        ).json()
        scopes = [row.get("object_scope") for row in packages.get("packages") or []]
        record("目录可读", "净水器" in scopes, {"packages": scopes})

        rule = client.post(
            base + "/v1/rules/get",
            json={
                "object_scope": "净水器", "part": "通用部分", "section_number": "9.1",
                "rule_kind": "chapter", "document_role": "采购技术规范书",
            },
        ).json()
        body = rule.get("rule") or {}
        record(
            "取回规则正文",
            body.get("section_title") == "样章" and "滤芯使用寿命" in (body.get("content") or ""),
            {"section_title": body.get("section_title"), "version": body.get("version")},
        )

        hits = client.post(
            base + "/v1/rules/semantic-search",
            json={
                "query": "滤芯更换提示与使用寿命要求", "object_scope": "净水器",
                "document_role": "采购技术规范书", "rule_kind": "chapter", "top_k": 5,
            },
        ).json()
        candidates = hits.get("candidates") or []
        top = candidates[0] if candidates else {}
        record(
            "语义检索命中导入的规则",
            bool(candidates) and top.get("section_number") == "9.1",
            {"count": len(candidates), "top": top.get("section_number"), "score": top.get("score")},
        )

        # 幂等：同一份规则再导一次应报 unchanged，且分块数不增长
        before = int(client.get(base + "/v1/service-info").json().get("chunks") or 0)
        again_ok, again_failed = import_path(FIXTURE, base)
        after = int(client.get(base + "/v1/service-info").json().get("chunks") or 0)
        record(
            "重复导入幂等",
            again_ok == 1 and again_failed == 0 and after == before,
            {"chunks_before": before, "chunks_after": after},
        )
    except Exception as exc:  # noqa: BLE001 - 报告要记录失败原因而不是崩栈
        record("执行链路", False, f"{type(exc).__name__}: {exc}")
    finally:
        stop_tree(process)
        if not keep:
            shutil.rmtree(data_dir, ignore_errors=True)

    duration_ms = int((time.time() - started) * 1000)
    passed = all(step["ok"] for step in steps) and bool(steps)
    report = {
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "durationMs": duration_ms,
        "port": port,
        "dataDir": str(data_dir) if keep else "(removed)",
        "passed": passed,
        "steps": steps,
    }
    ARTIFACTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = ARTIFACTS / f"e2e-rule-server-{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'E2E PASSED' if passed else 'E2E FAILED'}  {duration_ms}ms  报告: {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
