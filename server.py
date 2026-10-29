"""规则服务端：嵌入式 PostgreSQL + Registry API + 语义索引。

一个进程同时持有数据库与 HTTP 接口，客户端只通过 HTTP 访问。
用法：
    python server.py serve      # 前台启动（脚本会用这个）
    python server.py stop       # 停止（连同数据库子进程）
    python server.py status     # 查看是否在跑
    python server.py info       # 打印服务信息（嵌入模型、索引规模）
    python server.py import <路径>   # 导入规则（需服务已在跑）
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("RULE_SERVER_DATA_DIR", str(ROOT / "data")))
PG_DIR = DATA_DIR / "pg"
SQL_DIR = ROOT / "db" / "sql"
LOG_DIR = DATA_DIR / "logs"
PID_FILE = DATA_DIR / "server.pid"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8010


def load_env() -> None:
    """读 .env（KEY=VALUE，忽略注释与空行）。已存在的环境变量优先。"""
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


def base_url() -> str:
    host = os.getenv("RULE_SERVER_HOST", DEFAULT_HOST)
    port = int(os.getenv("RULE_SERVER_PORT", str(DEFAULT_PORT)))
    return f"http://{host}:{port}"


def _http():
    import httpx

    return httpx.Client(trust_env=False, timeout=httpx.Timeout(30, connect=5))


def cmd_serve() -> int:
    load_env()
    sys.path.insert(0, str(ROOT))

    host = os.getenv("RULE_SERVER_HOST", DEFAULT_HOST)
    token = os.getenv("RULE_SERVER_TOKEN", "")
    loopback = host in {"127.0.0.1", "::1", "localhost"}
    if not loopback and not token:
        # 规则库能被写就相当于规则权威；对外监听却无鉴权是明确的配置错误。
        print(
            "[server] 拒绝启动：RULE_SERVER_HOST 不是回环地址，但未设置 RULE_SERVER_TOKEN。\n"
            "         规则库可被任意来源改写会污染审查依据。请设置令牌后重启，"
            "并在客户端设置里填同一个“服务令牌”。",
            file=sys.stderr,
        )
        return 2

    from db.postgres import start

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle = start(PG_DIR, SQL_DIR, LOG_DIR)
    os.environ["DATABASE_URL"] = handle.uri
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    import uvicorn

    # 新版 mcp SDK 把 MCPServer 改名、streamable_http_app 签名也变了；
    # compat 负责让 registry 的 app 能导入，http_only 跳过它内嵌 MCP 的 lifespan
    # （客户端连的是插件自己的 MCP 服务，registry 这边只提供 HTTP）。
    from compat import http_only, install

    install()
    from app.main import app

    http_only(app)

    host = os.getenv("RULE_SERVER_HOST", DEFAULT_HOST)
    port = int(os.getenv("RULE_SERVER_PORT", str(DEFAULT_PORT)))

    def shutdown(*_args) -> None:
        handle.stop()
        PID_FILE.unlink(missing_ok=True)

    signal.signal(signal.SIGTERM, lambda *_: (shutdown(), sys.exit(0)))
    signal.signal(signal.SIGINT, lambda *_: (shutdown(), sys.exit(0)))

    print(f"[server] registry on {base_url()}", flush=True)
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        shutdown()
    return 0


def cmd_stop() -> int:
    if not PID_FILE.exists():
        print("未运行（没有 pid 文件）")
        return 1
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        PID_FILE.unlink(missing_ok=True)
        print("pid 文件损坏，已清理")
        return 1
    import subprocess

    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    PID_FILE.unlink(missing_ok=True)
    print(f"已停止（pid {pid}）")
    return 0


def cmd_status() -> int:
    url = base_url()
    try:
        with _http() as http:
            health = http.get(url + "/health")
            info = http.get(url + "/v1/service-info").json()
    except Exception as exc:  # noqa: BLE001 - 状态查询失败就是没跑
        print(f"未运行（{url} 不可达：{type(exc).__name__}）")
        return 1
    if health.status_code != 200:
        print(f"异常：/health 返回 {health.status_code}")
        return 1
    print(f"运行中：{url}")
    print(f"  嵌入模型：{info.get('embedding_model') or '未配置'}（{info.get('embedding_dimensions')} 维）")
    print(f"  索引分块：{info.get('chunks')}（章节 {info.get('chapter_chunks')} / 基础 {info.get('basic_chunks')}）")
    print(f"  生效规则：{info.get('active_rules')}")
    return 0


def cmd_info() -> int:
    try:
        with _http() as http:
            print(json.dumps(http.get(base_url() + "/v1/service-info").json(), ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001
        print(f"未运行：{type(exc).__name__}")
        return 1
    return 0


def cmd_import(target: str) -> int:
    from pipeline.importer import import_path

    path = Path(target)
    if not path.exists():
        print(f"路径不存在：{path}")
        return 1
    print(f"导入到 {base_url()}", flush=True)
    ok, failed = import_path(path, base_url())
    print(f"完成：成功 {ok}，失败 {failed}", flush=True)
    return 0 if failed == 0 else 1


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "serve"
    if command == "serve":
        return cmd_serve()
    if command == "stop":
        return cmd_stop()
    if command == "status":
        return cmd_status()
    if command == "info":
        return cmd_info()
    if command == "import":
        if len(argv) < 3:
            print("用法：python server.py import <规则 md 文件或目录>")
            return 2
        return cmd_import(argv[2])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
