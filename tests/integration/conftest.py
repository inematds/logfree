"""Conftest dos testes de integração: DB temporário com migrations + seed."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_env(monkeypatch):
    """Ambiente isolado: tmp dir, env vars, settings cache limpa."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "logfree.sqlite"
        db_url = f"sqlite:///{db_path}"
        monkeypatch.setenv("DATABASE_URL", db_url)
        monkeypatch.setenv("ENV", "test")
        monkeypatch.setenv("DATA_DIR", str(tmp))
        monkeypatch.setenv("BOT_SERVICE_TOKEN", "test-bot-token")
        monkeypatch.setenv(
            "COOKIE_SECRET", "test-secret-32-bytes-padding-padding"
        )
        monkeypatch.setenv("AUDIT_UNBLOCK_KEY", "test-unblock")

        from app.core.settings import get_settings
        get_settings.cache_clear()
        from app.db import base as db_base
        db_base.reset_engine_for_tests()

        # Roda migrations no DB temporário
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            check=True,
            env={**__import__("os").environ, "DATABASE_URL": db_url},
        )

        # Roda seed
        from app.db.seed import run_seed
        run_seed()

        yield {"url": db_url, "tmp": tmp}


@pytest.fixture
def client(tmp_env):
    from fastapi.testclient import TestClient

    from app.api.main import app

    with TestClient(app) as c:
        yield c
