# Rollback de deploy (P1)

## Sintoma
- Deploy recém-publicado quebrou produção: 5xx persistente, `/internal/ready` retornando 503, ou behavioral regression observada por motorista/operador.

## O que verificar
1. `git log --oneline -5` — qual commit foi promovido?
2. Painel admin → métricas `/internal/metrics` — counters de erro subindo desde quando?
3. `evento_audit` filtrado por `acao='deploy.start'` ou `migration.upgrade` desde a última hora.
4. `alembic current` no host de produção: qual revisão está aplicada?
5. Há migração nova entre `git_sha` antigo e atual? (`alembic history --rev-range PREV_SHA..CURRENT_SHA`)

## Comandos seguros
**Caminho A — apenas código mudou (sem migração nova):**
```bash
# Promove o sha anterior; deploy stop-the-world via plataforma.
fly deploy --image-label <git_sha_anterior>   # ou Railway: deploy do commit anterior
```

**Caminho B — código + migração nova (downgrade seguro):**
```bash
# 1. Para tráfego (drain).
fly scale count 0
# 2. Downgrade da migração.
alembic downgrade -1
# 3. Promove código anterior.
fly deploy --image-label <git_sha_anterior>
fly scale count 1
# 4. Verifica /internal/ready=200 antes de re-registrar webhook do Telegram.
```

**Caminho C — migração marcada `requires_restore_only=True`:**
```bash
# Não tem downgrade seguro. Restore do snapshot pré-deploy.
fly scale count 0
# Recupera snapshot pre-deploy-<git_sha> do storage de backup.
age -d -i /etc/logfree/backup.key snapshot.sqlite.age > /data/logfree/logfree.sqlite
# Promove código anterior.
fly deploy --image-label <git_sha_anterior>
fly scale count 1
# IMPORTANTE: rodar replay_tombstones para re-anonimizar dados de usuários apagados.
logfree replay-tombstones
# Verifica audit chain.
logfree audit-verify
```

## Quando escalar
- Se restore não recupera estado consistente.
- Se `audit-verify` falha após restore (chain break introduzido pelo backup).
- Se há suspeita de tampering do backup encriptado.

## Pós-rollback
- Documentar incidente (timeline + causa) no canal interno.
- Abrir issue para o bug que causou rollback.
- Manter snapshot pre-deploy do release ruim para análise forense.
