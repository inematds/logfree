"""Pydantic schemas com bounds (PLAN.md item 30b)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MelhorPostoRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    combustivel: Literal[
        "gasolina_comum", "gasolina_aditivada", "etanol",
        "diesel_s10", "diesel_s500", "gnv",
    ]
    autonomia_restante_km: float = Field(..., gt=0, le=2000)
    modo_abastecimento: Literal["parcial", "completo"]
    volume_alvo_litros: float | None = Field(default=None, ge=0.5, le=1000)
    top_n: int = Field(default=3, ge=1, le=10)
    cidade_id: int | None = None  # opcional; resolvido pelo user/bbox

    @model_validator(mode="after")
    def _check_volume(self):
        if self.modo_abastecimento == "parcial" and (
            self.volume_alvo_litros is None or self.volume_alvo_litros <= 0
        ):
            raise ValueError("modo parcial exige volume_alvo_litros > 0")
        return self


class PostoResultado(BaseModel):
    posto_id: int
    nome: str
    preco_centavos: int
    d_km: float
    custo_p50: float
    custo_p10: float
    custo_p90: float
    idade_horas: float
    flags: dict


class MelhorPostoResponse(BaseModel):
    top: list[PostoResultado]
    cobertura_baixa: bool
    sem_resultado_motivo: str | None
    assumptions: dict
    schema_version: int = 1
