"""Alerta de cobertura fresca de preços (PLAN.md item 14 + 41b)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.telemetry import emit
from app.db.models import Cidade, Posto, Preco


def calcular_cobertura(s: Session, cidade: Cidade, agora: datetime | None = None) -> float:
    """% de postos ativos com preço fresco (< soft horas) em pelo menos 1 combustível."""
    agora = agora or datetime.now(tz=UTC)
    soft_cutoff = (agora - timedelta(hours=cidade.preco_validade_horas_soft)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    total_ativos = s.query(Posto).filter(Posto.cidade_id == cidade.id, Posto.ativo == 1).count()
    if total_ativos == 0:
        return 100.0
    com_preco_fresco = (
        s.query(Preco.posto_id)
        .join(Posto, Posto.id == Preco.posto_id)
        .filter(Posto.cidade_id == cidade.id, Posto.ativo == 1, Preco.observed_at >= soft_cutoff)
        .distinct()
        .count()
    )
    return (com_preco_fresco / total_ativos) * 100.0


def alertar_se_baixa(s: Session, agora: datetime | None = None) -> list[dict]:
    """Para cada cidade, calcula cobertura; emite alerta se < threshold."""
    out = []
    for cidade in s.execute(select(Cidade).where(Cidade.is_demo == 0)).scalars():
        pct = calcular_cobertura(s, cidade, agora)
        if pct < cidade.stale_coverage_alert_pct:
            emit("preco.staleness_alert", cidade_id=cidade.id, pct_fresco=round(pct, 1))
            out.append({"cidade_id": cidade.id, "pct_fresco": pct})
    return out
