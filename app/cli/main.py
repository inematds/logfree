"""LogFree CLI — bootstrap admin, audit-unblock, run jobs, etc."""
from __future__ import annotations

import json
import sys

import click

from app.core.audit import unblock as audit_unblock_event
from app.core.audit import verify_chain
from app.core.flags import set_flag
from app.core.lgpd import replay_tombstones
from app.core.settings import get_settings
from app.db.base import get_sessionmaker
from app.db.models import Usuario, UsuarioCidade
from app.web.auth import hash_password


@click.group()
def cli() -> None:
    """LogFree admin CLI."""


@cli.command("bootstrap-admin")
@click.option("--email", required=True)
@click.option("--password", required=True)
@click.option("--cidade-id", type=int, default=None, help="Vincula admin a essa cidade")
def bootstrap_admin(email: str, password: str, cidade_id: int | None) -> None:
    """Cria (ou reseta senha de) o admin inicial."""
    sm = get_sessionmaker()
    with sm() as s:
        u = s.query(Usuario).filter(Usuario.email == email).one_or_none()
        if u is None:
            u = Usuario(
                email=email,
                nome=email.split("@")[0],
                perfil="admin",
                password_hash=hash_password(password),
                ativo=1,
            )
            s.add(u)
            s.flush()
        else:
            u.password_hash = hash_password(password)
            u.ativo = 1
            u.perfil = "admin"
        if cidade_id is not None:
            existing = s.query(UsuarioCidade).filter(
                UsuarioCidade.usuario_id == u.id,
                UsuarioCidade.cidade_id == cidade_id,
            ).one_or_none()
            if existing is None:
                s.add(UsuarioCidade(usuario_id=u.id, cidade_id=cidade_id, papel="admin"))
        s.commit()
        click.echo(f"admin {email} pronto (id={u.id})")


@cli.command("audit-verify")
def audit_verify() -> None:
    sm = get_sessionmaker()
    with sm() as s:
        res = verify_chain(s)
    click.echo(json.dumps({
        "ok": res.ok, "total": res.total,
        "primeiro_id_quebrado": res.primeiro_id_quebrado,
        "motivo": res.motivo,
    }, indent=2))
    if not res.ok:
        sys.exit(1)


@cli.command("audit-unblock")
@click.option("--approver", required=True)
@click.option("--motivo", required=True)
def audit_unblock(approver: str, motivo: str) -> None:
    if not get_settings().audit_unblock_key:
        raise click.ClickException("AUDIT_UNBLOCK_KEY não configurado")
    sm = get_sessionmaker()
    with sm() as s:
        ev = audit_unblock_event(s, approver, motivo)
        s.commit()
        click.echo(f"audit_unblock id={ev.id}")


@cli.command("flag-set")
@click.option("--chave", required=True)
@click.option("--valor", required=True, help="JSON ou string")
@click.option("--escopo", default="global", type=click.Choice(["global", "cidade", "usuario"]))
@click.option("--alvo", default="*")
def flag_set(chave: str, valor: str, escopo: str, alvo: str) -> None:
    try:
        v = json.loads(valor)
    except json.JSONDecodeError:
        v = valor
    sm = get_sessionmaker()
    with sm() as s:
        set_flag(s, chave, v, escopo=escopo, alvo=alvo, atualizado_por="cli")
        s.commit()
    click.echo(f"flag {chave} setada em {escopo}:{alvo} = {valor}")


@cli.command("replay-tombstones")
def replay() -> None:
    sm = get_sessionmaker()
    with sm() as s:
        n = replay_tombstones(s)
        s.commit()
    click.echo(f"replay aplicado em {n} usuários")


if __name__ == "__main__":
    cli()
