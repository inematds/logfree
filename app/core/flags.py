"""Feature flags com escopo global / cidade / usuário (PLAN.md item 41c).

Lookup ordering (mais específico vence): usuario > cidade > global.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_event
from app.db.base import utcnow_iso
from app.db.models import FeatureFlag


def _decode(valor_json: str):
    try:
        return json.loads(valor_json)
    except json.JSONDecodeError:
        return False


def get_flag(
    s: Session,
    chave: str,
    cidade_id: int | None = None,
    usuario_id: int | None = None,
    default=False,
):
    """Retorna o valor decodificado do flag mais específico ativo."""
    rows = s.execute(
        select(FeatureFlag).where(FeatureFlag.chave == chave, FeatureFlag.ativo == 1)
    ).scalars()
    rows = list(rows)
    if not rows:
        return default
    by_scope = {(r.escopo, r.alvo): r for r in rows}
    if usuario_id is not None and ("usuario", str(usuario_id)) in by_scope:
        return _decode(by_scope[("usuario", str(usuario_id))].valor_json)
    if cidade_id is not None and ("cidade", str(cidade_id)) in by_scope:
        return _decode(by_scope[("cidade", str(cidade_id))].valor_json)
    if ("global", "*") in by_scope:
        return _decode(by_scope[("global", "*")].valor_json)
    return default


def set_flag(
    s: Session,
    chave: str,
    valor,
    *,
    escopo: str = "global",
    alvo: str = "*",
    atualizado_por: str = "system",
    audit: bool = True,
) -> FeatureFlag:
    if escopo not in ("global", "cidade", "usuario"):
        raise ValueError("escopo invalido")
    valor_json = json.dumps(valor)
    existing = s.execute(
        select(FeatureFlag).where(
            FeatureFlag.chave == chave,
            FeatureFlag.escopo == escopo,
            FeatureFlag.alvo == alvo,
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = FeatureFlag(
            chave=chave,
            escopo=escopo,
            alvo=alvo,
            valor_json=valor_json,
            ativo=1,
            atualizado_em=utcnow_iso(),
            atualizado_por=atualizado_por[:80],
        )
        s.add(existing)
    else:
        existing.valor_json = valor_json
        existing.ativo = 1
        existing.atualizado_em = utcnow_iso()
        existing.atualizado_por = atualizado_por[:80]
    s.flush()
    if audit:
        write_event(
            s,
            acao="flag_change",
            recurso="feature_flag",
            recurso_id=existing.id,
            payload={"chave": chave, "escopo": escopo, "alvo": alvo, "valor": valor},
        )
    return existing


def kill_switch_global(s: Session) -> bool:
    return bool(get_flag(s, "kill_switch.global", default=False))
