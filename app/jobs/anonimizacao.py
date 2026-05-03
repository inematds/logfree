"""Job de anonimização de `consulta` após retenção (PLAN.md item 9)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.models import Consulta


def k_anon_grid(lat: float, lng: float, grid_m: int = 500) -> tuple[float, float]:
    """Snap lat/lng em grid de ~500m. ~0.0045 graus = 500m no equador."""
    step = 0.0045 * (grid_m / 500.0)
    return (round(lat / step) * step, round(lng / step) * step)


def anonimizar(s: Session, agora: datetime | None = None) -> int:
    """Zera lat/lng de consultas mais antigas que retention_days. Retorna contagem."""
    settings = get_settings()
    agora = agora or datetime.now(tz=UTC)
    cutoff = (agora - timedelta(days=settings.consulta_retention_days)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    result = s.execute(
        update(Consulta)
        .where(Consulta.feita_em < cutoff, Consulta.anonimizada == 0)
        .values(lat=None, lng=None, top_n_resultado_json="[]", anonimizada=1)
    )
    s.commit()
    return result.rowcount or 0
