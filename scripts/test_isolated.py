"""Run each legacy script/pytest module in its own seeded, offline process.

Usage: python scripts/test_isolated.py test_kaufcheck_root_cause.py ...
No arguments: all root test_*.py files. Logs and databases stay in TEMP.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def child(filename: str) -> None:
    import runpy
    import socket
    import sqlite3
    import threading

    isolation = Path(tempfile.mkdtemp(prefix="enfal_offline_"))
    # Prevent dotenv and inherited credentials from enabling real services.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    for name in ("GEMINI_API_KEY", "TAVILY_API_KEY", "STRIPE_SECRET_KEY",
                 "STRIPE_WEBHOOK_SECRET", "MOBILE_DE_USERNAME", "MOBILE_DE_PASSWORD",
                 "AUTO_KI_BREVO_API_KEY", "AUTO_KI_ALLOWED_MARKET_SOURCES"):
        os.environ[name] = ""
    # Signature tests need a configured value, but never contact Stripe.
    os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_offline_test_only"
    for name, path in (("AUTO_KI_DB_PATH", "test.db"), ("AUTO_KI_CHROMA_PATH", "chroma"),
                       ("AUTO_KI_DB_BACKUP_DIR", "backups")):
        os.environ[name] = str(isolation / path)
    os.environ["AUTO_KI_JWT_SECRET"] = "offline-test-secret-not-for-production-000000"
    # Keep the application's documented development API-key default: several
    # legacy endpoint tests intentionally authenticate with that exact value.
    os.environ.pop("AUTO_KI_API_KEY", None)
    import dotenv
    dotenv.load_dotenv = lambda *a, **kw: False

    def blocked(*args, **kwargs):
        raise RuntimeError("OFFLINE_GUARD: network disabled for isolated tests")

    # Windows implements asyncio's local wakeup socketpair through TCP loopback.
    # Permit only that standard-library operation, never arbitrary localhost HTTP.
    pair_state = threading.local()
    original_pair, original_socket_connect = socket.socketpair, socket.socket.connect

    def socketpair(*args, **kwargs):
        pair_state.active = True
        try:
            return original_pair(*args, **kwargs)
        finally:
            pair_state.active = False

    def socket_connect(sock, address):
        if getattr(pair_state, "active", False):
            return original_socket_connect(sock, address)
        return blocked()

    socket.socketpair = socketpair
    socket.socket.connect = socket_connect
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    original_connect = sqlite3.connect
    live = (Path(os.environ.get("LOCALAPPDATA", "")) / "auto-ki-backend").resolve()

    def connect(database, *args, **kwargs):
        if database != ":memory:" and live in Path(str(database)).resolve().parents:
            raise RuntimeError("OFFLINE_GUARD: live database access forbidden")
        return original_connect(database, *args, **kwargs)

    sqlite3.connect = connect
    sys.path.insert(0, str(ROOT))
    from app import database
    database.ensure_tables()
    # Older tests change environment and reload app modules themselves.
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    source = (ROOT / filename).read_text(encoding="utf-8-sig")
    if "TextIOWrapper" not in source and ("import pytest" in source or "from pytest" in source or "def test_" in source):
        import pytest
        raise SystemExit(pytest.main([filename, "-q", "--tb=short"]))
    runpy.run_path(str(ROOT / filename), run_name="__main__")


if __name__ == "__main__":
    os.chdir(ROOT)
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(sys.argv[2])
    else:
        import json
        logdir = Path(tempfile.mkdtemp(prefix="enfal_suite_"))
        print(f"Logs: {logdir}", flush=True)
        results = {}
        for filename in sys.argv[1:] or sorted(p.name for p in ROOT.glob("test_*.py")):
            with (logdir / f"{filename}.log").open("w", encoding="utf-8") as output:
                try:
                    result = subprocess.run([sys.executable, __file__, "--child", filename],
                                            stdout=output, stderr=subprocess.STDOUT, timeout=180)
                    code = result.returncode
                except subprocess.TimeoutExpired:
                    code = "timeout"
            results[filename] = code
            print(f"{filename}: {code}", flush=True)
        (logdir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        raise SystemExit(int(any(code != 0 for code in results.values())))
