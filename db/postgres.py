"""嵌入式 PostgreSQL 的启动与复用。

规则库是共享资产，所以数据库随服务端走：数据目录在服务端目录下，
不写进任何客户端。启动方式直接起 postgres.exe 并轮询连通性 ——
这台机器上 pg_ctl -w 会一直不返回（库其实已经起来），误判会触发重建。
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import psycopg


def pg_bin(name: str) -> Path:
    import pgserver

    return Path(pgserver.__file__).parent / "pginstall" / "bin" / name


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def probe(uri: str, timeout_s: float = 2.0) -> bool:
    try:
        with psycopg.connect(uri, connect_timeout=int(timeout_s)) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 - 探测只回答"能不能连"
        return False


class PostgresHandle:
    """postgres.exe 子进程；复用外部实例时 process 为 None。"""

    def __init__(self, uri: str, process: subprocess.Popen | None):
        self.uri = uri
        self._process = process

    def stop(self) -> None:
        if self._process is None:
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=20)
        except Exception:  # noqa: BLE001 - 退出清理失败不应掩盖调用方结果
            pass


def apply_sql(pg_dir: Path, uri: str, sql_dir: Path) -> None:
    """按文件名顺序执行建库脚本；成功后写标记，避免重复执行非幂等语句。"""
    marker = pg_dir / ".schema_initialized"
    if marker.exists():
        return
    files = sorted(
        (p for p in sql_dir.glob("*.sql") if p.name[:3].isdigit() or p.name == "init.sql"),
        key=lambda p: ("000" if p.name == "init.sql" else p.name),
    )
    with psycopg.connect(uri, connect_timeout=15) as conn:
        for path in files:
            with conn.cursor() as cur:
                cur.execute(path.read_text(encoding="utf-8"))
            conn.commit()
            print(f"[db] applied {path.name}", flush=True)
    marker.write_text("ok", encoding="utf-8")


def start(pg_dir: Path, sql_dir: Path, log_dir: Path) -> PostgresHandle:
    """启动或复用嵌入式 PostgreSQL，返回句柄（含连接串）。"""
    pg_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    pid_file = pg_dir / "postmaster.pid"
    if pid_file.exists():
        try:
            port = int(pid_file.read_text(encoding="utf-8", errors="replace").splitlines()[3].strip())
            uri = f"postgresql://postgres:@127.0.0.1:{port}/postgres"
            if probe(uri):
                print(f"[db] reused existing instance at {uri}", flush=True)
                apply_sql(pg_dir, uri, sql_dir)
                return PostgresHandle(uri, None)
        except (OSError, IndexError, ValueError):
            pass

    last: Exception | None = None
    process: subprocess.Popen | None = None
    for attempt in range(1, 4):
        try:
            if not (pg_dir / "PG_VERSION").exists():
                print("[db] initdb (first run)", flush=True)
                shutil.rmtree(pg_dir, ignore_errors=True)
                pg_dir.mkdir(parents=True, exist_ok=True)
                result = subprocess.run(
                    [str(pg_bin("initdb.exe" if os.name == "nt" else "initdb")),
                     "-D", str(pg_dir), "--auth=trust", "--auth-local=trust",
                     "--encoding=utf8", "-U", "postgres"],
                    capture_output=True, text=True, timeout=300,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"INITDB_FAILED: {result.stderr[-400:]}")

            port = free_port()
            uri = f"postgresql://postgres:@127.0.0.1:{port}/postgres"
            log_handle = open(log_dir / "postgres.log", "a", encoding="utf-8")
            process = subprocess.Popen(
                [str(pg_bin("postgres.exe" if os.name == "nt" else "postgres")),
                 "-D", str(pg_dir), "-h", "127.0.0.1", "-p", str(port)],
                stdin=subprocess.DEVNULL, stdout=log_handle, stderr=log_handle,
                cwd=str(pg_dir), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = time.time() + 60
            while time.time() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"POSTGRES_EXITED:{process.returncode}")
                if probe(uri):
                    print(f"[db] postgres at {uri}", flush=True)
                    apply_sql(pg_dir, uri, sql_dir)
                    return PostgresHandle(uri, process)
                time.sleep(0.5)
            raise RuntimeError("POSTGRES_CONNECT_TIMEOUT")
        except Exception as exc:  # noqa: BLE001 - 启动失败统一重建后重试
            last = exc
            print(f"[db] start failed (attempt {attempt}/3): {exc}", flush=True)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except Exception:  # noqa: BLE001 - 清理失败继续走重建
                    pass
            shutil.rmtree(pg_dir, ignore_errors=True)
            pg_dir.mkdir(parents=True, exist_ok=True)
            time.sleep(2)
    raise RuntimeError(f"POSTGRES_START_FAILED: {last}")
