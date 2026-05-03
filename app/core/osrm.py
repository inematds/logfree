"""Cliente OSRM Table com timeouts, circuit breaker e cache LRU.

PLAN.md item 12: connect 1s / read 2s / total 3s, fanout máx 30, cache 50 MB,
breaker 3 falhas em 30s → 60s aberto, fallback Haversine × fator (com flag).
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass

import httpx

from app.core.distance import haversine_km
from app.core.settings import get_settings


class CircuitBreaker:
    """Breaker simples por contagem de falhas em janela deslizante."""

    def __init__(self, max_failures: int, window_s: float, open_s: float) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.open_s = open_s
        self._failures: list[float] = []
        self._opened_at: float | None = None

    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at > self.open_s:
            # half-open: tentar de novo
            self._opened_at = None
            self._failures.clear()
            return False
        return True

    def record_success(self) -> None:
        self._failures.clear()
        self._opened_at = None

    def record_failure(self) -> None:
        now = time.monotonic()
        cutoff = now - self.window_s
        self._failures = [t for t in self._failures if t > cutoff]
        self._failures.append(now)
        if len(self._failures) >= self.max_failures:
            self._opened_at = now


class OSRMCache:
    """LRU com cap em bytes (estimado por len(json) por entrada)."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._d: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._size = 0

    @staticmethod
    def _key(origem: tuple[float, float], destino: tuple[float, float]) -> str:
        return f"{round(origem[0], 4)},{round(origem[1], 4)}|{round(destino[0], 4)},{round(destino[1], 4)}"

    def get(self, origem, destino) -> float | None:
        k = self._key(origem, destino)
        if k not in self._d:
            return None
        valor, _ts = self._d.pop(k)
        self._d[k] = (valor, time.time())  # touch
        return valor

    def put(self, origem, destino, valor: float) -> None:
        k = self._key(origem, destino)
        if k in self._d:
            self._d.pop(k)
            self._size -= len(k) + 24
        self._d[k] = (valor, time.time())
        self._size += len(k) + 24
        while self._size > self.max_bytes and self._d:
            old_k, _ = self._d.popitem(last=False)
            self._size -= len(old_k) + 24

    def __len__(self) -> int:
        return len(self._d)


@dataclass
class TableResult:
    """Distâncias km do origem para cada destino, na ordem de input."""
    distances_km: list[float]
    used_fallback: bool


class OSRMClient:
    def __init__(
        self,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        s = get_settings()
        self.base_url = (base_url or s.osrm_base_url).rstrip("/")
        self._connect = s.osrm_connect_timeout_s
        self._read = s.osrm_read_timeout_s
        self._total = s.osrm_total_timeout_s
        self._fanout_max = s.osrm_fanout_max
        self._cache = OSRMCache(s.osrm_cache_max_bytes)
        self._breaker = CircuitBreaker(
            max_failures=s.osrm_cb_failures,
            window_s=s.osrm_cb_window_s,
            open_s=s.osrm_cb_open_s,
        )
        self._fator = 1.35  # default; override via cidade.fator_road_default
        self._client = client
        self._owns_client = client is None
        self._lock = asyncio.Lock()
        # métricas em memória
        self.metrics = {"calls": 0, "fallback": 0, "cache_hit": 0, "errors": 0}

    async def __aenter__(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._total, connect=self._connect, read=self._read),
            )
        return self

    async def __aexit__(self, *_):
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def table(
        self,
        origem: tuple[float, float],
        destinos: list[tuple[float, float]],
        fator_road: float = 1.35,
    ) -> TableResult:
        """Distância de `origem` a cada `destino`. Retorna km. Pode usar fallback."""
        if not destinos:
            return TableResult(distances_km=[], used_fallback=False)
        # Cap de fanout
        if len(destinos) > self._fanout_max:
            destinos = destinos[: self._fanout_max]

        self.metrics["calls"] += 1

        # Cache lookup primeiro (todas as entradas, qualquer um faltando vira chamada)
        cached = [self._cache.get(origem, d) for d in destinos]
        if all(v is not None for v in cached):
            self.metrics["cache_hit"] += 1
            return TableResult(distances_km=cached, used_fallback=False)  # type: ignore[arg-type]

        if self._breaker.is_open():
            return self._fallback(origem, destinos, fator_road)

        if self._client is None:
            # uso fora de async with → fallback direto
            return self._fallback(origem, destinos, fator_road)

        coords = ";".join(f"{lng},{lat}" for lat, lng in [origem, *destinos])
        url = f"{self.base_url}/table/v1/driving/{coords}"
        params = {"sources": "0", "annotations": "distance"}
        try:
            r = await asyncio.wait_for(
                self._client.get(url, params=params), timeout=self._total
            )
            r.raise_for_status()
            payload = r.json()
            row = payload.get("distances", [[]])[0]
            if len(row) < len(destinos) + 1:
                raise ValueError("OSRM resposta incompleta")
            # row[0] = origem→origem (0); row[i+1] = origem→destinos[i]
            km = [(d / 1000.0) for d in row[1:]]
            for d, val in zip(destinos, km, strict=True):
                self._cache.put(origem, d, val)
            self._breaker.record_success()
            return TableResult(distances_km=km, used_fallback=False)
        except (TimeoutError, httpx.HTTPError, ValueError, KeyError) as exc:
            self.metrics["errors"] += 1
            self._breaker.record_failure()
            return self._fallback(origem, destinos, fator_road, error=str(exc))

    def _fallback(
        self,
        origem: tuple[float, float],
        destinos: list[tuple[float, float]],
        fator_road: float,
        error: str | None = None,
    ) -> TableResult:
        self.metrics["fallback"] += 1
        kms = [haversine_km(origem[0], origem[1], d[0], d[1]) * fator_road for d in destinos]
        return TableResult(distances_km=kms, used_fallback=True)

    @property
    def cache_size(self) -> int:
        return len(self._cache)
