"""Endpoint POST /melhor-posto e helpers de carga de candidatos do DB."""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AuthIdentity, auth_required, get_db
from app.api.schemas import MelhorPostoRequest, MelhorPostoResponse, PostoResultado
from app.core.audit import write_event  # noqa: F401
from app.core.flags import get_flag, kill_switch_global
from app.core.ranking import (
    Banda,
    ContextoRanking,
    DistanciaMetodo,
    FonteK,
    Modo,
    PostoCandidato,
    rank,
)
from app.core.rate_limit import limiter
from app.core.telemetry import emit, hash_id, round3
from app.db.models import (
    Cidade,
    Combustivel,
    Consulta,
    Posto,
    Preco,
    Usuario,
    UsuarioVeiculo,
    VeiculoPerfil,
    VeiculoTipo,
)

router = APIRouter()


def _resolve_cidade(db: Session, lat: float, lng: float, cidade_id: int | None) -> Cidade:
    """Resolve cidade pela bbox da localização (ou explícita)."""
    if cidade_id is not None:
        c = db.get(Cidade, cidade_id)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="cidade não encontrada")
        return c
    c = db.execute(
        select(Cidade).where(
            Cidade.bbox_min_lat <= lat,
            Cidade.bbox_max_lat >= lat,
            Cidade.bbox_min_lng <= lng,
            Cidade.bbox_max_lng >= lng,
        )
    ).scalars().first()
    if c is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="localização fora de cidade atendida",
        )
    return c


def _resolve_perfil(db: Session, usuario: Usuario, combustivel_id: int) -> VeiculoPerfil | None:
    """Perfil ativo do usuário compatível com o combustível pedido."""
    row = db.execute(
        select(VeiculoPerfil)
        .join(UsuarioVeiculo, UsuarioVeiculo.veiculo_perfil_id == VeiculoPerfil.id)
        .where(
            UsuarioVeiculo.usuario_id == usuario.id,
            UsuarioVeiculo.ativo == 1,
            VeiculoPerfil.combustivel_id == combustivel_id,
        )
        .limit(1)
    ).scalar_one_or_none()
    return row


def _candidates_para_cidade(
    db: Session, cidade_id: int, combustivel_id: int
) -> list[PostoCandidato]:
    """Para cada posto ativo da cidade, pega o preço mais recente do combustível."""
    # subselect: preço mais recente por (posto, combustivel)
    sub = (
        select(
            Preco.posto_id,
            func.max(Preco.observed_at).label("ob"),
        )
        .where(Preco.combustivel_id == combustivel_id)
        .group_by(Preco.posto_id)
        .subquery()
    )
    rows = db.execute(
        select(Posto, Preco)
        .join(sub, sub.c.posto_id == Posto.id)
        .join(
            Preco,
            (Preco.posto_id == Posto.id)
            & (Preco.combustivel_id == combustivel_id)
            & (Preco.observed_at == sub.c.ob),
        )
        .where(Posto.cidade_id == cidade_id, Posto.ativo == 1)
    ).all()

    out = []
    for posto, preco in rows:
        out.append(
            PostoCandidato(
                posto_id=posto.id,
                nome=posto.nome,
                lat=posto.lat,
                lng=posto.lng,
                ativo=bool(posto.ativo),
                preco_centavos=preco.valor_centavos,
                observed_at=datetime.fromisoformat(preco.observed_at.replace("Z", "+00:00")),
                fonte=preco.fonte,
                d_road_km=None,
            )
        )
    return out


def _banda_perfil(perfil: VeiculoPerfil | None, tipo: VeiculoTipo | None) -> tuple[Banda, FonteK]:
    if perfil and perfil.km_por_litro_p10 and perfil.km_por_litro_p90:
        return (
            Banda(p10=perfil.km_por_litro_p10, p50=perfil.km_por_litro,
                  p90=perfil.km_por_litro_p90),
            FonteK.PERFIL,
        )
    if perfil:
        # perfil sem bandas: usa do tipo se disponível
        if tipo:
            return (
                Banda(p10=tipo.kml_default_p10, p50=tipo.kml_default_p50,
                      p90=tipo.kml_default_p90),
                FonteK.PERFIL,
            )
        # último recurso: cria banda de ±25%
        k = perfil.km_por_litro
        return Banda(p10=k * 0.75, p50=k, p90=k * 1.25), FonteK.PERFIL
    if tipo:
        return (
            Banda(p10=tipo.kml_default_p10, p50=tipo.kml_default_p50,
                  p90=tipo.kml_default_p90),
            FonteK.CATEGORIA_P50,
        )
    # default conservador (carro)
    return Banda(p10=8.0, p50=11.0, p90=14.0), FonteK.DEFAULT


def _raio_max(perfil: VeiculoPerfil | None, tipo: VeiculoTipo | None) -> float:
    if tipo:
        return tipo.raio_maximo_km
    return 15.0


