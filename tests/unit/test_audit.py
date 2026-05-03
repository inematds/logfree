from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.audit import unblock, verify_chain, write_event
from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.models import EventoAudit


def _engine():
    e = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(e)
    return e


def test_chain_grows_and_verifies():
    e = _engine()
    with Session(e) as s:
        write_event(s, acao="login_success", payload={"u": 1})
        write_event(s, acao="role_grant", recurso="usuario", recurso_id=2, payload={})
        write_event(s, acao="data_export", payload={"who": 1})
        s.commit()
        res = verify_chain(s)
        assert res.ok
        assert res.total == 3
        assert res.primeiro_id_quebrado is None


def test_payload_too_large_raises():
    e = _engine()
    with Session(e) as s:
        big = {"k": "x" * 9000}
        try:
            write_event(s, acao="x", payload=big)
        except ValueError:
            return
        raise AssertionError("ValueError esperado")


def test_chain_break_detected_on_tamper():
    e = _engine()
    with Session(e) as s:
        write_event(s, acao="a")
        write_event(s, acao="b")
        write_event(s, acao="c")
        s.commit()
        # Tampera com payload sem recalcular hash_self
        ev = s.query(EventoAudit).filter_by(acao="b").one()
        ev.payload_json = '{"tampered": true}'
        s.commit()
        res = verify_chain(s)
        assert not res.ok
        assert res.motivo == "chain_break"
        assert res.primeiro_id_quebrado == ev.id


def test_chain_break_detected_on_missing_link():
    e = _engine()
    with Session(e) as s:
        write_event(s, acao="a")
        write_event(s, acao="b")
        write_event(s, acao="c")
        s.commit()
        # Apaga a linha do meio
        ev = s.query(EventoAudit).filter_by(acao="b").one()
        s.delete(ev)
        s.commit()
        res = verify_chain(s)
        assert not res.ok
        assert res.motivo == "chain_break"


def test_unblock_requires_key(monkeypatch):
    monkeypatch.delenv("AUDIT_UNBLOCK_KEY", raising=False)
    e = _engine()
    with Session(e) as s:
        try:
            unblock(s, approver_email="a@b", motivo="manual")
        except PermissionError:
            return
        raise AssertionError("PermissionError esperado")


def test_unblock_grava_evento():
    e = _engine()
    with Session(e) as s:
        write_event(s, acao="some")
        ev = unblock(s, approver_email="a@b", motivo="rebase manual",
                      audit_unblock_key="dev-key")
        s.commit()
        assert ev.acao == "audit_unblock"
        assert "rebase manual" in ev.payload_json
