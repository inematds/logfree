from __future__ import annotations

import time

import httpx
import pytest
import respx

from app.core.osrm import CircuitBreaker, OSRMCache, OSRMClient


def test_cache_lru_eviction_em_bytes():
    cache = OSRMCache(max_bytes=200)
    for i in range(50):
        cache.put((float(i), 0.0), (float(i + 1), 0.0), 1.0)
    # cache foi limitado
    assert cache._size <= 200 + 200  # margem por overhead da estimativa


def test_cache_hit_e_touch():
    cache = OSRMCache(max_bytes=10_000)
    cache.put((0.0, 0.0), (1.0, 1.0), 5.5)
    assert cache.get((0.0, 0.0), (1.0, 1.0)) == 5.5
    assert cache.get((9, 9), (9, 9)) is None


def test_circuit_breaker_abre_apos_falhas():
    cb = CircuitBreaker(max_failures=3, window_s=10, open_s=10)
    assert not cb.is_open()
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open()


def test_circuit_breaker_fecha_apos_open_s():
    cb = CircuitBreaker(max_failures=2, window_s=10, open_s=0.05)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open()
    time.sleep(0.06)
    assert not cb.is_open()


def test_circuit_breaker_sucesso_reseta():
    cb = CircuitBreaker(max_failures=3, window_s=10, open_s=10)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    assert not cb.is_open()


@pytest.mark.asyncio
@respx.mock
async def test_osrm_client_sucesso():
    respx.get("https://osrm.example/table/v1/driving/-38.5,-12.97;-38.51,-12.99").mock(
        return_value=httpx.Response(
            200, json={"distances": [[0, 2500.0]]}
        )
    )
    async with OSRMClient(base_url="https://osrm.example") as cli:
        res = await cli.table((-12.97, -38.50), [(-12.99, -38.51)])
    assert not res.used_fallback
    assert res.distances_km == pytest.approx([2.5])
    assert cli.metrics["calls"] == 1
    assert cli.metrics["fallback"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_osrm_client_fallback_on_500():
    respx.get("https://osrm.example/table/v1/driving/-38.5,-12.97;-38.51,-12.99").mock(
        return_value=httpx.Response(500)
    )
    async with OSRMClient(base_url="https://osrm.example") as cli:
        res = await cli.table((-12.97, -38.50), [(-12.99, -38.51)])
    assert res.used_fallback is True
    assert cli.metrics["fallback"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_osrm_circuit_breaker_abre_e_fallback():
    route = respx.get(url__startswith="https://osrm.example/table").mock(
        return_value=httpx.Response(500)
    )
    async with OSRMClient(base_url="https://osrm.example") as cli:
        for _ in range(3):
            await cli.table((-12.97, -38.50), [(-12.99, -38.51)])
        # breaker deve estar aberto agora
        assert cli._breaker.is_open()
        # próxima chamada não bate na rede
        before = route.call_count
        await cli.table((-12.97, -38.50), [(-12.99, -38.51)])
        assert route.call_count == before


@pytest.mark.asyncio
async def test_osrm_fanout_cap():
    async with OSRMClient(base_url="https://osrm.example") as cli:
        # client não vai chamar respx (sem mock), então cai em fallback
        # passamos 50 destinos; resultado tem ≤ fanout_max (30 default)
        destinos = [(-12.97, -38.50 + 0.001 * i) for i in range(50)]
        res = await cli.table((-12.97, -38.50), destinos)
        assert len(res.distances_km) <= cli._fanout_max


@pytest.mark.asyncio
async def test_osrm_lista_vazia():
    async with OSRMClient(base_url="https://osrm.example") as cli:
        res = await cli.table((-12.97, -38.50), [])
    assert res.distances_km == []
    assert res.used_fallback is False
