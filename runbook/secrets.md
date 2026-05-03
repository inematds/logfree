# Rotação de segredos

Janelas de duplo-aceite onde aplicável. Toda rotação grava `evento_audit('secret_rotation')`.

## Telegram webhook secret (`TG_WEBHOOK_SECRET`)

```bash
NEW=$(openssl rand -hex 32)
fly secrets set TG_WEBHOOK_SECRET_PREV=$TG_WEBHOOK_SECRET TG_WEBHOOK_SECRET=$NEW
# Re-registra webhook no Telegram (handler de bootstrap chama setWebhook automaticamente).
# 24h depois:
fly secrets unset TG_WEBHOOK_SECRET_PREV
```

Alerta `auth.tg_webhook_reject > 0` fora da janela de rotação → investigar.

## Bot service token (`BOT_SERVICE_TOKEN`)

```bash
NEW=$(openssl rand -hex 32)
fly secrets set BOT_SERVICE_TOKEN_PREV=$BOT_SERVICE_TOKEN BOT_SERVICE_TOKEN=$NEW
# Atualizar consumidores (bot worker) com o novo. 24h depois:
fly secrets unset BOT_SERVICE_TOKEN_PREV
```

## Backup encryption key (age)

- Chave em `/etc/logfree/backup.key` (root only). Rotação anual ou sob suspeita.
- Snapshots novos usam chave nova; antigos mantidos até retenção (14 dias) expirar.

## Cookie session secret (`COOKIE_SECRET`)

- Rotação implica invalidar sessões. Planejar em janela de manutenção.
- Alternativa: implementar dual-secret no `itsdangerous` (signing + previous_signing).

## Telemetria (Logfire/Sentry)

- Rotação trimestral via console do provider.
- Verificar ingest contínuo por 1h antes de revogar a chave antiga.
