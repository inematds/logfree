"""APScheduler in-process com jobs do MVP (PLAN.md item 26)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.audit import verify_chain
from app.core.telemetry import emit
from app.db.base import get_sessionmaker
from app.jobs.anonimizacao import anonimizar
from app.jobs.cobertura import alertar_se_baixa

logger = logging.getLogger("logfree.jobs")


def _run_audit_check() -> None:
    sm = get_sessionmaker()
    with sm() as s:
        res = verify_chain(s)
    if not res.ok and res.motivo == "chain_break":
        emit("audit.chain_break", first_id=res.primeiro_id_quebrado)
    elif not res.ok:
        emit("audit.verifier_error", motivo=res.motivo[:60])


def _run_anonimizacao() -> None:
    sm = get_sessionmaker()
    with sm() as s:
        n = anonimizar(s)
    logger.info("anonimizacao: %s rows", n)


def _run_cobertura() -> None:
    sm = get_sessionmaker()
    with sm() as s:
        alertar_se_baixa(s)


def build_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler()
    sched.add_job(_run_audit_check, "interval", hours=24, id="audit_check")
    sched.add_job(_run_anonimizacao, "interval", hours=24, id="anonimizacao")
    sched.add_job(_run_cobertura, "interval", hours=1, id="cobertura")
    return sched
