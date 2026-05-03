# Restore de backup (P1)

## Quando usar
- Restore-test semanal falhou.
- Corrupção do volume de produção.
- Rollback caminho C (migração destrutiva sem downgrade).

## Procedimento

1. **Para tráfego.**
   ```bash
   fly scale count 0
   ```

2. **Recupera snapshot encriptado mais recente válido.**
   ```bash
   age -d -i /etc/logfree/backup.key snapshot-YYYYMMDD.sqlite.age > /tmp/restore.sqlite
   sqlite3 /tmp/restore.sqlite "PRAGMA integrity_check;"
   # Esperado: 'ok'
   ```

3. **Substitui DB de produção.**
   ```bash
   mv /data/logfree/logfree.sqlite /data/logfree/logfree.sqlite.broken
   cp /tmp/restore.sqlite /data/logfree/logfree.sqlite
   ```

4. **Sobe o app — owner_lease vai aceitar.**
   ```bash
   fly scale count 1
   # Aguarda /internal/ready=200 (pode ficar 503 enquanto re-verifica audit chain).
   ```

5. **Replay tombstones.** Backup pode conter PII de usuários que pediram apagamento depois do snapshot.
   ```bash
   logfree replay-tombstones
   ```

6. **Verifica audit chain.**
   ```bash
   logfree audit-verify
   ```
   - Se quebrar: ver `runbook/audit-chain-break.md`.

7. **Re-registra webhook do Telegram** (segredo pode ter mudado entre snapshot e agora).
   ```bash
   curl -X POST https://api.telegram.org/bot<TOKEN>/setWebhook \
     -d "url=https://logfree.app/bot/webhook/$TG_WEBHOOK_SECRET" \
     -d "secret_token=$TG_WEBHOOK_SECRET"
   ```

8. **Documenta** o que foi feito em `evento_audit('restore.run')`.

## Quando escalar
- Snapshot mais recente falhou `integrity_check`.
- Não há snapshot menos de 24h atrás.
