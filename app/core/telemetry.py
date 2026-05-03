"""Telemetria allowlisted (PLAN.md item 40).

Eventos têm schema_version e só campos declarados saem. Coordenadas sempre
arredondadas a 3 casas (~110m) ou hash. Sem payload bruto.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("logfree.telemetry")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(h)


def hash_id(value: int | str | None) -> str:
    if value is None:
        return ""
    return hashlib.sha256(str(value).encode()).hexdigest()[:12]


def round3(x: float | None) -> float | None:
    if x is None:
        return None
    return round(float(x), 3)


# In-memory metrics (Prometheus-style counters/gauges).
_counters: dict[str, int] = {}
_gauges: dict[str, float] = {}


def incr(name: str, by: int = 1) -> None:
    _counters[name] = _counters.get(name, 0) + by


def gauge_set(name: str, value: float) -> None:
    _gauges[name] = value


def metrics_snapshot() -> dict[str, Any]:
    return {"counters": dict(_counters), "gauges": dict(_gauges)}


def reset_metrics() -> None:
    _counters.clear()
    _gauges.clear()


@dataclass
class Event:
    name: str
    schema_version: int = 1
    fields: dict[str, Any] = field(default_factory=dict)


_ALLOWLIST: dict[str, set[str]] = {
    "melhor_posto.request": {
        "usuario_id_hash", "cidade_id", "combustivel", "modo", "top_n", "lat3", "lng3",
        "assumptions_keys",
    },
    "melhor_posto.response": {
        "usuario_id_hash", "posto_ids", "latencia_ms", "distancia_metodo", "cobertura_baixa",
    },
    "melhor_posto.no_result": {"motivo_agregado"},
    "preco.staleness_alert": {"cidade_id", "pct_fresco"},
    "osrm.fallback": {"cidade_id", "motivo"},
    "auth.login": {"email_hash", "ip", "sucesso", "motivo_falha"},
    "authz.deny": {"usuario_id_hash", "recurso", "acao", "cidade_alvo"},
    "data.export_request": {"usuario_id_hash"},
    "data.delete_request": {"usuario_id_hash"},
    "audit.chain_break": {"first_id"},
    "audit.verifier_error": {"motivo"},
    "deploy.start": {"git_sha"},
    "deploy.ready": {"git_sha"},
    "owner_lease.acquire": {},
    "owner_lease.lose": {},
    "consulta.queue_drop": {"usuario_id_hash"},
    "rate_limit.deny": {"chave_prefix"},
    "flag.change": {"chave", "escopo"},
}


def emit(event_name: str, **payload: Any) -> None:
    """Emite evento com filtro allowlist. Campos não declarados são descartados."""
    allowed = _ALLOWLIST.get(event_name)
    if allowed is None:
        # evento desconhecido: emite só o nome (sem vazar campos arbitrários)
        logger.warning(json.dumps({"event": event_name, "_warn": "unknown_schema"}))
        return
    safe = {k: v for k, v in payload.items() if k in allowed}
    record = {"event": event_name, "schema_version": 1, **safe}
    logger.info(json.dumps(record, sort_keys=True, ensure_ascii=False))
    incr(f"event:{event_name}")
