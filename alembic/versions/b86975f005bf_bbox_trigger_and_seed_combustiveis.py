"""bbox trigger and seed combustiveis

Revision ID: b86975f005bf
Revises: 72dc738e6a61
Create Date: 2026-05-02 16:02:44.917637

"""
from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "b86975f005bf"
down_revision: str | Sequence[str] | None = "72dc738e6a61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


COMBUSTIVEIS = [
    "gasolina_comum",
    "gasolina_aditivada",
    "etanol",
    "diesel_s10",
    "diesel_s500",
    "gnv",
]


def upgrade() -> None:
    # Triggers de bbox: posto.lat/lng tem que cair na bbox da cidade do posto.
    for op_kind in ("INSERT", "UPDATE"):
        op.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS posto_bbox_{op_kind.lower()}_check
            BEFORE {op_kind} ON posto
            FOR EACH ROW
            BEGIN
              SELECT CASE
                WHEN (SELECT 1 FROM cidade c
                      WHERE c.id = NEW.cidade_id
                        AND NEW.lat BETWEEN c.bbox_min_lat AND c.bbox_max_lat
                        AND NEW.lng BETWEEN c.bbox_min_lng AND c.bbox_max_lng) IS NULL
                THEN RAISE(ABORT, 'posto fora da bbox da cidade')
              END;
            END;
            """
        )

    # Trigger de tombstone: bloqueia escrita futura em consulta para usuario tombstoned.
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS consulta_block_tombstoned
        BEFORE INSERT ON consulta
        FOR EACH ROW
        BEGIN
          SELECT CASE
            WHEN (SELECT 1 FROM tombstone_usuario t WHERE t.usuario_id = NEW.usuario_id) IS NOT NULL
            THEN RAISE(ABORT, 'usuario tombstoned')
          END;
        END;
        """
    )

    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS abastecimento_block_tombstoned
        BEFORE INSERT ON abastecimento_relatado
        FOR EACH ROW
        BEGIN
          SELECT CASE
            WHEN (SELECT 1 FROM tombstone_usuario t WHERE t.usuario_id = NEW.usuario_id) IS NOT NULL
            THEN RAISE(ABORT, 'usuario tombstoned')
          END;
        END;
        """
    )

    # Seed combustiveis (idempotente via INSERT OR IGNORE).
    for codigo in COMBUSTIVEIS:
        op.execute(f"INSERT OR IGNORE INTO combustivel (codigo) VALUES ('{codigo}')")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS abastecimento_block_tombstoned")
    op.execute("DROP TRIGGER IF EXISTS consulta_block_tombstoned")
    op.execute("DROP TRIGGER IF EXISTS posto_bbox_update_check")
    op.execute("DROP TRIGGER IF EXISTS posto_bbox_insert_check")
    op.execute("DELETE FROM combustivel WHERE codigo IN ('"
               + "','".join(COMBUSTIVEIS) + "')")
