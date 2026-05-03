# Backup falhou (P1)

## Sintoma
- Job de backup diário não rodou ou retornou erro.
- Idade do último snapshot ok > 24h.

## O que verificar
1. `/internal/metrics` → gauge `backup.last_ok_iso` (idade).
2. Logs do job: `evento_audit` com `acao='snapshot.create'` na última hora.
3. Espaço em disco no destino do backup.
4. Chave de encriptação (`age` key) acessível?

## Comandos seguros
```bash
# Roda backup manualmente.
sqlite3 /data/logfree/logfree.sqlite ".backup /tmp/manual.sqlite"
age -e -i /etc/logfree/backup.pub /tmp/manual.sqlite > /data/backups/manual-$(date +%F).sqlite.age
```

## Quando escalar
- 2 dias seguidos de falha → P1.
- Disk full no destino → ver `runbook/capacity.md`.
