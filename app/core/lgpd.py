"""Apagamento e exportação LGPD (PLAN.md item 9).

apagar_meus_dados:
1. Drena fila assíncrona filtrando o usuario_id (caller faz antes de chamar).
2. Transação única: tombstone, anonimiza usuário, apaga vinculos, anonimiza consultas.
3. Trigger SQL bloqueia INSERTs futuros — mesmo motorista (telegram_id zerado).
4. Audit event 'data_delete'.

exportar_meus_dados: monta JSON com tudo do usuário.
replay_tombstones: re-aplica anonimização lendo tombstone_usuario depois de restore.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.audit import write_event
from app.db.base import utcnow_iso
from app.db.models import (
    AbastecimentoRelatado,
    BotEstado,
    Consulta,
    TombstoneUsuario,
    Usuario,
    UsuarioCidade,
    UsuarioVeiculo,
)


@dataclass
class ExportData:
    usuario: dict
    veiculos: list[dict]
    cidades: list[dict]
    consultas: list[dict]
    abastecimentos: list[dict]


def apagar_meus_dados(s: Session, usuario_id: int, motivo: str = "user_request") -> None:
    """Hard delete LGPD: tombstone + anonimização + bloqueio futuro.

    Caller deve ter drenado a fila assíncrona de `consulta` para esse usuário antes.
    Toda a operação fica numa transação única (commit pelo session_scope do caller
    ou aqui mesmo). Audit gravado dentro da mesma transação.
    """
    u = s.get(Usuario, usuario_id)
    if u is None:
        raise ValueError(f"usuario {usuario_id} não encontrado")
    if u.tombstoned:
        return  # idempotente

    now = utcnow_iso()

    if s.get(TombstoneUsuario, usuario_id) is None:
        s.add(TombstoneUsuario(usuario_id=usuario_id, criado_em=now, motivo=motivo[:40]))

    # Anonimização do usuário (zero PII).
    s.execute(
        update(Usuario)
        .where(Usuario.id == usuario_id)
        .values(
            nome="",
            email=None,
            password_hash=None,
            telegram_id=None,
            tombstoned=1,
            tombstoned_at=now,
            ativo=0,
            opt_in_lgpd=0,
        )
    )

    # Vínculos: removemos user_veiculo e user_cidade
    s.query(UsuarioVeiculo).filter(UsuarioVeiculo.usuario_id == usuario_id).delete(
        synchronize_session=False
    )
    s.query(UsuarioCidade).filter(UsuarioCidade.usuario_id == usuario_id).delete(
        synchronize_session=False
    )
    s.query(BotEstado).filter(BotEstado.usuario_id == usuario_id).delete(
        synchronize_session=False
    )

    # Anonimização das consultas: zera lat/lng e top_n_resultado_json.
    s.execute(
        update(Consulta)
        .where(Consulta.usuario_id == usuario_id)
        .values(lat=None, lng=None, top_n_resultado_json="[]", anonimizada=1)
    )

    # Apaga foto_storage_key das relatadas (caller deve apagar arquivos no storage).
    s.execute(
        update(AbastecimentoRelatado)
        .where(AbastecimentoRelatado.usuario_id == usuario_id)
        .values(foto_storage_key=None)
    )

    write_event(
        s,
        usuario_id=usuario_id,
        acao="data_delete",
        recurso="usuario",
        recurso_id=usuario_id,
        payload={"motivo": motivo[:40]},
    )


def is_tombstoned(s: Session, usuario_id: int) -> bool:
    return s.get(TombstoneUsuario, usuario_id) is not None


def exportar_meus_dados(s: Session, usuario_id: int) -> ExportData:
    u = s.get(Usuario, usuario_id)
    if u is None:
        raise ValueError(f"usuario {usuario_id} não encontrado")
    veiculos = list(
        s.execute(select(UsuarioVeiculo).where(UsuarioVeiculo.usuario_id == usuario_id)).scalars()
    )
    cidades = list(
        s.execute(select(UsuarioCidade).where(UsuarioCidade.usuario_id == usuario_id)).scalars()
    )
    consultas = list(
        s.execute(select(Consulta).where(Consulta.usuario_id == usuario_id)).scalars()
    )
    abasts = list(
        s.execute(
            select(AbastecimentoRelatado).where(AbastecimentoRelatado.usuario_id == usuario_id)
        ).scalars()
    )

    write_event(
        s,
        usuario_id=usuario_id,
        acao="data_export",
        recurso="usuario",
        recurso_id=usuario_id,
        payload={"items": len(consultas) + len(abasts)},
    )

    return ExportData(
        usuario={
            "id": u.id,
            "nome": u.nome,
            "telegram_id": u.telegram_id,
            "email": u.email,
            "perfil": u.perfil,
            "opt_in_lgpd": bool(u.opt_in_lgpd),
            "opt_in_at": u.opt_in_at,
        },
        veiculos=[{"veiculo_perfil_id": v.veiculo_perfil_id, "ativo": bool(v.ativo)}
                  for v in veiculos],
        cidades=[{"cidade_id": c.cidade_id, "papel": c.papel} for c in cidades],
        consultas=[
            {
                "id": c.id,
                "feita_em": c.feita_em,
                "modo": c.modo,
                "combustivel_id": c.combustivel_id,
                "anonimizada": bool(c.anonimizada),
            }
            for c in consultas
        ],
        abastecimentos=[
            {
                "id": a.id,
                "criado_em": a.criado_em,
                "valor_centavos": a.valor_centavos,
                "volume_l": a.volume_l,
                "posto_id": a.posto_id,
            }
            for a in abasts
        ],
    )


def replay_tombstones(s: Session) -> int:
    """Pós-restore: lê tombstone_usuario e re-aplica apagar_meus_dados.

    Retorna número de usuários re-anonimizados. Idempotente.
    """
    count = 0
    for t in s.execute(select(TombstoneUsuario)).scalars():
        u = s.get(Usuario, t.usuario_id)
        if u is None or u.tombstoned:
            continue
        apagar_meus_dados(s, t.usuario_id, motivo="restore_replay")
        count += 1
    return count


def export_to_json(data: ExportData) -> str:
    return json.dumps(asdict(data), sort_keys=True, ensure_ascii=False, indent=2)
