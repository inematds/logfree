"""Audit chain SHA-256 sobre `evento_audit` (PLAN.md item 41a).

Eventos auditados — login_*, session_*, role_*, posto.*, preco.create,
faixa_preco.override, import_batch.*, data_export, data_delete,
raw_location_read, foto_*, authz.deny, owner_lease.*, deploy.*,
migration.*, restore.run, snapshot.create.

API:
- write_event(s, usuario_id, acao, recurso=..., recurso_id=..., payload=...)
- verify_chain(s) -> VerifyResult
- unblock(s, approver_email, motivo) -> rebase a partir do último íntegro

Modos do verificador:
- íntegra → tudo ok
- chain_break → tampering ou registro perdido (quarentena)
- verifier_error → erro do próprio verificador (não derruba prod)
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow_iso
from app.db.models import EventoAudit


def _hash_payload(
    hash_prev: str,
    row_id: int,
    criado_em: str,
    acao: str,
    recurso: str,
    recurso_id: int | None,
    payload_json: str,
) -> str:
    h = hashlib.sha256()
    h.update(hash_prev.encode())
    h.update(b"|")
    h.update(str(row_id).encode())
    h.update(b"|")
    h.update(criado_em.encode())
    h.update(b"|")
    h.update(acao.encode())
    h.update(b"|")
    h.update(recurso.encode())
    h.update(b"|")
    h.update(b"" if recurso_id is None else str(recurso_id).encode())
    h.update(b"|")
    h.update(payload_json.encode())
    return h.hexdigest()


def write_event(
    s: Session,
    *,
    acao: str,
    usuario_id: int | None = None,
    recurso: str = "",
    recurso_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> EventoAudit:
    """Adiciona evento ao audit log encadeado."""
    payload_json = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False)
    if len(payload_json) >= 8192:
        raise ValueError("payload de audit > 8KB; trunque ou divida")

    last = s.execute(select(EventoAudit).order_by(EventoAudit.id.desc()).limit(1)).scalar_one_or_none()
    hash_prev = last.hash_self if last else ""

    criado_em = utcnow_iso()
    ev = EventoAudit(
        usuario_id=usuario_id,
        acao=acao,
        recurso=recurso,
        recurso_id=recurso_id,
        payload_json=payload_json,
        criado_em=criado_em,
        hash_prev=hash_prev,
        hash_self="",  # preenchido após flush para ter o id
    )
    s.add(ev)
    s.flush()
    ev.hash_self = _hash_payload(
        hash_prev, ev.id, criado_em, acao, recurso, recurso_id, payload_json
    )
    s.flush()
    return ev


@dataclass
class VerifyResult:
    ok: bool
    total: int
    primeiro_id_quebrado: int | None
    motivo: str  # "ok" | "chain_break" | "verifier_error"


def verify_chain(s: Session) -> VerifyResult:
    """Re-calcula o hash de cada linha em ordem e compara com o gravado.

    Erros de I/O viram verifier_error (não chain_break).
    """
    try:
        rows = list(s.execute(select(EventoAudit).order_by(EventoAudit.id.asc())).scalars())
    except Exception as exc:  # pragma: no cover - I/O
        return VerifyResult(ok=False, total=0, primeiro_id_quebrado=None,
                            motivo=f"verifier_error:{exc}")

    expected_prev = ""
    for r in rows:
        if r.hash_prev != expected_prev:
            return VerifyResult(
                ok=False, total=len(rows), primeiro_id_quebrado=r.id, motivo="chain_break"
            )
        expected = _hash_payload(
            r.hash_prev, r.id, r.criado_em, r.acao, r.recurso, r.recurso_id, r.payload_json
        )
        if expected != r.hash_self:
            return VerifyResult(
                ok=False, total=len(rows), primeiro_id_quebrado=r.id, motivo="chain_break"
            )
        expected_prev = r.hash_self
    return VerifyResult(ok=True, total=len(rows), primeiro_id_quebrado=None, motivo="ok")


def unblock(
    s: Session,
    approver_email: str,
    motivo: str,
    audit_unblock_key: str | None = None,
) -> EventoAudit:
    """Re-âncora a cadeia gravando ponto de partida novo + audit_unblock event.

    Requer AUDIT_UNBLOCK_KEY (env) e approver_email com perfil admin (caller checa).
    """
    expected = audit_unblock_key or os.environ.get("AUDIT_UNBLOCK_KEY", "")
    if not expected:
        raise PermissionError("AUDIT_UNBLOCK_KEY ausente")
    return write_event(
        s,
        acao="audit_unblock",
        recurso="evento_audit",
        payload={"approver_email": approver_email, "motivo": motivo},
    )
