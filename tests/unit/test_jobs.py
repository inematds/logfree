from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.models import Cidade, Combustivel, Consulta, Posto, Preco, Usuario
from app.jobs.anonimizacao import anonimizar, k_anon_grid
from app.jobs.cobertura import calcular_cobertura


def _engine():
    e = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(e)
    return e


def test_k_anon_grid_snap_500m():
    a = k_anon_grid(-12.97123, -38.50456)
    b = k_anon_grid(-12.97120, -38.50450)  # ~3-5m de diferença
    assert a == b


def test_anonimizacao_zera_consultas_antigas(monkeypatch):
    e = _engine()
    with Session(e) as s:
        s.add(Combustivel(codigo="diesel_s10"))
        s.add(Usuario(perfil="motorista"))
        s.commit()
        agora = datetime.now(tz=UTC)
        # consulta de 100 dias atrás
        antiga = (agora - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        s.add(Consulta(
            usuario_id=1, lat=-12.97, lng=-38.5, combustivel_id=1,
            autonomia_km=200, modo="completo", top_n=3,
            feita_em=antiga, top_n_resultado_json='[{"a":1}]',
        ))
        # consulta recente
        recente = agora.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        s.add(Consulta(
            usuario_id=1, lat=-12.97, lng=-38.5, combustivel_id=1,
            autonomia_km=200, modo="completo", top_n=3,
            feita_em=recente, top_n_resultado_json='[{"a":1}]',
        ))
        s.commit()
        n = anonimizar(s, agora=agora)
        assert n == 1
        anon, fresca = s.query(Consulta).order_by(Consulta.id).all()
        assert anon.lat is None and anon.anonimizada == 1
        assert fresca.lat == -12.97


def test_cobertura_calcula_pct_fresco():
    e = _engine()
    with Session(e) as s:
        c = Cidade(
            nome="X", uf="BA", timezone="America/Bahia",
            bbox_min_lat=-13.1, bbox_max_lat=-12.8, bbox_min_lng=-38.7, bbox_max_lng=-38.3,
            preco_validade_horas_soft=24, preco_validade_horas_hard=72,
            stale_coverage_alert_pct=30, horizonte_default_km=100, fator_road_default=1.35,
        )
        s.add(c)
        s.flush()
        s.add(Combustivel(codigo="diesel_s10"))
        s.flush()
        # 4 postos: 2 com preço fresco, 1 com preço velho, 1 sem preço
        for i in range(4):
            s.add(Posto(cidade_id=c.id, nome=f"P{i}", lat=-12.97, lng=-38.50, ativo=1))
        s.flush()
        agora = datetime.now(tz=UTC)
        fresco = agora.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        velho = (agora - timedelta(hours=100)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        s.add(Preco(posto_id=1, combustivel_id=1, valor_centavos=590,
                     observed_at=fresco, recorded_at=fresco, fonte="operador"))
        s.add(Preco(posto_id=2, combustivel_id=1, valor_centavos=590,
                     observed_at=fresco, recorded_at=fresco, fonte="operador"))
        s.add(Preco(posto_id=3, combustivel_id=1, valor_centavos=590,
                     observed_at=velho, recorded_at=velho, fonte="operador"))
        s.commit()
        pct = calcular_cobertura(s, c, agora)
        assert 49.0 < pct < 51.0
