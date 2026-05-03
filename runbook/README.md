# Runbooks LogFree

Cada runbook segue o template: **sintoma → o que verificar → comandos seguros → quando escalar → rollback**.

| Arquivo | Sinal disparador | Severidade |
|---|---|---|
| [api-errors.md](api-errors.md) | `/melhor-posto` 5xx > 1% por 5 min | P1 |
| [latency.md](latency.md) | `/melhor-posto` p95 > 800ms por 5 min | P2 |
| [osrm.md](osrm.md) | `osrm.fallback` > 20% req em 5 min | P2 |
| [price-staleness.md](price-staleness.md) | cobertura fresca < SLO por 6h | P2 |
| [no-result.md](no-result.md) | `melhor_posto.no_result` > 20% por 30 min | P2 |
| [backup.md](backup.md) | backup diário falhou | P1 |
| [restore.md](restore.md) | restore-test semanal falhou ou restore manual | P1 |
| [audit-chain-break.md](audit-chain-break.md) | `audit.chain_break` | P1 |
| [audit-verifier.md](audit-verifier.md) | `audit.verifier_error` | P2 |
| [db-lock.md](db-lock.md) | DB busy/lock retry > 50/min | P2 |
| [owner-lease.md](owner-lease.md) | `owner_lease.lose` | P1 |
| [authz-anomaly.md](authz-anomaly.md) | `authz.deny` em rota nunca-deve-falhar | P2 |
| [disk.md](disk.md) | volume > 80% / > 90% | P2/P1 |
| [wal-bloat.md](wal-bloat.md) | WAL > 200 MB | P2 |
| [secrets.md](secrets.md) | rotação de segredos (planejada) | — |
| [capacity.md](capacity.md) | quotas e cleanups | — |
| [rollback.md](rollback.md) | reverter deploy após bug | P1 |
| [anonymization.md](anonymization.md) | anonimização atrasada | P2 |
