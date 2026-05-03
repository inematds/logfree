"""Rota de webhook do Telegram com secret token (PLAN.md item 2).

Não usamos parser do PTB no MVP local — recebemos o JSON e roteamos para handlers
através de um dispatcher minimalista. Em produção (`ENV=prod`) ligamos o webhook
no Telegram apontando para `/bot/webhook/{path_secret}` com header
`X-Telegram-Bot-Api-Secret-Token`.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.bot import handlers
from app.core.settings import get_settings
from app.core.telemetry import emit

router = APIRouter()


def _validate_secret(header_value: str | None) -> bool:
    s = get_settings()
    if not s.tg_webhook_secret:
        return False
    candidates = [s.tg_webhook_secret, s.tg_webhook_secret_prev]
    candidates = [c for c in candidates if c]
    if not header_value:
        return False
    return any(secrets.compare_digest(header_value, c) for c in candidates)


@router.post("/bot/webhook/{path_secret}")
async def telegram_webhook(
    path_secret: str,
    request: Request,
    db: Session = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    s = get_settings()
    if not s.tg_webhook_secret:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="webhook não configurado",
        )
    if not secrets.compare_digest(path_secret, s.tg_webhook_secret):
        emit("auth.tg_webhook_reject", chave_prefix="path")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="path inválido")
    if not _validate_secret(x_telegram_bot_api_secret_token):
        emit("auth.tg_webhook_reject", chave_prefix="header")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="secret inválido")

    update = await request.json()
    msg = update.get("message", {})
    text = (msg.get("text") or "").strip()
    chat = msg.get("chat", {})
    telegram_id = chat.get("id")
    nome = chat.get("first_name", "") or ""

    if telegram_id is None:
        return {"ok": True, "ignored": True}

    if text.startswith("/start"):
        resp = handlers.cmd_start(db, telegram_id, nome)
    elif text.startswith("/aceitar_termos"):
        resp = handlers.cmd_aceitar_termos(db, telegram_id)
    elif text.startswith("/apagar_meus_dados"):
        resp = handlers.cmd_apagar(db, telegram_id)
    elif text.startswith("/exportar_meus_dados"):
        resp = handlers.cmd_exportar(db, telegram_id)
    elif text.startswith("/postos"):
        resp = handlers.cmd_postos(db, telegram_id)
    elif text.startswith("/melhor"):
        parts = text.split()
        codigo = parts[1] if len(parts) > 1 else "diesel_s10"
        resp = handlers.cmd_melhor(db, telegram_id, codigo)
    else:
        resp = handlers.BotResponse(text="Comandos: /start /aceitar_termos /melhor /postos "
                                          "/apagar_meus_dados /exportar_meus_dados")

    db.commit()
    return {"ok": True, "reply": resp.text, "expects_location": resp.expects_location}


