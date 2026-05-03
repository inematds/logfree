# OSRM degradação (P2/P3)

## Sintoma
- `osrm.fallback` > 20% das requisições em janela de 5min.
- `melhor_posto.response.distancia_metodo == "haversine_x_fator"` em todas as respostas (flag `distancia_aproximada=true` no payload).
- Cache hit-rate < 50% (P3).

## O que verificar
1. `/internal/metrics` → counter `event:osrm.fallback`, gauge `osrm.cache_size`.
2. Status da OSRM pública: `curl -sI https://router.project-osrm.org/route/v1/driving/-38.5,-12.97;-38.51,-12.99` — esperado 200.
3. Tempo de resposta (httpx logs internos): connect/read excedendo timeouts?
4. Circuit breaker aberto? Logs do app: `circuit_breaker.open`.

## Comandos seguros
**Forçar fallback temporariamente** (se OSRM público está caótico):
```bash
logfree flag-set --chave="ranking.osrm_enabled" --valor=false
```
Reverte com `--valor=true`.

**Subir OSRM self-hosted como mitigação:**
```bash
docker run -d --name osrm \
  -v /data/osrm:/data \
  -p 5000:5000 \
  osrm/osrm-backend osrm-routed --algorithm mld /data/regiao.osrm

# Atualiza env var:
fly secrets set OSRM_BASE_URL=https://osrm.internal:5000
```

## Quando escalar
- Fallback > 50% por > 1h e cidade está com cobertura fresca alta (recomendações ainda úteis, mas precisão de distância sofre — afeta ROI do produto).
- Circuit breaker reabrindo mais que 5x/hora.

## Pós
- Se fallback persistir, abrir issue para acelerar plano de OSRM self-hosted (PLAN.md item 12 menciona como gate antes de expandir além da cidade piloto).
