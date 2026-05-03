"""Handlers Telegram (PLAN.md item 2).

Estado de conversa em `bot_estado` com schema_version e expira_em.
Webhook secret token validado antes do parse na rota webhook (`app.bot.webhook`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_event
from app.core.lgpd import apagar_meus_dados, export_to_json, exportar_meus_dados
from app.db.base import utcnow_iso
from app.db.models import BotEstado, Cidade, Combustivel, Usuario

BOT_STATE_VERSION = 1
BOT_STATE_TTL_MIN = 30


@dataclass
class BotResponse:
    text: str
    expects_location: bool = False


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _expira() -> str:
    return (_now() + timedelta(minutes=BOT_STATE_TTL_MIN)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _set_state(s: Session, usuario_id: int, chave: str, valor: dict[str, Any]) -> None:
    existing = s.execute(
        select(BotEstado).where(BotEstado.usuario_id == usuario_id, BotEstado.chave == chave)
    ).scalar_one_or_none()
    if existing is None:
        s.add(
            BotEstado(
                usuario_id=usuario_id,
                chave=chave,
                valor_json=json.dumps(valor),
                schema_version=BOT_STATE_VERSION,
                expira_em=_expira(),
            )
        )
    else:
        existing.valor_json = json.dumps(valor)
        existing.schema_version = BOT_STATE_VERSION
        existing.expira_em = _expira()


def _get_state(s: Session, usuario_id: int, chave: str) -> dict[str, Any] | None:
    row = s.execute(
        select(BotEstado).where(BotEstado.usuario_id == usuario_id, BotEstado.chave == chave)
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.schema_version != BOT_STATE_VERSION:
        return None
    if row.expira_em < utcnow_iso():
        return None
    try:
        return json.loads(row.valor_json)
    except json.JSONDecodeError:
        return None


def _user_by_tg(s: Session, telegram_id: int) -> Usuario | None:
    return s.query(Usuario).filter(Usuario.telegram_id == telegram_id).one_or_none()


def cmd_start(s: Session, telegram_id: int, nome_telegram: str = "") -> BotResponse:
    u = _user_by_tg(s, telegram_id)
    if u is None:
        u = Usuario(
            telegram_id=telegram_id,
            nome=nome_telegram[:120],
            perfil="motorista",
            ativo=1,
            opt_in_lgpd=0,
        )
        s.add(u)
        s.flush()
    return BotResponse(
        text=(
            "Bem-vindo ao LogFree.\n\n"
            "Para usar, preciso seu consentimento LGPD: aceito guardar consultas "
            "(localização, combustível, escolha) por até 90 dias para melhorar a "
            "recomendação. Você pode pedir /apagar_meus_dados a qualquer momento.\n\n"
            "Mande /aceitar_termos para confirmar."
        )
    )


def cmd_aceitar_termos(s: Session, telegram_id: int) -> BotResponse:
    u = _user_by_tg(s, telegram_id)
    if u is None:
        return cmd_start(s, telegram_id)
    if u.opt_in_lgpd:
        return BotResponse(text="Você já aceitou os termos. Use /melhor para começar.")
    u.opt_in_lgpd = 1
    u.opt_in_at = utcnow_iso()
    write_event(s, usuario_id=u.id, acao="opt_in_lgpd", recurso="usuario", recurso_id=u.id)
    return BotResponse(text="Obrigado. Use /melhor <combustível> e me mande sua localização.")


def cmd_apagar(s: Session, telegram_id: int) -> BotResponse:
    u = _user_by_tg(s, telegram_id)
    if u is None:
        return BotResponse(text="Nada para apagar.")
    apagar_meus_dados(s, u.id, motivo="user_request")
    return BotResponse(
        text="Seus dados foram apagados. Não te enviaremos mais mensagens.\n"
             "Para usar de novo, mande /start."
    )


def cmd_exportar(s: Session, telegram_id: int) -> BotResponse:
    u = _user_by_tg(s, telegram_id)
    if u is None:
        return BotResponse(text="Cadastre-se primeiro com /start.")
    data = exportar_meus_dados(s, u.id)
    payload = export_to_json(data)
    # Em produção: gerar link assinado com TTL 72h e mandar por mensagem.
    # No MVP local: devolver os primeiros 1500 chars como prévia.
    return BotResponse(
        text=f"Seus dados (prévia):\n```\n{payload[:1500]}\n```"
    )


def cmd_melhor(
    s: Session,
    telegram_id: int,
    combustivel_codigo: str,
) -> BotResponse:
    u = _user_by_tg(s, telegram_id)
    if u is None or not u.opt_in_lgpd:
        return BotResponse(
            text="Antes de usar /melhor, mande /start e /aceitar_termos."
        )
    comb = s.query(Combustivel).filter(Combustivel.codigo == combustivel_codigo).one_or_none()
    if comb is None:
        codigos = ", ".join(c.codigo for c in s.query(Combustivel).all())
        return BotResponse(text=f"Combustível inválido. Use um destes: {codigos}.")
    _set_state(s, u.id, "melhor_em_andamento", {"combustivel": combustivel_codigo})
    return BotResponse(
        text="Mande sua localização atual e me diga sua autonomia restante "
             "(ex.: 'autonomia 60' ou 'autonomia 200 cheio').",
        expects_location=True,
    )


def receive_location(
    s: Session,
    telegram_id: int,
    lat: float,
    lng: float,
) -> dict[str, Any] | None:
    """Salva lat/lng como state intermediário do fluxo /melhor."""
    u = _user_by_tg(s, telegram_id)
    if u is None:
        return None
    state = _get_state(s, u.id, "melhor_em_andamento") or {}
    state.update({"lat": lat, "lng": lng})
    _set_state(s, u.id, "melhor_em_andamento", state)
    return state


def receive_autonomia(
    s: Session,
    telegram_id: int,
    autonomia_km: float,
    modo: str = "completo",
    volume_alvo_l: float | None = None,
) -> dict[str, Any] | None:
    u = _user_by_tg(s, telegram_id)
    if u is None:
        return None
    state = _get_state(s, u.id, "melhor_em_andamento") or {}
    state.update(
        {"autonomia_restante_km": autonomia_km, "modo": modo, "volume_alvo_l": volume_alvo_l}
    )
    _set_state(s, u.id, "melhor_em_andamento", state)
    return state


def cmd_postos(s: Session, telegram_id: int) -> BotResponse:
    """Lista postos da cidade do usuário (sem ranking de custo/km)."""
    u = _user_by_tg(s, telegram_id)
    if u is None:
        return BotResponse(text="Mande /start primeiro.")
    # Procura cidade do usuário via usuario_cidade ou pega a cidade demo.
    cidade = s.query(Cidade).filter(Cidade.is_demo == 1).first()
    if cidade is None:
        return BotResponse(text="Sem cidade configurada ainda.")
    from app.db.models import Posto
    postos = s.query(Posto).filter(Posto.cidade_id == cidade.id, Posto.ativo == 1).all()
    if not postos:
        return BotResponse(text="Nenhum posto cadastrado nesta cidade.")
    linhas = [f"- {p.nome} ({p.bandeira})" for p in postos[:20]]
    return BotResponse(text="Postos na cidade:\n" + "\n".join(linhas))