@router.post("/melhor-posto", response_model=MelhorPostoResponse)
async def melhor_posto(
    payload: MelhorPostoRequest,
    request: Request,
    identity: AuthIdentity = Depends(auth_required),
    db: Session = Depends(get_db),
) -> MelhorPostoResponse:
    if kill_switch_global(db):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="kill switch ativo")

    # Rate limit por usuário e por IP.
    if not limiter.allow(f"melhor:user:{identity.usuario_id}"):
        emit("rate_limit.deny", chave_prefix="melhor")
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="muitas requisições")
    ip = (request.client.host if request.client else "0.0.0.0")
    if not limiter.allow(f"ip:{ip}"):
        emit("rate_limit.deny", chave_prefix="ip")
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="muitas requisições por IP")

    cidade = _resolve_cidade(db, payload.lat, payload.lng, payload.cidade_id)
    combustivel = (
        db.query(Combustivel).filter(Combustivel.codigo == payload.combustivel).one_or_none()
    )
    if combustivel is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="combustivel desconhecido")

    perfil = _resolve_perfil(db, identity.usuario, combustivel.id)
    tipo = db.get(VeiculoTipo, perfil.tipo_id) if perfil else None
    banda, fonte_k = _banda_perfil(perfil, tipo)
    raio_max = _raio_max(perfil, tipo)

    candidatos = _candidates_para_cidade(db, cidade.id, combustivel.id)
    total_postos = db.query(Posto).filter(
        Posto.cidade_id == cidade.id, Posto.ativo == 1
    ).count()

    use_osrm = bool(get_flag(db, "ranking.osrm_enabled", cidade_id=cidade.id, default=False))
    distancia_metodo = DistanciaMetodo.OSRM if use_osrm else DistanciaMetodo.HAVERSINE_FATOR

    ctx = ContextoRanking(
        lat_usuario=payload.lat,
        lng_usuario=payload.lng,
        autonomia_restante_km=payload.autonomia_restante_km,
        modo=Modo(payload.modo_abastecimento),
        volume_alvo_l=payload.volume_alvo_litros,
        k_banda=banda,
        fonte_k=fonte_k,
        horizonte_km=cidade.horizonte_default_km,
        raio_maximo_km=raio_max,
        fator_road=cidade.fator_road_default,
        soft_horas=cidade.preco_validade_horas_soft,
        hard_horas=cidade.preco_validade_horas_hard,
        distancia_metodo=distancia_metodo,
        now=datetime.now(tz=UTC),
    )

    t0 = time.perf_counter()
    resp = rank(candidatos, ctx, top_n=payload.top_n, total_postos_cidade=total_postos)
    latencia_ms = int((time.perf_counter() - t0) * 1000)

    emit(
        "melhor_posto.request",
        usuario_id_hash=hash_id(identity.usuario_id),
        cidade_id=cidade.id,
        combustivel=payload.combustivel,
        modo=payload.modo_abastecimento,
        top_n=payload.top_n,
        lat3=round3(payload.lat),
        lng3=round3(payload.lng),
        assumptions_keys=sorted(resp.assumptions.keys()),
    )
    if not resp.top:
        emit("melhor_posto.no_result", motivo_agregado=resp.sem_resultado_motivo or "nenhum")
    else:
        emit(
            "melhor_posto.response",
            usuario_id_hash=hash_id(identity.usuario_id),
            posto_ids=[r.posto_id for r in resp.top],
            latencia_ms=latencia_ms,
            distancia_metodo=ctx.distancia_metodo.value,
            cobertura_baixa=resp.cobertura_baixa,
        )

    # Loga consulta no DB (síncrono no MVP — fila assíncrona pode ser plugada depois)
    db.add(
        Consulta(
            usuario_id=identity.usuario_id,
            lat=payload.lat,
            lng=payload.lng,
            combustivel_id=combustivel.id,
            autonomia_km=payload.autonomia_restante_km,
            modo=payload.modo_abastecimento,
            volume_alvo_l=payload.volume_alvo_litros,
            top_n=payload.top_n,
            top_n_resultado_json=json.dumps(
                [{"posto_id": r.posto_id, "custo_p50": r.custo_p50} for r in resp.top]
            ),
            assumptions_json=json.dumps(resp.assumptions, default=str),
        )
    )
    db.commit()

    return MelhorPostoResponse(
        top=[
            PostoResultado(
                posto_id=r.posto_id,
                nome=r.nome,
                preco_centavos=r.preco_centavos,
                d_km=r.d_km,
                custo_p50=r.custo_p50,
                custo_p10=r.custo_p10,
                custo_p90=r.custo_p90,
                idade_horas=r.idade_horas,
                flags=r.flags,
            )
            for r in resp.top
        ],
        cobertura_baixa=resp.cobertura_baixa,
        sem_resultado_motivo=resp.sem_resultado_motivo,
        assumptions=resp.assumptions,
    )
