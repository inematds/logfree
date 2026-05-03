# Audit chain break (P1)

## Sintoma
- Evento `audit.chain_break` emitido pelo verificador.
- `/internal/ready` retorna 503 com `audit_quarantine` (modo aplicado em deploy.start).
- Aplicação rejeita mutações com mensagem "audit_quarantine" (lê continua liberado).

## É tampering ou bug do verificador?

**Indicadores de tampering** (real chain break):
- `evento_audit` tem `id` faltando no meio da sequência (gap).
- `hash_self` do registro `N` não confere com `SHA256(hash_prev||...)`.
- Linha de audit foi editada (`payload_json` mudou após gravação).
- Acesso DB direto recente fora do app (logs de SSH, atividade no console do Fly/Railway).

**Indicadores de verificador com bug** (não derruba prod, P2):
- Erro só aparece quando `verify_chain` chama I/O para a cópia append-only externa.
- Mudança recente em `app/core/audit.py` ou no formato de `payload_json`.
- Migration recente que mexeu em `evento_audit`.

## O que verificar
1. `logfree audit-verify` — confirma quebra e mostra `primeiro_id_quebrado`.
2. Compara linha problemática com cópia append-only externa (volume separado):
   ```bash
   sqlite3 /data/logfree/logfree.sqlite "SELECT * FROM evento_audit WHERE id=$ID"
   sqlite3 /audit-archive/last24h.sqlite "SELECT * FROM evento_audit WHERE id=$ID"
   ```
3. Se hashes da cópia externa batem entre si → tampering no DB primário.
4. Se hashes não batem em nenhum dos dois → verificador com bug.

## Caminho — tampering confirmado
- Manter aplicação em quarentena (read-only).
- Coletar evidência: dump do `evento_audit` antes de qualquer ação.
- Investigar acesso (logs SSH, logs do Fly/Railway, audit do bucket de backup).
- **NÃO unblock antes de investigar.**

## Caminho — bug do verificador
- Restaurar comportamento aplicando hot-fix no `app/core/audit.py`.
- Re-rodar `logfree audit-verify` após hot-fix.
- Se necessário rodar com aplicação fora do quarantine, usar `audit-unblock`:

```bash
export AUDIT_UNBLOCK_KEY=<chave-em-cofre>
logfree audit-unblock --approver=admin@logfree.tld --motivo="bug verificador X corrigido"
```
- O comando grava `evento_audit('audit_unblock')` re-âncorando a cadeia.

## Quando escalar
- Tampering confirmado → segurança/legal.
- Aplicação em quarentena por > 1h em horário de pico.
