"""Owner lease em três camadas (PLAN.md item 26).

- Filesystem: flock no DATA_DIR/owner.lock
- DB: tabela owner_lease com heartbeat
- Plataforma: replicas=1 (config externa, não checado aqui)

Falha em qualquer camada → não inicia o servidor.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import socket
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.base import utcnow_iso


class OwnerLeaseError(RuntimeError):
    pass


class OwnerLease:
    """Lease holder. Use start() ao subir, stop() ao desligar.

    Se perder o lease em runtime (heartbeat falha), `_lost` é setado e o caller
    pode chamar `panic_if_lost()` para derrubar o servidor.
    """

    def __init__(self, engine: Engine, data_dir: Path | None = None) -> None:
        self.engine = engine
        self.data_dir = data_dir or get_settings().data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.data_dir / "owner.lock"
        self.uuid = str(uuid.uuid4())
        self.host = socket.gethostname()
        self._fp = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._lost = False
        self._ttl_s = get_settings().owner_lease_ttl_s
        self._heartbeat_s = get_settings().owner_lease_heartbeat_s

    def _flock_acquire(self) -> None:
        self._fp = open(self.lock_path, "w")  # noqa: SIM115 — segurado pelo lifecycle do lease
        try:
            fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._fp.close()
            self._fp = None
            raise OwnerLeaseError(
                f"flock ocupado em {self.lock_path}; outro processo está rodando"
            ) from exc

    def _flock_release(self) -> None:
        if self._fp is not None:
            with contextlib.suppress(Exception):
                fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
            with contextlib.suppress(Exception):
                self._fp.close()
            self._fp = None

    def _db_acquire(self) -> None:
        """Adquire/renova lease via UPSERT condicional. Lança se outro válido."""
        now = utcnow_iso()
        cutoff = (
            datetime.now(tz=UTC) - timedelta(seconds=self._ttl_s)
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with Session(self.engine) as s:
            # Tentamos atualizar se id=1 está expirado OU somos o owner.
            res = s.execute(
                text(
                    """
                    UPDATE owner_lease
                       SET owner_uuid=:u, host=:h, started_at=:n, heartbeat_at=:n
                     WHERE id=1 AND (heartbeat_at < :cut OR owner_uuid=:u)
                    """
                ),
                {"u": self.uuid, "h": self.host, "n": now, "cut": cutoff},
            )
            if res.rowcount == 0:
                # tentar inserir; se já existe e não é nosso e não expirou → falha
                try:
                    s.execute(
                        text(
                            "INSERT INTO owner_lease (id, owner_uuid, host, started_at, heartbeat_at) "
                            "VALUES (1, :u, :h, :n, :n)"
                        ),
                        {"u": self.uuid, "h": self.host, "n": now},
                    )
                except Exception as exc:  # IntegrityError ou similar
                    s.rollback()
                    raise OwnerLeaseError(
                        f"owner_lease ocupado por outro processo: {exc}"
                    ) from exc
            s.commit()

    def _db_heartbeat(self) -> bool:
        now = utcnow_iso()
        with Session(self.engine) as s:
            res = s.execute(
                text(
                    "UPDATE owner_lease SET heartbeat_at=:n WHERE id=1 AND owner_uuid=:u"
                ),
                {"n": now, "u": self.uuid},
            )
            s.commit()
            return res.rowcount == 1

    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._heartbeat_s)
                break
            except TimeoutError:
                pass
            ok = await asyncio.to_thread(self._db_heartbeat)
            if not ok:
                self._lost = True
                break

    async def start(self) -> None:
        self._flock_acquire()
        try:
            await asyncio.to_thread(self._db_acquire)
        except Exception:
            self._flock_release()
            raise
        self._task = asyncio.create_task(self._heartbeat_loop(), name="owner-lease-hb")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(Exception):
                await self._task
        # libera lease no DB
        with contextlib.suppress(Exception), Session(self.engine) as s:
            s.execute(
                text("DELETE FROM owner_lease WHERE id=1 AND owner_uuid=:u"),
                {"u": self.uuid},
            )
            s.commit()
        self._flock_release()

    @property
    def lost(self) -> bool:
        return self._lost

    def panic_if_lost(self) -> None:
        if self._lost:
            os._exit(70)  # EX_SOFTWARE
