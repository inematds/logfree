"""Dependências FastAPI: autenticação, autorização, sessão."""
from __future__ import annotations

import secrets
from typing import Literal

from fastapi import Depends, Header, HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.core.lgpd import is_tombstoned
from app.core.settings import get_settings
from app.core.telemetry import emit, hash_id
from app.db.base import get_sessionmaker
from app.db.models import Usuario


def get_db() -> Session:
    sm = get_sessionmaker()
    s = sm()
    try:
        yield s
    finally:
        s.close()


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().cookie_secret, salt="logfree-session")


def make_session_cookie(usuario_id: int) -> str:
    return _signer().dumps({"uid": usuario_id})


def parse_session_cookie(value: str) -> int | None:
    try:
        data = _signer().loads(value, max_age=get_settings().session_ttl_minutes * 60)
        return int(data["uid"])
    except (BadSignature, KeyError, ValueError):
        return None


class AuthIdentity:
    """Identidade do caller resolvida pela camada de auth."""

    def __init__(
        self,
        usuario: Usuario,
        source: Literal["session", "bot_token"],
    ) -> None:
        self.usuario = usuario
        self.source = source

    @property
    def usuario_id(self) -> int:
        return self.usuario.id


def _bot_token_valid(token: str) -> bool:
    s = get_settings()
    candidates = [s.bot_service_token, s.bot_service_token_prev]
    candidates = [c for c in candidates if c]
    return any(secrets.compare_digest(token, c) for c in candidates)


async def auth_required(
    request: Request,
    db: Session = Depends(get_db),
    x_bot_token: str | None = Header(default=None, alias="X-Bot-Token"),
    x_bot_user_telegram_id: int | None = Header(
        default=None, alias="X-Bot-User-Telegram-Id"
    ),
) -> AuthIdentity:
    """(a) cookie de sessão ou (b) bot service token + X-Bot-User-Telegram-Id."""
    # (a) cookie de sessão
    cookie = request.cookies.get("logfree_session")
    if cookie:
        uid = parse_session_cookie(cookie)
        if uid is not None:
            u = db.get(Usuario, uid)
            if u is not None and u.ativo and not u.tombstoned:
                return AuthIdentity(usuario=u, source="session")

    # (b) bot token
    if x_bot_token and _bot_token_valid(x_bot_token):
        if x_bot_user_telegram_id is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="X-Bot-User-Telegram-Id ausente",
            )
        u = (
            db.query(Usuario)
            .filter(Usuario.telegram_id == x_bot_user_telegram_id)
            .one_or_none()
        )
        if u is None or not u.ativo or u.tombstoned:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="usuário inválido")
        if not u.opt_in_lgpd:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="execute /aceitar_termos antes de usar o bot",
            )
        if is_tombstoned(db, u.id):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="usuário apagado")
        return AuthIdentity(usuario=u, source="bot_token")

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="autenticação requerida")


def require_perfil(*perfis: str):
    async def _dep(identity: AuthIdentity = Depends(auth_required)) -> AuthIdentity:
        if identity.usuario.perfil not in perfis:
            emit(
                "authz.deny",
                usuario_id_hash=hash_id(identity.usuario.id),
                recurso="perfil",
                acao="check",
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="perfil insuficiente")
        return identity
    return _dep


def require_authz(recurso: str, acao: str, escopo_cidade: bool = True):
    """Default-deny. Admin sempre passa. Operador só na própria cidade."""
    async def _dep(
        request: Request,
        identity: AuthIdentity = Depends(auth_required),
        db: Session = Depends(get_db),
    ) -> AuthIdentity:
        u = identity.usuario
        if u.perfil == "admin":
            return identity
        if u.perfil == "operador" and escopo_cidade:
            # validação cross-city é responsabilidade do handler usando a cidade alvo
            return identity
        emit(
            "authz.deny",
            usuario_id_hash=hash_id(u.id),
            recurso=recurso,
            acao=acao,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="não autorizado")
    return _dep
