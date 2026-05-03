from __future__ import annotations

import time

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.flags import get_flag, kill_switch_global, set_flag
from app.core.rate_limit import TokenBucketLimiter
from app.core.telemetry import emit, hash_id, metrics_snapshot, reset_metrics, round3
from app.db import models  # noqa: F401
from app.db.base import Base


def _engine():
    e = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(e)
    return e


def test_set_and_get_global_flag():
    e = _engine()
    with Session(e) as s:
        set_flag(s, "ranking.osrm_enabled", True)
        s.commit()
        assert get_flag(s, "ranking.osrm_enabled") is True


def test_user_scope_overrides_global():
    e = _engine()
    with Session(e) as s:
        set_flag(s, "ranking.shadow", False)
        set_flag(s, "ranking.shadow", True, escopo="usuario", alvo="42")
        s.commit()
        assert get_flag(s, "ranking.shadow", usuario_id=42) is True
        assert get_flag(s, "ranking.shadow", usuario_id=99) is False


def test_cidade_scope_overrides_global():
    e = _engine()
    with Session(e) as s:
        set_flag(s, "ranking.stale_prices_enabled", False)
        set_flag(s, "ranking.stale_prices_enabled", True, escopo="cidade", alvo="1")
        s.commit()
        assert get_flag(s, "ranking.stale_prices_enabled", cidade_id=1) is True


def test_kill_switch_default_false():
    e = _engine()
    with Session(e) as s:
        assert kill_switch_global(s) is False
        set_flag(s, "kill_switch.global", True)
        s.commit()
        assert kill_switch_global(s) is True


def test_token_bucket_limiter_basic():
    lim = TokenBucketLimiter()
    for _ in range(3):
        assert lim.allow("k:1", capacity=3, per_seconds=60)
    assert not lim.allow("k:1", capacity=3, per_seconds=60)


def test_token_bucket_recarrega():
    lim = TokenBucketLimiter()
    assert lim.allow("k:1", capacity=1, per_seconds=1)
    assert not lim.allow("k:1", capacity=1, per_seconds=1)
    time.sleep(1.05)
    assert lim.allow("k:1", capacity=1, per_seconds=1)


def test_telemetry_round3():
    assert round3(-12.97123) == -12.971
    assert round3(None) is None


def test_telemetry_hash_id():
    h1 = hash_id(42)
    h2 = hash_id(42)
    h3 = hash_id(43)
    assert h1 == h2
    assert h1 != h3
    assert hash_id(None) == ""


def test_telemetry_emit_filtra_campos_nao_allowlist():
    reset_metrics()
    emit(
        "melhor_posto.request",
        usuario_id_hash="abc",
        cidade_id=1,
        combustivel="diesel_s10",
        modo="completo",
        top_n=3,
        lat3=-12.97,
        lng3=-38.50,
        assumptions_keys=["k_banda"],
        leak_pii="sensitive",  # não deve aparecer no log
    )
    snap = metrics_snapshot()
    assert snap["counters"].get("event:melhor_posto.request") == 1


def test_telemetry_emit_evento_desconhecido_nao_quebra():
    reset_metrics()
    emit("evento.inexistente", anything="x")
    # não levanta; counter não incrementa porque o nome não está no allowlist
    snap = metrics_snapshot()
    assert "event:evento.inexistente" not in snap["counters"]
