"""Motor de decisão LogFree — função pura, sem I/O.

Implementa fórmulas (b) do PLAN.md item 10/11:

    custo_por_km(p) = preco/k + (2*d * preco/k) / horizonte_km   # modo completo
    custo_por_litro(p, V) = preco + (2*d/k) * preco / V          # modo parcial

Filtros: inativo / fora bbox / preço expirado (hard) / inalcançável / fora raio_max.
Bandas de incerteza p10/p50/p90 levadas no ranking; postos com bandas sobrepostas
viram empate técnico ('≈'). Política soft/hard: stale entra com flag e penalidade
opcional. Retorna `assumptions` em toda resposta.

Sem dependência de DB. Tudo via dataclasses de input.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

from app.core.distance import haversine_km


class Modo(str, Enum):
    PARCIAL = "parcial"
    COMPLETO = "completo"


class DistanciaMetodo(str, Enum):
    OSRM = "osrm"
    HAVERSINE_FATOR = "haversine_x_fator"


class FonteK(str, Enum):
    PERFIL = "perfil"
    CATEGORIA_P50 = "categoria_p50"
    DEFAULT = "default"


@dataclass(frozen=True)
class Banda:
    """Banda p10/p50/p90 de consumo (km/l)."""
    p10: float
    p50: float
    p90: float

    def __post_init__(self) -> None:
        if not (self.p10 <= self.p50 <= self.p90):
            raise ValueError("Banda inválida: p10 <= p50 <= p90")
        if self.p10 <= 0:
            raise ValueError("Banda p10 deve ser > 0")


@dataclass(frozen=True)
class PostoCandidato:
    posto_id: int
    nome: str
    lat: float
    lng: float
    ativo: bool
    preco_centavos: int
    observed_at: datetime  # UTC tz-aware
    fonte: str
    # `d_road_km` opcional: se None, calculamos Haversine × fator.
    d_road_km: float | None = None


@dataclass
class ContextoRanking:
    lat_usuario: float
    lng_usuario: float
    autonomia_restante_km: float
    modo: Modo
    volume_alvo_l: float | None  # obrigatório no modo PARCIAL
    k_banda: Banda
    fonte_k: FonteK
    horizonte_km: float
    raio_maximo_km: float
    fator_road: float
    soft_horas: int
    hard_horas: int
    stale_penalidade_pct: float = 0.0  # 0.0 = stale entra sem penalidade
    margem_inalcancavel: float = 0.9
    distancia_metodo: DistanciaMetodo = DistanciaMetodo.HAVERSINE_FATOR
    now: datetime | None = None  # default = utcnow

    def __post_init__(self) -> None:
        if self.modo == Modo.PARCIAL and (self.volume_alvo_l is None or self.volume_alvo_l <= 0):
            raise ValueError("modo PARCIAL exige volume_alvo_l > 0")
        if self.now is None:
            self.now = datetime.now(tz=UTC)
        if self.now.tzinfo is None:
            self.now = self.now.replace(tzinfo=UTC)


@dataclass
class ResultadoPosto:
    posto_id: int
    nome: str
    preco_centavos: int
    d_km: float
    custo_p50: float          # custo na unidade do modo (R$/km ou R$/L)
    custo_p10: float
    custo_p90: float
    idade_horas: float
    flags: dict
    motivo_exclusao: str | None = None


@dataclass
class RespostaRanking:
    top: list[ResultadoPosto]
    excluidos: list[ResultadoPosto]
    assumptions: dict
    cobertura_baixa: bool
    sem_resultado_motivo: str | None
    schema_version: int = 1

    def to_jsonable(self) -> dict:
        return {
            "top": [asdict(r) for r in self.top],
            "excluidos": [asdict(r) for r in self.excluidos],
            "assumptions": self.assumptions,
            "cobertura_baixa": self.cobertura_baixa,
            "sem_resultado_motivo": self.sem_resultado_motivo,
            "schema_version": self.schema_version,
        }


def _idade_horas(observed_at: datetime, now: datetime) -> float:
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    delta = now - observed_at
    return delta.total_seconds() / 3600.0


def _custo(preco: float, d: float, k: float, ctx: ContextoRanking) -> float:
    """Custo na unidade do modo. preco em R$ (ou centavos), d em km, k em km/l."""
    if ctx.modo == Modo.COMPLETO:
        # custo_por_km = preco/k + (2d * preco/k) / horizonte
        return preco / k + (2.0 * d * preco / k) / ctx.horizonte_km
    # PARCIAL: custo_por_litro = preco + (2d/k) * preco / V
    assert ctx.volume_alvo_l is not None
    return preco + (2.0 * d / k) * preco / ctx.volume_alvo_l


def _calcular_d_km(p: PostoCandidato, ctx: ContextoRanking) -> float:
    if p.d_road_km is not None:
        return p.d_road_km
    return haversine_km(ctx.lat_usuario, ctx.lng_usuario, p.lat, p.lng) * ctx.fator_road


def _bandas_sobrepoem(a: ResultadoPosto, b: ResultadoPosto) -> bool:
    """Empate técnico: intervalos [p10,p90] tem interseção."""
    return not (a.custo_p90 < b.custo_p10 or b.custo_p90 < a.custo_p10)


def rank(
    candidatos: Iterable[PostoCandidato],
    ctx: ContextoRanking,
    top_n: int = 3,
    total_postos_cidade: int | None = None,
) -> RespostaRanking:
    """Ranqueia postos. Retorna top N + excluidos com motivo + assumptions."""
    if top_n < 1 or top_n > 10:
        raise ValueError("top_n ∈ [1,10]")
    if ctx.k_banda.p10 <= 0:
        raise ValueError("k_banda.p10 > 0")
    if ctx.autonomia_restante_km <= 0:
        raise ValueError("autonomia_restante_km > 0")

    aceitos: list[ResultadoPosto] = []
    excluidos: list[ResultadoPosto] = []
    soft_cutoff = ctx.now - timedelta(hours=ctx.soft_horas)
    hard_cutoff = ctx.now - timedelta(hours=ctx.hard_horas)
    candidatos_total = 0
    elegiveis_apos_filtros = 0
    frescos = 0

    for p in candidatos:
        candidatos_total += 1
        idade = _idade_horas(p.observed_at, ctx.now)
        d_km = _calcular_d_km(p, ctx)
        flags = {}

        if not p.ativo:
            excluidos.append(_excluido(p, d_km, idade, "posto_inativo"))
            continue
        if p.observed_at < hard_cutoff:
            excluidos.append(_excluido(p, d_km, idade, "preco_expirado_hard"))
            continue
        if d_km > ctx.raio_maximo_km:
            excluidos.append(_excluido(p, d_km, idade, "fora_raio_maximo"))
            continue
        if 2.0 * d_km > ctx.autonomia_restante_km * ctx.margem_inalcancavel:
            excluidos.append(_excluido(p, d_km, idade, "inalcancavel"))
            continue

        elegiveis_apos_filtros += 1
        is_stale = p.observed_at < soft_cutoff
        if not is_stale:
            frescos += 1
            preco_efetivo = float(p.preco_centavos)
        else:
            flags["preco_envelhecido"] = True
            preco_efetivo = float(p.preco_centavos) * (1.0 + ctx.stale_penalidade_pct / 100.0)

        if ctx.distancia_metodo == DistanciaMetodo.HAVERSINE_FATOR:
            flags["distancia_aproximada"] = True
        if ctx.fonte_k != FonteK.PERFIL:
            flags["consumo_estimado"] = True

        c_p50 = _custo(preco_efetivo, d_km, ctx.k_banda.p50, ctx)
        # bandas: p10 do consumo gera custo MAIOR (consumo pior), p90 gera custo menor.
        # p10 e p90 trocam de papel no custo:
        c_p10_consumo = _custo(preco_efetivo, d_km, ctx.k_banda.p10, ctx)  # custo p90
        c_p90_consumo = _custo(preco_efetivo, d_km, ctx.k_banda.p90, ctx)  # custo p10
        c_lo = min(c_p10_consumo, c_p90_consumo)
        c_hi = max(c_p10_consumo, c_p90_consumo)
        aceitos.append(
            ResultadoPosto(
                posto_id=p.posto_id,
                nome=p.nome,
                preco_centavos=p.preco_centavos,
                d_km=round(d_km, 4),
                custo_p50=round(c_p50, 6),
                custo_p10=round(c_lo, 6),
                custo_p90=round(c_hi, 6),
                idade_horas=round(idade, 3),
                flags=flags,
                motivo_exclusao=None,
            )
        )

    # ordenação estável: por p50, depois por preco bruto, depois por observed_at (newest)
    aceitos.sort(
        key=lambda r: (r.custo_p50, r.preco_centavos, -r.idade_horas)
    )

    # marca empates técnicos com p50 do líder
    if aceitos:
        lider = aceitos[0]
        for r in aceitos:
            if r is lider:
                continue
            if _bandas_sobrepoem(lider, r):
                r.flags["empate_tecnico"] = True

    cobertura_baixa = False
    if total_postos_cidade and total_postos_cidade > 0:
        pct_fresco = frescos / total_postos_cidade * 100.0
        if pct_fresco < 30.0:  # threshold default; cidade pode ajustar via stale_coverage_alert_pct
            cobertura_baixa = True

    sem_resultado_motivo = None
    if not aceitos:
        if candidatos_total == 0:
            sem_resultado_motivo = "sem_postos"
        else:
            # motivo agregado mais comum
            counts: dict[str, int] = {}
            for r in excluidos:
                counts[r.motivo_exclusao or ""] = counts.get(r.motivo_exclusao or "", 0) + 1
            sem_resultado_motivo = max(counts, key=counts.get) if counts else "todos_excluidos"

    assumptions = {
        "k_banda": {"p10": ctx.k_banda.p10, "p50": ctx.k_banda.p50, "p90": ctx.k_banda.p90},
        "fonte_k": ctx.fonte_k.value,
        "horizonte_km": ctx.horizonte_km,
        "modo": ctx.modo.value,
        "volume_alvo_l": ctx.volume_alvo_l,
        "distancia_metodo": ctx.distancia_metodo.value,
        "fator_road": ctx.fator_road,
        "soft_horas": ctx.soft_horas,
        "hard_horas": ctx.hard_horas,
        "stale_penalidade_pct": ctx.stale_penalidade_pct,
        "raio_maximo_km": ctx.raio_maximo_km,
        "autonomia_restante_km": ctx.autonomia_restante_km,
        "margem_inalcancavel": ctx.margem_inalcancavel,
        "now_utc": ctx.now.isoformat(),
    }

    return RespostaRanking(
        top=aceitos[:top_n],
        excluidos=excluidos,
        assumptions=assumptions,
        cobertura_baixa=cobertura_baixa,
        sem_resultado_motivo=sem_resultado_motivo,
    )


def _excluido(p: PostoCandidato, d_km: float, idade: float, motivo: str) -> ResultadoPosto:
    return ResultadoPosto(
        posto_id=p.posto_id,
        nome=p.nome,
        preco_centavos=p.preco_centavos,
        d_km=round(d_km, 4),
        custo_p50=0.0,
        custo_p10=0.0,
        custo_p90=0.0,
        idade_horas=round(idade, 3),
        flags={},
        motivo_exclusao=motivo,
    )
