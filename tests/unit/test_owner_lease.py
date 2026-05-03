from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.core.owner_lease import OwnerLease, OwnerLeaseError
from app.db import models  # noqa: F401  -- registra tabelas em Base.metadata
from app.db.base import Base


def _engine(path: Path):
    e = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(e)
    return e


@pytest.mark.asyncio
async def test_lease_acquires_and_stops():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        e = _engine(d / "db.sqlite")
        lease = OwnerLease(e, data_dir=d)
        await lease.start()
        assert not lease.lost
        await lease.stop()


@pytest.mark.asyncio
async def test_segundo_processo_falha_no_flock():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        e = _engine(d / "db.sqlite")
        a = OwnerLease(e, data_dir=d)
        await a.start()
        b = OwnerLease(e, data_dir=d)
        with pytest.raises(OwnerLeaseError):
            await b.start()
        await a.stop()


@pytest.mark.asyncio
async def test_apos_stop_outro_processo_assume():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        e = _engine(d / "db.sqlite")
        a = OwnerLease(e, data_dir=d)
        await a.start()
        await a.stop()
        b = OwnerLease(e, data_dir=d)
        await b.start()
        await b.stop()
