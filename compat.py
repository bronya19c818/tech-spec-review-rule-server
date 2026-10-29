"""兼容层：让 registry 服务适配新版 mcp SDK。

registry 的 app/main.py 是按旧版 mcp SDK 写的：
  from mcp.server import MCPServer
  mcp = MCPServer("...")
  mcp.streamable_http_app(streamable_http_path="/", json_response=True,
                          stateless_http=True, host="0.0.0.0")

新版 mcp（1.30）把 MCPServer 改名为 FastMCP，且 streamable_http_app()
不再接受上述关键字参数。这里在 import registry app 之前做最小 patch：
  - 用 FastMCP 别名替代 MCPServer
  - 包装 streamable_http_app，丢弃旧关键字参数（新版在默认路径即为 /mcp 挂载）
这样无需改动原 main.py 的正文。
"""
from __future__ import annotations

import functools


def install() -> None:
    import mcp.server as _server

    if not hasattr(_server, "MCPServer"):
        _server.MCPServer = _server.FastMCP  # type: ignore[attr-defined]

    from mcp.server import FastMCP

    original = FastMCP.streamable_http_app

    @functools.wraps(original)
    def _tolerant(self, *args, **kwargs):
        kwargs.pop("streamable_http_path", None)
        kwargs.pop("json_response", None)
        kwargs.pop("stateless_http", None)
        kwargs.pop("host", None)
        kwargs.pop("port", None)
        return original(self)

    FastMCP.streamable_http_app = _tolerant  # type: ignore[assignment]

    # registry 还用到 mcp.server.mcpserver.exceptions.ToolError
    try:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: F401
        import sys
        import types

        if "mcp.server.mcpserver" not in sys.modules:
            pkg = types.ModuleType("mcp.server.mcpserver")
            exc = types.ModuleType("mcp.server.mcpserver.exceptions")
            exc.ToolError = ToolError
            pkg.exceptions = exc
            sys.modules["mcp.server.mcpserver"] = pkg
            sys.modules["mcp.server.mcpserver.exceptions"] = exc
    except Exception:
        pass


def http_only(app):
    """把 registry 的 FastAPI app 改成只跑 HTTP：去掉内嵌 MCP 的 lifespan。

    内嵌 MCP 的 session_manager 需要 anyio 事件循环，在本地一体化进程里与
    uvicorn 线程冲突会导致启动卡住。registry 的 /v1/* 端点不依赖它。
    """
    import contextlib

    @contextlib.asynccontextmanager
    async def _lifespan(_app):
        yield

    app.router.lifespan_context = _lifespan
    return app


