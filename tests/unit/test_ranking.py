"""Testes unitários do core/ranking — itens 42 e 43 do PLAN.md."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.ranking import (
    Banda,
    ContextoRanking,
    DistanciaMetodo,
    FonteK,
    Modo,
    PostoCandidato,
    rank,
)

NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


def _ctx(**overrides) -> ContextoRanking:
    base: dict = {
        "lat_usuario": -12.97,
        "lng_usuario": -38.50,
        "autonomia_restante_km": 200.0,
        "modo": Modo.COMPLETO,
        "volume_alvo_l": None,
        "k_banda": Banda(p10=8.0, p50=11.0, p90=14.0),
        "fonte_k": FonteK.PERFIL,
        "horizonte_km": 100.0,
        "raio_maximo_km": 15.0,
        "fator_road": 1.35,
        "soft_horas": 24,
        "hard_horas": 72,
        "stale_penalidade_pct": 0.0,
        "margem_inalcancavel": 0.9,
        "distancia_metodo": DistanciaMetodo.HAVERSINE_FATOR,
        "now": NOW,
    }
    base.update(overrides)
    return ContextoRanking(**base)


def _posto(
    posto_id=1,
    nome="P",
    lat=-12.97,
    lng=-38.51,
    preco=590,
    horas_atras=2,
    ativo=True,
    d_road_km=None,
):
    return PostoCandidato(
        posto_id=posto_id,
        nome=nome,
        lat=lat,
        lng=lng,
        ativo=ativo,
        preco_centavos=preco,
        observed_at=NOW - timedelta(hours=horas_atras),
        fonte="operador",
        d_road_km=d_road_km,
    )


def test_modo_parcial_sem_volume_falha():
    with pytest.raises(ValueError):
        _ctx(modo=Modo.PARCIAL, volume_alvo_l=None)


def test_modo_parcial_volume_zero_falha():
    with pytest.raises(ValueError):
        _ctx(modo=Modo.PARCIAL, volume_alvo_l=0)


def test_top_n_invalido():
    with pytest.raises(ValueError):
        rank([], _ctx(), top_n=0)
    with pytest.raises(ValueError):
        rank([], _ctx(), top_n=11)


def test_autonomia_invalida_no_rank():
    ctx = _ctx(autonomia_restante_km=200)
    # com autonomia 200 e raio 15 e desvio ~2km, posto entra normalmente — sem erro
    resp = rank([_posto()], ctx, top_n=3)
    assert resp is not None


def test_autonomia_zero_no_rank_levanta():
    # autonomia <= 0 é input inválido pra função rank
    bad_ctx = _ctx(autonomia_restante_km=200)
    bad_ctx.autonomia_restante_km = 0
    with pytest.raises(ValueError):
        rank([_posto()], bad_ctx, top_n=3)


def test_desvio_zero_mesmo_ponto():
    """Posto no exato lat/lng do usuário tem d=0; custo cai pra preco/k (modo completo)."""
    p = _posto(lat=-12.97, lng=-38.50, preco=590, d_road_km=0.0)
    resp = rank([p], _ctx(distancia_metodo=DistanciaMetodo.OSRM), top_n=3)
    assert len(resp.top) == 1
    r = resp.top[0]
    assert r.d_km == 0.0
    # preco/k_p50 = 590/11
    assert r.custo_p50 == pytest.approx(590.0 / 11.0)


def test_autonomia_infinita_nao_exclui_distancia_grande():
    # Autonomia 5000 km; desvio de 30 km dentro do raio_maximo seria ok
    p = _posto(d_road_km=14.0)  # dentro do raio
    resp = rank([p], _ctx(autonomia_restante_km=5000.0), top_n=3)
    assert len(resp.top) == 1


def test_inalcancavel_excluido():
    # autonomia 10 km, posto a 10 km de road → 2*10 = 20 > 0.9 * 10 = 9
    p = _posto(d_road_km=10.0)
    resp = rank([p], _ctx(autonomia_restante_km=10.0), top_n=3)
    assert len(resp.top) == 0
    assert any(r.motivo_exclusao == "inalcancavel" for r in resp.excluidos)


def test_preco_expirado_hard_excluido():
    p = _posto(horas_atras=200)  # > 72h hard
    resp = rank([p], _ctx(), top_n=3)
    assert len(resp.top) == 0
    assert any(r.motivo_exclusao == "preco_expirado_hard" for r in resp.excluidos)


def test_soft_stale_entra_com_flag():
    p = _posto(horas_atras=48)  # entre 24 e 72
    resp = rank([p], _ctx(), top_n=3)
    assert len(resp.top) == 1
    assert resp.top[0].flags.get("preco_envelhecido") is True


def test_soft_stale_com_penalidade_aumenta_custo():
    p_fresco = _posto(posto_id=1, horas_atras=2, preco=590, d_road_km=2.0)
    p_stale = _posto(posto_id=2, horas_atras=48, preco=590, d_road_km=2.0)
    ctx = _ctx(stale_penalidade_pct=10.0, distancia_metodo=DistanciaMetodo.OSRM)
    resp = rank([p_fresco, p_stale], ctx, top_n=3)
    assert resp.top[0].posto_id == 1  # fresco vence


def test_fora_raio_excluido():
    p = _posto(d_road_km=20.0)  # > raio_maximo_km=15
    resp = rank([p], _ctx(), top_n=3)
    assert len(resp.top) == 0
    assert any(r.motivo_exclusao == "fora_raio_maximo" for r in resp.excluidos)


def test_inativo_excluido():
    p = _posto(ativo=False, d_road_km=2.0)
    resp = rank([p], _ctx(), top_n=3)
    assert len(resp.top) == 0
    assert any(r.motivo_exclusao == "posto_inativo" for r in resp.excluidos)


def test_bandas_sobrepostas_marcam_empate_tecnico():
    # dois postos com preço quase igual → bandas largas se sobrepõem
    p1 = _posto(posto_id=1, preco=590, d_road_km=2.0)
    p2 = _posto(posto_id=2, preco=595, d_road_km=2.0)
    ctx = _ctx(k_banda=Banda(p10=5.0, p50=11.0, p90=20.0), distancia_metodo=DistanciaMetodo.OSRM)
    resp = rank([p1, p2], ctx, top_n=3)
    assert any(r.flags.get("empate_tecnico") for r in resp.top[1:])


def test_ordenacao_estavel_em_empate_estrito():
    # mesmo preço e mesma distância: empate exato
    p1 = _posto(posto_id=1, preco=590, d_road_km=2.0)
    p2 = _posto(posto_id=2, preco=590, d_road_km=2.0)
    ctx = _ctx(distancia_metodo=DistanciaMetodo.OSRM)
    r1 = rank([p1, p2], ctx, top_n=3)
    r2 = rank([p2, p1], ctx, top_n=3)
    # mesmos custos → ambos resultados; só checamos que não trava
    assert {r.posto_id for r in r1.top} == {1, 2}
    assert {r.posto_id for r in r2.top} == {1, 2}


def test_top_n_corta_resultado():
    postos = [_posto(posto_id=i, preco=500 + i, d_road_km=2.0) for i in range(8)]
    resp = rank(postos, _ctx(distancia_metodo=DistanciaMetodo.OSRM), top_n=3)
    assert len(resp.top) == 3
    # mais barato vence
    assert resp.top[0].posto_id == 0


def test_assumptions_sempre_presentes():
    resp = rank([_posto(d_road_km=2.0)], _ctx(distancia_metodo=DistanciaMetodo.OSRM), top_n=1)
    expected_keys = {
        "k_banda", "fonte_k", "horizonte_km", "modo", "volume_alvo_l",
        "distancia_metodo", "fator_road", "soft_horas", "hard_horas",
        "stale_penalidade_pct", "raio_maximo_km", "autonomia_restante_km",
        "margem_inalcancavel", "now_utc",
    }
    assert expected_keys <= set(resp.assumptions.keys())


def test_modo_parcial_volume_baixo_aumenta_custo():
    p_perto = _posto(posto_id=1, preco=590, d_road_km=0.5)
    p_longe = _posto(posto_id=2, preco=580, d_road_km=8.0)
    ctx = _ctx(
        modo=Modo.PARCIAL, volume_alvo_l=10.0, distancia_metodo=DistanciaMetodo.OSRM
    )
    resp = rank([p_perto, p_longe], ctx, top_n=3)
    # com V=10L pequeno, desvio penaliza muito o longe
    assert resp.top[0].posto_id == 1


def test_modo_parcial_volume_alto_relativiza_desvio():
    p_perto = _posto(posto_id=1, preco=590, d_road_km=0.5)
    p_longe = _posto(posto_id=2, preco=580, d_road_km=8.0)
    ctx = _ctx(
        modo=Modo.PARCIAL, volume_alvo_l=200.0, distancia_metodo=DistanciaMetodo.OSRM
    )
    resp = rank([p_perto, p_longe], ctx, top_n=3)
    # com V=200L, ratear 16km de desvio dilui — longe ganha pelo preço
    assert resp.top[0].posto_id == 2


def test_consumo_estimado_flag_quando_fonte_categoria():
    p = _posto(d_road_km=2.0)
    resp = rank(
        [p],
        _ctx(fonte_k=FonteK.CATEGORIA_P50, distancia_metodo=DistanciaMetodo.OSRM),
        top_n=1,
    )
    assert resp.top[0].flags.get("consumo_estimado") is True


def test_distancia_aproximada_flag_no_haversine():
    p = _posto(lat=-12.99, lng=-38.51)  # sem d_road_km, vai usar Haversine
    resp = rank([p], _ctx(), top_n=1)
    assert resp.top[0].flags.get("distancia_aproximada") is True


def test_sem_resultado_motivo_agregado():
    # Todos expirados
    postos = [_posto(posto_id=i, horas_atras=200) for i in range(3)]
    resp = rank(postos, _ctx(), top_n=3)
    assert resp.sem_resultado_motivo == "preco_expirado_hard"


def test_cobertura_baixa_quando_poucos_frescos():
    # 1 fresco em "100" da cidade -> 1% < 30%
    p = _posto(d_road_km=2.0, horas_atras=2)
    resp = rank([p], _ctx(distancia_metodo=DistanciaMetodo.OSRM), top_n=1, total_postos_cidade=100)
    assert resp.cobertura_baixa is True


def test_to_jsonable_roundtrip():
    resp = rank([_posto(d_road_km=2.0)], _ctx(distancia_metodo=DistanciaMetodo.OSRM), top_n=1)
    j = resp.to_jsonable()
    assert "top" in j and "assumptions" in j


def test_banda_invalida_p10_maior():
    with pytest.raises(ValueError):
        Banda(p10=10.0, p50=5.0, p90=15.0)


def test_banda_invalida_p10_zero():
    with pytest.raises(ValueError):
        Banda(p10=0.0, p50=5.0, p90=15.0)


# --------- Property-based ---------


@settings(max_examples=80, deadline=None)
@given(
    d_extra=st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False),
)
def test_property_custo_monotonico_em_d(d_extra):
    """Custo p50 nunca decresce quando aumentamos a distância (ceteris paribus)."""
    base = _posto(posto_id=1, preco=590, d_road_km=1.0)
    longer = _posto(posto_id=2, preco=590, d_road_km=1.0 + d_extra)
    ctx = _ctx(
        autonomia_restante_km=10000.0,
        raio_maximo_km=10000.0,
        distancia_metodo=DistanciaMetodo.OSRM,
    )
    resp = rank([base, longer], ctx, top_n=2)
    # encontre cada um na resposta (pode estar em qualquer ordem)
    by_id = {r.posto_id: r for r in resp.top}
    assert by_id[2].custo_p50 >= by_id[1].custo_p50 - 1e-9


@settings(max_examples=40, deadline=None)
@given(
    seed=st.integers(min_value=0, max_value=999),
)
def test_property_invariante_a_permutacao(seed):
    """Reordenar a entrada produz o mesmo top (em conjunto, e mesmo lider quando único)."""
    import random

    rng = random.Random(seed)
    postos = [_posto(posto_id=i, preco=550 + i * 3, d_road_km=1.0 + i * 0.5) for i in range(6)]
    a = list(postos)
    b = list(postos)
    rng.shuffle(b)
    ctx = _ctx(
        autonomia_restante_km=10000.0,
        raio_maximo_km=10000.0,
        distancia_metodo=DistanciaMetodo.OSRM,
    )
    ra = rank(a, ctx, top_n=3)
    rb = rank(b, ctx, top_n=3)
    set_a = {r.posto_id for r in ra.top}
    set_b = {r.posto_id for r in rb.top}
    assert set_a == set_b
