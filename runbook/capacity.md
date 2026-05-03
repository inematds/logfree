# Capacidade e quotas

## Volume de dados (Railway/Fly)

- Provisão inicial: 10 GB.
- Alertas: 80% (P2), 90% (P1).
- Cleanup automático:
  - snapshots `pre-deploy-*` > 30 dias removidos.
  - backups diários > 14 dias removidos.
  - logs locais rotacionados (logrotate, 7 dias).

## SQLite WAL

- `PRAGMA wal_autocheckpoint=1000`.
- Job a cada 5 min: `PRAGMA wal_checkpoint(PASSIVE)`.
- Alerta `WAL > 200 MB` (P2): geralmente sintoma de leitor longo segurando o WAL.

```bash
# Diagnóstico:
sqlite3 /data/logfree/logfree.sqlite "PRAGMA wal_checkpoint(TRUNCATE)"
ls -lh /data/logfree/logfree.sqlite-wal
```

## Cache OSRM

- Teto 50 MB (LRU). Cleanup diário.
- Alerta `hit-rate < 50%` (P3) → revisar TTL ou expandir cache.

## Audit append-only copy

- Volume separado, write-only token.
- Retenção 365 dias online; arquivamento p/ storage frio depois de 90 dias.
- Quota 5 GB.

## Backups

- Online: 14 dias.
- Snapshots pre-deploy: 30 dias.
- Total ≤ 5 GB.

## Fotos de abastecimento

- Teto 1 GB.
- Cleanup de fotos órfãs (sem `abastecimento_relatado`) após 7 dias.
- Redimensionamento máx 1024px no upload.

## Comportamento disk-full

- Aplicação detecta `< 5%` livre → recusa novas escritas com 503 "manutenção".
- Grava `evento_audit('disk_full')`.
- Alerta P1.

## Comandos úteis

```bash
# Uso por subdir
du -sh /data/logfree/* /data/audit-archive/* /data/backups/* /data/fotos/*

# Top 10 maiores
find /data -type f -exec du -sh {} + | sort -rh | head -10

# Forçar cleanup de snapshots
find /data/backups/snapshots -mtime +30 -delete
```
