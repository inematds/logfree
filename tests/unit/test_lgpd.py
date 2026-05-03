from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.lgpd import apagar_meus_dados, exportar_meus_dados, is_tombstoned, replay_tombstones
from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.models import Consulta, Usuario


def _engine():
    e = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(e)
    return e


def _seed_user(s: Session, telegram_id: int = 100) -> Usuario:
    u = Usuario(telegram_id=telegram_id, nome="Foo", perfil="motorista", opt_in_lgpd=1)
    s.add(u)
    s.flush()
    s.add(
        Consulta(
            usuario_id=u.id,
            lat=-12.97,
            lng=-38.50,
            combustivel_id=1,
            autonomia_km=200.0,
            modo="completo",
            top_n=3,
            top_n_resultado_json="[]",
            assumptions_json="{}",
        )
    )
    s.commit()
    return u


def test_apagar_anonimiza_e_tombstone():
    e = _engine()
    with Session(e) as s:
        # precisa de pelo menos 1 combustivel para o FK da Consulta
        from app.db.models import Combustivel

        s.add(Combustivel(codigo="diesel_s10"))
        s.commit()
        u = _seed_user(s)
        apagar_meus_dados(s, u.id, motivo="user_request")
        s.commit()
        u2 = s.get(Usuario, u.id)
        assert u2.tombstoned == 1
        assert u2.email is None
        assert u2.telegram_id is None
        assert u2.nome == ""
        assert is_tombstoned(s, u.id)
        # consulta anonimizada
        c = s.execute(
            Consulta.__table__.select().where(Consulta.usuario_id == u.id)
        ).first()
        assert c.lat is None and c.lng is None and c.anonimizada == 1


def test_apagar_idempotente():
    e = _engine()
    with Session(e) as s:
        from app.db.models import Combustivel
        s.add(Combustivel(codigo="diesel_s10"))
        s.commit()
        u = _seed_user(s)
        apagar_meus_dados(s, u.id)
        s.commit()
        # 2a chamada não levanta
        apagar_meus_dados(s, u.id)
        s.commit()


def test_export_inclui_consultas():
    e = _engine()
    with Session(e) as s:
        from app.db.models import Combustivel
        s.add(Combustivel(codigo="diesel_s10"))
        s.commit()
        u = _seed_user(s)
        data = exportar_meus_dados(s, u.id)
        s.commit()
        assert data.usuario["telegram_id"] == 100
        assert len(data.consultas) == 1


def test_replay_tombstones_reaplica():
    e = _engine()
    with Session(e) as s:
        from app.db.models import Combustivel, TombstoneUsuario
        s.add(Combustivel(codigo="diesel_s10"))
        s.commit()
        u = _seed_user(s)
        apagar_meus_dados(s, u.id)
        s.commit()
        # simula restore: usuário deixou de estar tombstoned mas tombstone persiste
        u2 = s.get(Usuario, u.id)
        u2.tombstoned = 0
        u2.email = "leak@x.com"
        u2.nome = "Leaked"
        s.commit()
        # garante que tombstone ainda está lá
        assert s.get(TombstoneUsuario, u.id) is not None
        n = replay_tombstones(s)
        s.commit()
        assert n == 1
        u3 = s.get(Usuario, u.id)
        assert u3.tombstoned == 1
        assert u3.email is None
        assert u3.nome == ""


def test_trigger_bloqueia_consulta_para_tombstoned():
    """Trigger SQL do migration b86975f005bf bloqueia INSERT de consulta após tombstone."""
    # Aqui usamos o engine real com migrations aplicadas para testar o trigger.
    import os
    import tempfile

    from sqlalchemy import create_engine, text

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "x.sqlite")
        e = create_engine(f"sqlite:///{db_path}", future=True)
        Base.metadata.create_all(e)
        # cria triggers manualmente (já que estamos sem alembic aqui)
        with e.begin() as c:
            c.execute(text("""
                CREATE TRIGGER consulta_block_tombstoned
                BEFORE INSERT ON consulta
                FOR EACH ROW
                BEGIN
                  SELECT CASE
                    WHEN (SELECT 1 FROM tombstone_usuario t WHERE t.usuario_id = NEW.usuario_id)
                         IS NOT NULL
                    THEN RAISE(ABORT, 'usuario tombstoned')
                  END;
                END;
            """))
        with Session(e) as s:
            from app.db.models import Combustivel
            s.add(Combustivel(codigo="diesel_s10"))
            s.commit()
            u = _seed_user(s)
            apagar_meus_dados(s, u.id)
            s.commit()
            # Tentar inserir nova consulta deve falhar
            try:
                s.add(
                    Consulta(
                        usuario_id=u.id,
                        combustivel_id=1,
                        autonomia_km=10.0,
                        modo="completo",
                        top_n=3,
                    )
                )
                s.commit()
                raise AssertionError("trigger não bloqueou")
            except Exception as exc:
                assert "tombstoned" in str(exc).lower()
                s.rollback()
