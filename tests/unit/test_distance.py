from __future__ import annotations

import math

import pytest

from app.core.distance import haversine_km


def test_haversine_zero():
    assert haversine_km(0, 0, 0, 0) == pytest.approx(0.0)


def test_haversine_one_degree_lat():
    # ~111.19 km per 1 degree latitude
    d = haversine_km(0, 0, 1, 0)
    assert 110 < d < 112


def test_haversine_symmetric():
    a = haversine_km(-12.97, -38.50, -12.99, -38.51)
    b = haversine_km(-12.99, -38.51, -12.97, -38.50)
    assert a == pytest.approx(b)


def test_haversine_nonneg():
    assert haversine_km(-12.97, -38.50, -12.99, -38.51) >= 0
    assert haversine_km(89, 179, -89, -179) >= 0


def test_haversine_antipodes():
    # Aproximadamente meia circunferência da terra
    d = haversine_km(0, 0, 0, 180)
    assert math.isfinite(d)
    assert 19_000 < d < 21_000
