"""Aplicação FastAPI principal — owner lease + healthcheck + endpoints."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.api import melhor_posto
from app.bot import webhook as bot_webhook
from app.core.audit import verify_chain
from app.core.owner_lease import OwnerLease
from app.core.settings import get_settings
from app.core.telemetry import emit, metrics_snapshot
from app.db.base import get_engine, get_sessionmaker
from app.web import admin as admin_web

logger = logging.getLogger("logfree.api")


_state: dict = {"lease": None, "ready": False, "audit_ok": True}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = get_engine()

    # Smoke check do DB
    with engine.connect() as c:
        head = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if not head:
            raise RuntimeError("alembic_version vazio — rode `alembic upgrade head`")

    lease = OwnerLease(engine)
    if not settings.is_test:
        await lease.start()
    _state["lease"] = lease

    # Audit chain ok?
    sm = get_sessionmaker()
    with sm() as s:
        res = verify_chain(s)
        _state["audit_ok"] = res.ok
        if not res.ok and res.motivo == "chain_break":
            emit("audit.chain_break", first_id=res.primeiro_id_quebrado)

    _state["ready"] = True
    emit("deploy.ready", git_sha="dev")
    try:
        yield
    finally:
        _state["ready"] = False
        if _state["lease"] and not settings.is_test:
            await _state["lease"].stop()


app = FastAPI(title="LogFree API", version="0.1.0", lifespan=lifespan)
app.include_router(melhor_posto.router)
app.include_router(bot_webhook.router)
app.include_router(admin_web.router)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://unpkg.com 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'"
    )
    if get_settings().is_prod:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/internal/ready")
async def ready():
    if not _state["ready"]:
        raise HTTPException(503, detail="not ready")
    if not _state["audit_ok"]:
        raise HTTPException(503, detail="audit_quarantine")
    return {"status": "ok"}


@app.get("/internal/metrics")
async def metrics():
    return metrics_snapshot()
