# PLAN.md — LogFree MVP (Fase 1, semanas 1-6)

Consolidação crítica do plano em `doc/logfree.txt` + correções aceitas em `doc/critica-externa-01.md` + revisões da rodada 1 (Codex). Foco: tirar o projeto do estado "PRD" e entregar o motor de decisão executável da cidade piloto, sem IA.

## Escopo do MVP

1. **Núcleo de decisão**: endpoint `POST /melhor-posto` (FastAPI), **autenticado**: aceita apenas (a) sessão de usuário do admin (cookie) ou (b) bot service token interno (header `X-Bot-Token`, segredo em env). Anônimo é rejeitado com 401. Payload: `(lat, lng, combustivel, autonomia_restante_km, modo_abastecimento ∈ {parcial, completo}, volume_alvo_litros?, top_n ≤ 10)`. **`veiculo_perfil_id` NÃO é aceito do cliente** — o servidor resolve o perfil ativo via `usuario_id` (sessão) ou `telegram_id` (header `X-Bot-User-Telegram-Id` validado contra usuário ativo opt-in). Bind de cidade: `(lat, lng)` precisa cair em alguma `usuario_cidade` autorizada do usuário; senão 403. Bot só chama com usuários `opt_in_lgpd=true`; sem opt-in, retorna 403 com mensagem "execute /aceitar_termos". Caps: `top_n ≤ 10`, raio efetivo ≤ `min(raio_max_tipo_veiculo, 50 km)`, `autonomia_restante_km ∈ [1, 2000]`. A resposta inclui as `assumptions` usadas (k, autonomia, horizonte, fonte do consumo, schema_version). Postos inalcançáveis (`2*d > autonomia_restante_km * 0.9`) são excluídos com motivo. Rate limit: 30 req/min por `usuario_id`, 120 req/min por IP.
2. **Bot Telegram**: `/start` (cadastro de veículo + opt-in LGPD), envio de localização, `/melhor <combustível>` (pergunta `parcial`/`completo` + autonomia restante na primeira interação, depois lembra), `/postos` (lista crua, sem ranking de custo/km), `/aceitar_termos`, `/apagar_meus_dados`, `/exportar_meus_dados`, `/abasteci`. `/rota` **fora do MVP** (item 51). **Ingress**: webhook com **secret token** (`X-Telegram-Bot-Api-Secret-Token` header, segredo de 32 bytes em env var, validado antes do parsing); URL do webhook contém também segredo no path para defesa em profundidade. Se secret/path inválido → 401 imediato, sem parse. Modo `long_polling` é o fallback de dev/local. Updates aceitos só de `telegram_id` que existe em `usuario` com `ativo=1` e `opt_in_lgpd=1` (exceto `/start` e `/aceitar_termos`). **Versionamento de estado e callbacks**: tabela `bot_estado(usuario_id, chave, valor_json, schema_version, expira_em)`; toda `callback_data` codifica `v={schema_version}|...`. Parser do bot mantém compatibilidade backward por **1 release**; payload com `schema_version` desconhecido ou expirado responde "essa interação expirou, mande /melhor de novo" e descarta. Estado de conversação expira em 30 min de inatividade. Updates do Telegram que chegam atrasados (Telegram retem até 24h) e referenciam estado expirado caem nesse mesmo tratamento. Teste obrigatório: enfileirar update da versão N-1 e processar com código N (item 44).
3. **Painel web admin**: HTMX + Jinja, contas individuais por operador (item 22), listar/editar preços, **importar CSV de postos** + CRUD manual, mapa Leaflet read-only.
4. **Onboarding de cidade real**: a partir da semana 1 já trabalhamos com a cidade piloto definida (item 32). `cidade_demo` existe apenas em testes/CI, isolada por flag `is_demo=true`, nunca aparece em produção.
5. **Stack**: Python 3.12, FastAPI, python-telegram-bot v21, SQLite + SQLAlchemy 2 + Alembic, HTMX/Jinja, Leaflet, OSRM público (rota e matriz de distância). Single-process / single-worker no MVP (item 26). Deploy em Railway/Fly.

## Modelo de dados (DDL Alembic obrigatória na semana 1)

6. **Tabelas + colunas mínimas** (DDL alvo SQLite/Postgres-compatível; constraints inter-tabela e ENUM via CHECK + trigger no SQLite, validador app + teste em CI; `id` BIGINT auto; timestamps `TEXT ISO-8601 UTC` no SQLite):

   ```
   cidade(id, nome TEXT(120), uf TEXT(2), timezone TEXT(40) NOT NULL,
          bbox_min_lat REAL, bbox_max_lat REAL, bbox_min_lng REAL, bbox_max_lng REAL,
          preco_validade_horas_soft INT DEFAULT 24,
          preco_validade_horas_hard INT DEFAULT 72,
          stale_coverage_alert_pct INT DEFAULT 30,
          is_demo INT DEFAULT 0,
          CHECK(uf GLOB '[A-Z][A-Z]'),
          CHECK(bbox_min_lat BETWEEN -90 AND 90 AND bbox_max_lat BETWEEN -90 AND 90),
          CHECK(preco_validade_horas_soft <= preco_validade_horas_hard))

   posto(id, cidade_id, nome TEXT(160), bandeira TEXT(60), endereco TEXT(240),
         lat REAL, lng REAL, ativo INT DEFAULT 1,
         CHECK(lat BETWEEN -90 AND 90 AND lng BETWEEN -180 AND 180))
     -- TRIGGER posto_bbox_check: BEFORE INSERT/UPDATE garante (lat, lng)
     -- dentro da bbox da cidade; também rodado no validador Pydantic.

   combustivel(id, codigo TEXT(20) UNIQUE NOT NULL,
               CHECK(codigo IN ('gasolina_comum','gasolina_aditivada','etanol',
                                'diesel_s10','diesel_s500','gnv')))

   preco(id, posto_id, combustivel_id, valor_centavos INT NOT NULL,
         observed_at TEXT NOT NULL,
         recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
         registrado_por_usuario_id, fonte TEXT(16),
         import_batch_id NULL,
         schema_version INT DEFAULT 1,
         CHECK(valor_centavos > 0 AND valor_centavos < 5000000),  -- < R$ 50.000/L sanidade
         CHECK(fonte IN ('operador','bot','import_csv','ocr')),
         CHECK(observed_at <= recorded_at))
     -- validador app: observed_at não-futuro, não > hard*2 atrás (item 24).

   faixa_preco(id, cidade_id, combustivel_id, min_centavos INT, max_centavos INT,
               max_variacao_pct_24h INT DEFAULT 15,
               UNIQUE(cidade_id, combustivel_id),
               CHECK(min_centavos > 0 AND max_centavos > min_centavos))

   veiculo_tipo(id, nome TEXT(40), kml_default_p10 REAL, kml_default_p50 REAL, kml_default_p90 REAL,
                CHECK(kml_default_p10 <= kml_default_p50 AND kml_default_p50 <= kml_default_p90))
   veiculo_perfil(id, tipo_id, modelo TEXT(80), combustivel_id,
                  km_por_litro REAL, km_por_litro_p10 REAL, km_por_litro_p90 REAL,
                  CHECK(km_por_litro BETWEEN 0.5 AND 50))

   usuario(id, telegram_id INTEGER UNIQUE, nome TEXT(120),
           perfil TEXT(16) NOT NULL,
           email TEXT(160) UNIQUE, password_hash TEXT(200),
           ativo INT DEFAULT 1,
           opt_in_lgpd INT DEFAULT 0, opt_in_at TEXT,
           tombstoned INT DEFAULT 0, tombstoned_at TEXT,
           CHECK(perfil IN ('motorista','operador','admin')))
   usuario_veiculo(usuario_id, veiculo_perfil_id, ativo INT DEFAULT 1,
                   PRIMARY KEY(usuario_id, veiculo_perfil_id))
   usuario_cidade(usuario_id, cidade_id, papel TEXT(16),
                  PRIMARY KEY(usuario_id, cidade_id),
                  CHECK(papel IN ('motorista','operador','admin')))

   consulta(id, usuario_id NOT NULL, lat REAL, lng REAL, combustivel_id,
            autonomia_km REAL, modo TEXT(10), volume_alvo_l REAL,
            top_n INT, feita_em TEXT NOT NULL,
            top_n_resultado_json TEXT, assumptions_json TEXT,
            schema_version INT DEFAULT 1,
            anonimizada INT DEFAULT 0, lat_anon REAL, lng_anon REAL,
            CHECK(modo IN ('parcial','completo')),
            CHECK(length(top_n_resultado_json) < 16384),
            CHECK(length(assumptions_json) < 4096))
     -- 90 dias retenção raw; depois job zera lat/lng/json e marca anonimizada=1.

   abastecimento_relatado(id, usuario_id, consulta_id NULL, posto_id, combustivel_id,
                          valor_centavos INT, volume_l REAL,
                          foto_storage_key TEXT(120) NULL,  -- key local/S3, nunca URL livre
                          criado_em TEXT NOT NULL,
                          CHECK(valor_centavos > 0 AND volume_l BETWEEN 0.5 AND 1000))

   import_batch(id, cidade_id, idempotency_key TEXT(80) UNIQUE NOT NULL,
                criado_por_usuario_id, criado_em TEXT, status TEXT(16),
                total_linhas INT, linhas_ok INT, linhas_erro INT,
                erros_json TEXT,
                CHECK(status IN ('staging','commit','rollback','erro')))

   tombstone_usuario(usuario_id PRIMARY KEY, criado_em TEXT NOT NULL,
                     motivo TEXT(40))  -- bloqueia INSERTs futuros (trigger + app check)

   rate_limit(chave TEXT PRIMARY KEY, janela_ini TEXT, contador INT)
     -- usado por API/bot; alternativamente em Redis na fase 2

   evento_audit(id, usuario_id, acao TEXT(40), recurso TEXT(40), recurso_id,
                payload_json TEXT, criado_em TEXT NOT NULL,
                hash_prev TEXT(64), hash_self TEXT(64),
                CHECK(length(payload_json) < 8192))
     -- hash_self = SHA256(hash_prev || id || criado_em || acao || payload); cadeia
     -- verificada por job diário; cópia rotacionada em append-only fora do DB (item 41a).
   ```

7. **Seleção do preço atual** (regra única e documentada): por `(posto_id, combustivel_id)` retorna a linha com maior `observed_at` válido (≤ `now()`, dentro de `preco_validade_horas_hard`); empate por maior `recorded_at`. View materializada `preco_atual` no Postgres futuro.
8. **Índices**: `preco(posto_id, combustivel_id, observed_at DESC)`, `posto(cidade_id, ativo)`, `consulta(feita_em)`, `usuario(telegram_id) UNIQUE`.
9. **Privacidade e LGPD**: `consulta.lat/lng` brutos retidos 90 dias; job noturno calcula `lat_anon/lng_anon` com k-anonimato (snap em grid de 500 m, descarta se < 5 consultas no bucket) e zera os brutos + `top_n_resultado_json` (deixando só `posto_id` + `custo_centavos`). Opt-in explícito no `/start` ou `/aceitar_termos` (grava `opt_in_at`); sem opt-in → bot recusa `/melhor`. **Apagamento (`/apagar_meus_dados`)** é uma transação durável e crash-safe:
   1. drena fila assíncrona de `consulta` filtrando o `usuario_id` antes de qualquer escrita;
   2. transação única: insere em `tombstone_usuario`, zera PII em `usuario` (nome, email, password_hash, telegram_id → NULL), apaga `usuario_veiculo`, anonimiza `consulta` do usuário (mesma rotina do job), apaga `abastecimento_relatado.foto_storage_key` + arquivo no storage, registra `evento_audit('user_delete')`;
   3. bot/API checam `tombstone_usuario` e `usuario.tombstoned` antes de qualquer INSERT futuro; trigger SQL bloqueia também.
   **Restore**: dump nunca volta sozinho. Procedimento documentado: após `restore`, rodar `replay_tombstones.py` que relê `tombstone_usuario` (parte do backup) e reaplica anonimização; backup que ainda contenha PII de usuário tombado é purgado em ≤ 24h. Backups encriptados em rest, retenção 14 dias, sem cópias fora desse ciclo.
   **Export**: `/exportar_meus_dados` gera JSON assinado em ≤ 24h, com tudo do usuário (consulta, abastecimento, perfil); link tem TTL 72h e exige confirmação no Telegram. **Acesso a `consulta.lat/lng` brutos**: só `perfil=admin`, sempre via rota dedicada `GET /admin/consulta/{id}/raw` que escreve `evento_audit('raw_location_read')` antes de devolver.

## Algoritmo de ranking (decisão fechada)

10. **Função objetivo, modo `completo`** (encher tanque, horizonte longo):
    `custo_por_km(p) = preco_C(p)/k_efetivo + (2*d_road * preco_C(p)/k_efetivo) / horizonte_km`
    onde `horizonte_km = max(autonomia_restante_km, autonomia_pos_abastecimento_estimada)` ou parâmetro `cidade.horizonte_default_km` se não houver dado de tanque.
11. **Função objetivo, modo `parcial`** (V litros agora):
    `custo_por_litro(p, V) = preco_C(p) + (2*d_road/k_efetivo) * preco_C(p) / V`.
    `V` (volume_alvo_litros) é obrigatório nesse modo; se ausente, API rejeita com 422.
12. **Distância de desvio `d_road`**: OSRM Table API em batch para os candidatos pré-filtrados (Haversine ≤ raio_máximo, **fanout máximo: 30 postos por requisição**). Cache LRU `(posto_id, hash_lat_lng_arredondado_50m)` com **teto de 50 MB** e TTL 24h; eviction LRU + cleanup diário. Cliente HTTP com `connect_timeout=1s`, `read_timeout=2s`, `total_timeout=3s`, retries=0. **Circuit breaker**: 3 falhas/timeouts em janela de 30s → abre por 60s; durante aberto, vai direto pro fallback. Fallback `Haversine × cidade.fator_road_default` (default 1.35) com flag `distancia_aproximada=true`. Métricas: `osrm.latency_ms`, `osrm.fallback` (alerta se > 20% das requisições em 5 min, item 41b). Plano de saída do OSRM público antes de expandir além da cidade piloto (item 51) — opções: container OSRM self-hosted no mesmo deploy ou contrato pago; decisão na semana 6.
13. **Filtros antes do ranking**: posto inativo, fora da `bbox`, sem preço fresco (ver item 14), `2*d > autonomia_restante_km*0.9` (inalcançável), `d > raio_maximo` por tipo de veículo, ou sem preço para o combustível pedido.
14. **Política de frescor (soft/hard)**:
    - `observed_at ≥ now() - soft` (default 24h) → preço **fresco**, entra normal.
    - `soft < observed_at ≤ hard` (24–72h) → preço **stale**, entra no ranking com flag `preco_envelhecido=true` e penalidade configurável (default 0%); UI/bot mostra ⚠ e idade.
    - `observed_at > hard` → **excluído**.
    - Se cobertura fresca da cidade < `stale_coverage_alert_pct` (default 30% dos postos ativos), dispara alerta no canal de operadores e a resposta inclui `cobertura_baixa=true`.
    - Se zero postos elegíveis após hard cutoff, bot recomenda **não recomendar** ("dados insuficientes hoje, abasteça pelo critério de proximidade") e registra evento.
15. **Incerteza de consumo**: ranking calcula `custo_p50`, `custo_p10`, `custo_p90` usando bandas de `km_por_litro`. Postos cujo `custo_p10` se sobrepõe ao melhor `custo_p90` são apresentados como **empate técnico** (mesmo bullet "≈"); desempate só ocorre fora da banda de sobreposição. Empate restante: preço bruto menor, depois `observed_at` mais recente.
16. **Resposta sempre retorna `assumptions`**: `{k_usado, fonte_k ∈ (perfil, categoria_p50, default), horizonte_km, distancia_metodo ∈ (osrm, haversine_x_fator), validade_aplicada}`.

## Edge cases e unhappy paths

17. **Sem postos elegíveis** (todos excluídos): bot diz qual filtro eliminou todos (expirados / fora de raio / inalcançáveis) e oferece relaxar raio ou aumentar autonomia informada.
18. **Localização ausente ou inválida** (fora da bbox da cidade do usuário): bot pede localização novamente; API 422.
19. **Veículo não cadastrado**: `/melhor` força `/start`.
20. **`km_por_litro` ausente para o perfil**: usa `kml_default_p50` da categoria com flag `consumo_estimado=true` e bandas mais largas (item 15).
21. **Preço zero/negativo/fora da faixa** (item 30): rejeitado no admin com mensagem; nunca entra em `preco`.
22. **Posto sem coordenadas ou fora da bbox**: bloqueio na criação (constraint do banco).
23. **Combustível inexistente**: API valida contra `combustivel.codigo`; bot lista válidos.
24. **`observed_at` no futuro ou anterior a `now() - hard*2`**: rejeitado; admin precisa confirmar manualmente com motivo (registrado em `evento_audit`).
25. **Inalcançabilidade**: `2*d_road > 0.9 * autonomia_restante_km` exclui o posto com motivo "fora da autonomia restante".

## Concorrência, timing e processo

26. **Modelo de processo e single-owner lease**: 1 processo Python, 1 worker uvicorn no MVP. Bot, API, jobs (anonimização, alertas de cobertura, backup, restore-test, verificação da cadeia de audit) rodam **no mesmo processo** via APScheduler in-process. Cron externo proibido. Plataforma configurada com `replicas=1`, `maxUnavailable=1`, **stop-the-world deploy** (não rolling) e máquina pinada (Fly `auto_stop=false`, single machine; Railway: 1 instância, sem horizontal scaling). Volume persistente único e dedicado por ambiente. **Lease de owner em três camadas**:
    1. *Plataforma*: hard-cap de 1 instância via config (review-gate no PR de infra).
    2. *Filesystem*: `flock` exclusivo em `/data/logfree/owner.lock` no volume montado (defesa local contra duplo-start na mesma máquina).
    3. *Banco*: tabela `owner_lease(id=1 PRIMARY KEY, owner_uuid, host, started_at, heartbeat_at)` com lease de 30s renovado a cada 10s; ao subir, processo faz `INSERT ... ON CONFLICT(id) DO UPDATE SET ... WHERE heartbeat_at < now()-30s`. Se não conseguir o lease, aborta com erro claro. Se perder o heartbeat em runtime (clock skew, pause), processo derruba o servidor (panic-and-restart) em vez de continuar servindo sem certeza de exclusividade. Healthcheck `/internal/ready` só responde 200 quando: lease seguro **E** `alembic current = head` **E** smoke do `core.ranking` ok **E** verificação da cadeia de audit ok. Plataforma só direciona tráfego ao receber 200 (item 31a).
27. **SQLite**: `journal_mode=WAL`, **`synchronous=FULL`** por default no MVP (durabilidade > throughput nesse volume), `busy_timeout=5000ms`, retry exponencial em `OperationalError` (3 tentativas, backoff 50/200/800ms), `foreign_keys=ON`. Escritas críticas (tombstone, `evento_audit`, `import_batch.commit`, `usuario.role_grant`, anonimização) usam `BEGIN IMMEDIATE` + `PRAGMA wal_checkpoint(TRUNCATE)` no fim e só retornam ao cliente após `COMMIT` durável + `fsync` do WAL **E** verificação pós-commit (re-SELECT confirma estado). Para `/apagar_meus_dados` o ack ao usuário acontece **somente** depois desse fluxo concluir.
28. **Escrita de `consulta`**: fila assíncrona em memória (`asyncio.Queue`, max 1000) com flush em batch (≤200ms ou 50 itens), drenada antes do shutdown via lifespan handler (timeout 10s). **Drenagem inclui filtro por `tombstone_usuario`** (item 9). Em crash, perde-se no máximo o último batch — aceitável (log analítico). Backpressure: se fila cheia, descarta com contador em métrica `consulta_queue_drop` (não bloqueia o request do motorista).
29. **Fuso horário**: tudo UTC no banco. Apresentação converte para `cidade.timezone`. Jobs APScheduler rodam em UTC e calculam horário local por cidade.
30. **Faixas de preço configuráveis** (`faixa_preco`): por cidade × combustível, com `min`/`max` e `max_variacao_pct_24h` (default 15%). Admin pode override gravando `evento_audit`. Sem fallback hard-coded global.
30a. **Idempotência de escrita**:
   - Importação CSV: cliente envia header `Idempotency-Key`; servidor cria `import_batch(idempotency_key)` com UNIQUE; se a mesma key chega duas vezes, retorna o resultado da primeira sem reprocessar. Pipeline: upload → staging em `import_batch_linha` (tabela auxiliar) → validação completa → commit em transação única (todas as linhas ou rollback) → `status='commit'`. Postos com chave natural `(cidade_id, lat_arredondado_5m, lng_arredondado_5m, nome_normalizado)` evitam duplicação entre batches.
   - Submissão de preço (admin/bot): cliente envia `Idempotency-Key`; servidor armazena em `rate_limit` ou tabela dedicada `idempotency_key(chave, response_json, criado_em)` com TTL 24h; replay devolve resposta gravada. INSERT em `preco` é transacional com a gravação da chave.
30b. **Validação de bounds (Pydantic + DB)**: `lat ∈ [-90,90]`, `lng ∈ [-180,180]` com 6 casas; `autonomia_restante_km ∈ [1, 2000]`; `volume_alvo_litros ∈ [0.5, 1000]`; `top_n ∈ [1, 10]`; `km_por_litro ∈ [0.5, 50]`; strings com `max_length` declarado em todo modelo (nome ≤120, endereço ≤240, modelo ≤80, motivo ≤40); `valor_centavos > 0 e < 5_000_000`. Photo upload: `multipart/form-data` para `POST /admin/foto`, validação MIME (image/jpeg|png), limite 5 MB, antivírus opcional, salvo em storage controlado e referenciado por `foto_storage_key`; URLs externas **nunca** aceitas.

## Integridade e operações

31. Migrations Alembic obrigatórias desde o commit 1; CI roda `alembic upgrade head` em DB vazio + smoke + teste de constraints (item 6) verificando rejeição de `(lat,lng)` fora da bbox, `uf` inválido, `valor_centavos<=0`, `observed_at>recorded_at`, `perfil` fora do enum. Schema editado à mão = build quebra.
31a. **Contrato de migração e rollback** (toda mudança de schema/código segue este pipeline, sem exceção):
   - Cada migração tem `upgrade()` **e** `downgrade()` testáveis. CI roda ciclo `upgrade→downgrade→upgrade` num dump real anonimizado e compara hash de schema; falha bloqueia o merge.
   - **Compatibilidade backward de 1 release**: o código novo precisa ler dados gravados pela versão imediatamente anterior; o código antigo precisa ler dados gravados pela versão nova até o release seguinte (regra "expand-then-contract"). Mudanças destrutivas dividem em 2 releases (release N adiciona, N+1 remove após verificação).
   - Migrações grandes/destrutivas marcadas `requires_restore_only=True` em comentário no arquivo Alembic; deploy bloqueia se essa flag estiver presente sem aprovação manual + janela de manutenção declarada.
   - **Pré-deploy automático**: snapshot encriptado do SQLite (item 35) com tag `pre-deploy-{git_sha}`, retido 30 dias, antes de aplicar migração. Restore desse snapshot é o caminho oficial de rollback de dados.
   - **Roll-forward por default**: rollback de schema usa `downgrade()`; se downgrade não for seguro (flag), procedimento documentado é restore do snapshot pré-deploy + redeploy do `git_sha` anterior.
   - **Health gate de startup**: `/internal/ready` retorna 200 só após: lease ok (item 26), `alembic current = head`, smoke `core.ranking` (10 casos canônicos), verificação da cadeia de audit (item 41a). Plataforma só direciona tráfego com 200; webhooks Telegram são re-registrados pela aplicação **após** ready=200.
   - **Runbook de rollback** em `runbook/rollback.md`: quando reverter via redeploy, quando fazer restore, como reaplicar tombstones (item 9), como verificar audit chain pós-restore.
32. **Cidade piloto + operação de preços** (decisão **trava** semana 1): nome da cidade, frota piloto, **2 operadores nomeados** com responsabilidade de atualização, **cadência alvo**: 80% dos postos ativos atualizados a cada 24h (SLO), aferida no painel. Se não houver decisão até fim da semana 1, projeto pausa — não roda contra `cidade_demo` em produção.
33. **Onboarding de postos**: importação CSV idempotente (item 30a) com colunas: `nome, bandeira, endereco, lat, lng, combustiveis_disponiveis`. CRUD manual no admin com validação Pydantic. Geocoding via Nominatim com cache local + fallback "clicar no mapa".
34. Seed idempotente (`UPSERT` por chave natural). `cidade_demo` só em CI/dev.
35. Backup: dump SQLite diário **encriptado** (libsodium/age, chave em env separada) pro storage do Railway/Fly; retenção 14 dias; cópia secundária em bucket diferente. Restore testado 1×/semana via job APScheduler que cria DB temporário, roda smoke, descarta. **Procedimento de restore em produção** documentado em `runbook/restore.md`: parar processo (lock), restore, rodar `replay_tombstones.py` (item 9), verificar cadeia de audit (item 41a), só então liberar tráfego.
36. Toda escrita de `preco`, `posto`, `faixa_preco`, `import_batch`, `usuario`, `usuario_cidade`, `consulta` (delete/anonimize), `abastecimento_relatado` registra evento em `evento_audit` (item 41a).
36a. **Rotação de segredos** (`runbook/secrets.md`): segredos com janela de duplo-aceite onde aplicável, alertas para falhas inesperadas durante rotação:
   - **Telegram webhook secret**: env vars `TG_WEBHOOK_SECRET` e `TG_WEBHOOK_SECRET_PREV`; ambos aceitos no header durante a janela de rotação. Procedimento: gerar novo segredo, deploy com `PREV=antigo` e atual=novo, re-registrar webhook no Telegram com o novo, observar 24h, deploy removendo `PREV`. Alerta se `auth.tg_webhook_reject` > 0 fora de janela.
   - **Bot service token** (`X-Bot-Token` interno): mesma estrutura `BOT_SERVICE_TOKEN` + `BOT_SERVICE_TOKEN_PREV`.
   - **Backup encryption key** (libsodium/age): chave nova adicionada ao keyring; backups novos com chave nova; chaves antigas mantidas até retenção (14 dias) expirar; rotação automática anual + sob suspeita de leak.
   - **Telemetria** (Logfire/Sentry): rotação trimestral; chaves antigas revogadas após confirmação de ingest contínuo.
   - **Cookie session secret**: rotação requer invalidar todas sessões; planejar em janela de manutenção ou usar `signing_secret + previous_signing_secret` (lib precisa suportar).
   - Cada rotação grava `evento_audit('secret_rotation')` com nome do segredo e responsável; alerta P2 se `auth.*reject` subir > baseline durante janela.
36b. **Capacidade e limites de armazenamento** (`runbook/capacity.md`, alertas item 41b):
   - **Volume de dados** (Railway/Fly): provisão inicial 10 GB; alerta em 80% / 90% (item 41b); cleanup automático: snapshots > 30 dias removidos; backups > 14 dias removidos; logs locais rotacionados.
   - **WAL do SQLite**: `PRAGMA wal_autocheckpoint=1000`; job a cada 5 min força `wal_checkpoint(PASSIVE)`; alerta se WAL > 200 MB (sintoma de leitor longo).
   - **Cache OSRM**: teto 50 MB (item 12), eviction LRU + cleanup diário; alerta se hit-rate < 50%.
   - **Audit append-only copy**: rotação diária com retenção 365 dias; quota 5 GB; arquivamento para storage frio quando > 90 dias.
   - **Backups**: retenção 14 dias online + 30 dias snapshots pre-deploy; total ≤ 5 GB.
   - **Fotos de abastecimento** (`foto_storage_key`): teto 1 GB; cleanup de fotos órfãs (sem `abastecimento_relatado`) após 7 dias; redimensionamento máximo 1024px no upload para limitar tamanho.
   - **`consulta` raw**: retenção 90 dias (item 9); volume estimado < 100 MB no MVP.
   - **`evento_audit`**: append-only sem cleanup; estimativa < 50 MB/ano no MVP; revisar na fase 2.
   - Disk-full behavior: aplicação detecta < 5% livre, recusa novas escritas com 503 "manutenção", grava `evento_audit('disk_full')`, alerta P1.
36c. **Escape e renderização** (defesa em profundidade contra XSS/injeção):
   - Jinja com `autoescape=True` em todo template; nada de `|safe` em conteúdo controlado por operador.
   - Mensagens Telegram: usar `parse_mode=None` por default; quando precisar formatação, escapar via `telegram.helpers.escape_markdown` (v2).
   - Logs: campos de operador (`nome`, `endereco`, `bandeira`) passam por scrubber de quebra de linha + truncate.
   - **Export CSV/XLSX** (export do usuário/admin): prefixar células que começam com `=`, `+`, `-`, `@`, TAB, CR com aspa simples para neutralizar fórmulas (CSV injection / CWE-1236).
   - Headers de segurança no admin: `Content-Security-Policy: default-src 'self'`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `Strict-Transport-Security` em produção.

## Auth e autorização

37. **Contas individuais por operador desde semana 1**: email + senha (Argon2id, parâmetros OWASP 2024), sessão via cookie HTTP-only + Secure + SameSite=Lax, CSRF token em todo form HTMX (header `HX-CSRF-Token`), `Cookie-Prefix=__Host-`, sem OAuth no MVP. Sessão expira em 12h, sliding refresh. Admin cria operador via CLI seed; revogação = `usuario.ativo=false` invalida sessão na próxima requisição.
38. **Autorização — matriz default-deny** aplicada via *dependência FastAPI única* `require_authz(resource, action)` em **todas** as rotas admin (não há rota admin sem ela; teste de CI varre o router e falha se faltar):

   | Recurso/ação                 | motorista | operador (cidade própria) | operador (outra cidade) | admin |
   |------------------------------|:---------:|:-------------------------:|:-----------------------:|:-----:|
   | `posto.create/update/delete` |  ✗        | ✓                         | ✗                       | ✓     |
   | `preco.create/update`        |  ✗        | ✓                         | ✗                       | ✓     |
   | `import_batch.create`        |  ✗        | ✓                         | ✗                       | ✓     |
   | `faixa_preco.override`       |  ✗        | ✗                         | ✗                       | ✓     |
   | `usuario.invite/role_grant`  |  ✗        | ✗                         | ✗                       | ✓     |
   | `consulta.read_raw_location` |  ✗        | ✗                         | ✗                       | ✓     |
   | `data_export/delete (próprio)`| ✓        | ✓                         | ✓                       | ✓     |
   | `audit.read`                 |  ✗        | leitura cidade            | ✗                       | ✓     |

   Operador é vinculado à cidade via `usuario_cidade(papel='operador')`. Toda mutação verifica `recurso.cidade_id ∈ usuario_cidades(usuario, papel)`. Cross-city e non-admin mutations cobertos por testes (item 44).
39. **Rate limits — token bucket in-process** (não usar `rate_limit` no DB no caminho hot do MVP; tabela continua só para anti-replay/idempotência):
    - admin login: 10 tentativas/min/IP, 5/min/email; após 5 falhas → bloqueio temporário 15 min (estado em memória + persistido por usuário em `usuario.bloqueado_ate` para sobreviver a restart de processo);
    - `/melhor-posto`: 30/min por `usuario_id`, 120/min por IP;
    - `/exportar_meus_dados`: 1/hora/usuário (estado durável em `usuario.export_ultimo_em`);
    - import CSV: 5/hora/operador (estado durável em `import_batch`);
    - bot Telegram: 30/min por `telegram_id` na camada de handler.
    Buckets em RAM custam ~0 e não geram lock no SQLite. Limites duráveis (login bloqueado, export, import) ficam em colunas dedicadas, não na hot path. Métrica `rate_limit.deny` por bucket/recurso; alerta em pico súbito (possível abuse).

## Observabilidade

40. Logfire (ou stdlib JSON + Sentry free) com **schema de telemetria allowlisted**: cada evento é um `dataclass` versionado (`schema_version`) e só campos declarados saem. Bibliotecas configuradas com scrubber para `Authorization`, `Cookie`, `X-Bot-Token`, `password`, `password_hash`, `email`, `telegram_id`, `nome`, `endereco`, `foto_storage_key`. **Coordenadas nunca saem em precisão raw**: `lat/lng` sempre arredondados a 3 casas (~110 m) ou hash SHA-256 truncado quando o evento é por usuário. Payload bruto de request/response **proibido**; logger usa só os campos do schema. Retenção do destino (Logfire/Sentry) configurada para ≤ 30 dias; acesso ao painel restrito a admins do projeto.
    Eventos obrigatórios (todos com `schema_version`):
    - `melhor_posto.request` — `usuario_id_hash`, `cidade_id`, `combustivel`, `modo`, `top_n`, `lat3`, `lng3`, `assumptions_keys`
    - `melhor_posto.response` — `usuario_id_hash`, `posto_ids`, `latencia_ms`, `distancia_metodo`, `cobertura_baixa`
    - `melhor_posto.no_result` — `motivo_agregado`
    - `preco.staleness_alert` — `cidade_id`, `pct_fresco`
    - `osrm.fallback` — `cidade_id`, `motivo`
    - `auth.login` — `email_hash`, `ip`, `sucesso`, `motivo_falha`
    - `authz.deny` — `usuario_id_hash`, `recurso`, `acao`, `cidade_alvo`
    - `data.export_request` / `data.delete_request`
    - `audit.chain_break` (verificação diária do hash chain)
41. Métricas no painel admin: cobertura fresca por cidade, latência p50/p95 do endpoint, % consultas sem resultado, idade média do preço usado, contadores de `consulta_queue_drop`, `osrm.fallback`, `authz.deny`, `rate_limit.deny`, idade do snapshot de backup, idade do último restore-test ok, idade da última verificação de audit chain, tamanho do WAL, uso de disco, tamanho do cache OSRM.
41a. **Audit trail** (`evento_audit` + cadeia hash): registra **login_success, login_failure, session_create, session_revoke, role_grant, role_revoke, posto.* , preco.create, faixa_preco.override, import_batch.* , data_export, data_delete, raw_location_read, foto_upload, foto_delete, authz.deny, csv.import.commit/rollback, owner_lease.acquire/lose, deploy.start/ready, migration.upgrade/downgrade, restore.run, snapshot.create**. `hash_self = SHA256(hash_prev || id || criado_em || acao || recurso || recurso_id || payload_json)`. Job diário verifica a cadeia inteira. Cópia rotacionada diariamente para storage append-only fora do DB principal (volume separado, write-only token); admin **não** pode editar/deletar `evento_audit` pela UI; só rota `audit.read` existe. **Modos do verificador (break-glass)**:
    - *cadeia íntegra* → tudo normal.
    - *cadeia quebrada por tampering*: aplicação entra em **modo read-only** (rejeita mutações com 503 + mensagem "audit_quarantine"); leitura/exportação seguem; alerta P1 dispara.
    - *verificador falha por erro próprio* (storage append-only inacessível, bug no checker, clock skew): evento `audit.verifier_error` distinto de `audit.chain_break`; aplicação **continua servindo**, mas alerta P2 dispara e healthcheck `/internal/ready` mantém 200 — não derrubamos produção por bug de verificador.
    - *unblock de emergência*: comando CLI `audit-unblock --approver=<email_admin> --motivo=<texto>` exige confirmação dupla (env var `AUDIT_UNBLOCK_KEY` + login admin), grava `evento_audit('audit_unblock')` com motivo, e re-ancora a cadeia gravando o último hash íntegro como novo ponto de partida (com nota explícita). Runbook em `runbook/audit-chain-break.md` lista passos para distinguir tampering de bug.
41b. **SLOs, alertas e runbooks** (todos os alertas têm `owner`, `severity`, `pager`/`canal`, runbook em `runbook/<slug>.md`):

   | Sinal                                            | Threshold                       | Severity | Runbook                       |
   |--------------------------------------------------|---------------------------------|----------|-------------------------------|
   | `/melhor-posto` p95                              | > 800 ms por 5 min              | P2       | runbook/latency.md            |
   | `/melhor-posto` 5xx                              | > 1% por 5 min                  | P1       | runbook/api-errors.md         |
   | `osrm.fallback`                                  | > 20% req em 5 min              | P2       | runbook/osrm.md               |
   | `consulta_queue_drop`                            | > 0 em 5 min                    | P3       | runbook/queue-drop.md         |
   | `preco.staleness_alert` (cobertura fresca)       | < SLO da cidade por 6h          | P2       | runbook/price-staleness.md    |
   | `melhor_posto.no_result`                         | > 20% por 30 min                | P2       | runbook/no-result.md          |
   | Backup diário falha                              | 1 ocorrência                    | P1       | runbook/backup.md             |
   | Restore-test semanal falha                       | 1 ocorrência                    | P1       | runbook/restore.md            |
   | Anonimização atrasada                            | > 6h após `now()-90d`           | P2       | runbook/anonymization.md      |
   | `audit.chain_break`                              | 1 ocorrência                    | P1       | runbook/audit-chain-break.md  |
   | `audit.verifier_error`                           | 1 ocorrência                    | P2       | runbook/audit-verifier.md     |
   | DB busy/lock retry                               | > 50/min                        | P2       | runbook/db-lock.md            |
   | `owner_lease.lose`                               | 1 ocorrência                    | P1       | runbook/owner-lease.md        |
   | `authz.deny` em rota nunca-deve-falhar           | > 0                             | P2       | runbook/authz-anomaly.md      |
   | Disco do volume                                  | > 80% / > 90%                   | P2/P1    | runbook/disk.md               |
   | WAL size                                         | > 200 MB                        | P2       | runbook/wal-bloat.md          |
   | Cache OSRM                                       | > 50 MB ou hit-rate < 50%       | P3       | runbook/osrm.md               |

   Pager: Telegram canal interno + email do owner. P1 acorda; P2 horário comercial; P3 backlog. Toda runbook segue template: *sintoma → o que verificar → comandos seguros → quando escalar → rollback*.
41c. **Feature flags e canários** (tabela `feature_flag(chave, escopo ENUM(global, cidade, usuario), alvo, valor_json, ativo, atualizado_em, atualizado_por)`):
    - flags previstos: `ranking.osrm_enabled`, `ranking.uncertainty_bands_enabled`, `ranking.stale_prices_enabled`, `ranking.formula_version`, `bot.command_enabled.<cmd>`, `admin.csv_import_enabled`, `kill_switch.global` (desliga `/melhor-posto` retornando 503 com mensagem amigável).
    - escopo permite: liberar feature por cidade, por `usuario_id` (canário), ou globalmente; default-off para mudanças comportamentais.
    - **Shadow/dark mode**: nova versão de fórmula roda junto da antiga em mesmo request (`ranking.formula_version='shadow'` retorna ambas em log), comparada offline antes do switch.
    - **Canary gate**: rollout de mudança de ranking exige (a) shadow ≥ 7 dias na cidade piloto, (b) ativação em 1 usuário canário ≥ 48h, (c) métricas de regressão (latência, no_result, cobertura) dentro do envelope, antes de habilitar para a cidade.
    - **Kill switches** acionáveis em < 1 min via CLI `flag set kill_switch.global true` ou via UI admin; ambas gravam `evento_audit('flag_change')`.

## Testes

42. Unitários do `core/ranking`: desvio zero, autonomia infinita, preço expirado (soft/hard), posto inalcançável, modo parcial sem V (deve falhar), banda de incerteza sobreposta = empate técnico, ordenação estável.
43. Property-based (Hypothesis): custo nunca decresce com `d` ↑ ceteris paribus; ranking invariante a permutação de entrada; `assumptions` sempre presente; bounds (item 30b) nunca aceitam valor fora da faixa.
44. **Integração e segurança**: API + DB real (SQLite tmpfs), bot mockado, admin via httpx + cookies. Casos obrigatórios:
    - `/melhor-posto` sem auth → 401; com bot token errado → 401; com `telegram_id` de usuário sem opt-in → 403.
    - operador da cidade A tentando editar posto/preço da cidade B → 403, `authz.deny` registrado.
    - motorista tentando rota admin → 403.
    - CSRF ausente em form admin → 403.
    - `Idempotency-Key` repetida → mesma resposta, sem efeito colateral duplicado.
    - import CSV com 1 linha inválida em 100 → rollback total, batch fica `status='rollback'`.
    - `/apagar_meus_dados` → tombstone gravado, fila de `consulta` purgada, INSERT futuro do mesmo `telegram_id` rejeitado.
    - restore: dump de antes de `delete` + `replay_tombstones.py` → PII some.
    - webhook do Telegram com secret token errado → 401; sem secret e modo polling on → ok.
    - cadeia de `evento_audit` quebrada manualmente → job de verificação detecta e dispara `audit.chain_break`.
    - input fuzz: lat/lng/autonomia/volume/top_n fora do bound, string > max_length, JSON > limite → 422.
    - upload de foto não-imagem ou > 5MB → 415/413.
45. Cobertura: `app/core` ≥ 90%, total ≥ 75%. CI bloqueia merge se cair.
45a. **CI varre o router** garantindo que toda rota com mutação tem `require_authz`; falha caso contrário (test `test_authz_coverage`).

## Plano de execução revisado

46. **Semana 1**: trava decisões de cidade + operadores (item 32); scaffold (`app/api`, `app/bot`, `app/core`, `app/db`, `app/web`, `tests`, `alembic`); migration inicial completa (item 6); seed `cidade_demo` para CI; importação inicial dos postos da cidade piloto via CSV; CI verde com lint + testes.
47. **Semana 2**: `app/core/ranking.py` (modos completo/parcial, OSRM Table com cache e fallback, bandas de incerteza, política soft/hard); endpoint `/melhor-posto` com Pydantic; testes unitários ≥ 90% no módulo.
48. **Semana 3**: bot Telegram (`/start` com opt-in LGPD, `/melhor`, `/postos`, localização, `/apagar_meus_dados`); fila assíncrona de `consulta`; logging estruturado.
49. **Semana 4**: admin HTMX (auth individual, CRUD posto + preço com validações + override de faixa, importação CSV, mapa Leaflet read-only, painel de cobertura/SLO).
50. **Semana 5**: deploy single-worker (Railway/Fly), piloto com 3–5 motoristas reais, coleta de `abastecimento_relatado` via bot (`/abasteci posto valor litros foto?`), comparação contra baseline.
51. **Semana 6**: pós-piloto — relatório de ROI vs. baseline (item 53), decisão go/no-go pra Supabase (Postgres + multi-worker), backlog fase 2 incluindo `/rota` (com contrato de detour-delta real), OCR de bomba, multi-cidade.

## Critérios de "pronto" do MVP

52. `/melhor-posto` p95 < 500 ms com OSRM Table + cache quente, < 200 ms no fallback Haversine.
53. **ROI mensurável**: ≥ 30 `abastecimento_relatado` na semana 5, comparados contra **2 baselines fixos** — (a) posto mais próximo elegível e (b) posto mais barato bruto (sem desvio). Recomendação do LogFree deve ter custo médio ponderado por volume **≤** o melhor dos dois baselines, com IC 80% (bootstrap). Não-piora é o gate; superioridade é meta.
54. 100% das respostas exibem timestamp do preço, idade em horas, e flags (`distancia_aproximada`, `consumo_estimado`, `preco_envelhecido`, `cobertura_baixa`) quando aplicáveis.
55. Cobertura `app/core` ≥ 90%, total ≥ 75%; CI verde.
56. ≥ 50 consultas reais em `consulta` na semana 5; SLO de cobertura fresca ≥ 80% atingido em ≥ 5 dos 7 dias.
57. Job de anonimização de `consulta` rodando em produção (item 9).

## Changelog

### Round 1 (Codex senior-engineer review) — 2026-05-02

**High — aceito integralmente:**
- *Contrato econômico do ranking.* Aceito. Itens 1, 10, 11, 13, 16: API exige `autonomia_restante_km` + `modo_abastecimento`, exclui inalcançáveis, separa modos `completo`/`parcial`, e devolve `assumptions` com cada resposta.
- *Caminho de `cidade_demo` → piloto real.* Aceito. Itens 4, 32, 33: cidade piloto + 2 operadores nomeados + SLO de cadência viram bloqueador da semana 1; importação CSV + CRUD de postos entram no admin; `cidade_demo` só em CI.
- *Frescor all-or-nothing.* Aceito. Item 14: política soft (24h) / hard (72h), preços stale entram com flag e penalidade configurável, alerta de cobertura baixa, refusal explícito quando dados são insuficientes.
- *Schema não implementation-ready.* Aceito. Item 6: DDL completa em PLAN.md, com tipos, defaults, constraints, índices, retenção; "schema conforme README" removido.
- *Privacidade/LGPD.* Aceito. Item 9: retenção de 90 dias, anonimização k-anônima em grid de 500m, opt-in no `/start`, `/apagar_meus_dados`, acesso a coordenadas brutas restrito a admin com auditoria.

**Medium — aceito integralmente:**
- *Haversine ≠ desvio real.* Item 12: OSRM Table API com cache e fallback `Haversine × fator` flagado.
- *Concorrência SQLite.* Itens 26–28: single-worker explícito, busy_timeout, fila assíncrona de `consulta`, Postgres adiado pra fase 2.
- *Auth basic insuficiente.* Itens 37–39: contas individuais Argon2id + sessão + CSRF + autorização por cidade desde semana 1.
- *`coletado_em` ambíguo.* Item 6: split em `observed_at` (validado, ≤ now) e `recorded_at` (server-side); regra de "preço atual" documentada em item 7.
- *Métrica de sucesso fraca.* Item 53: 2 baselines fixos + IC 80% via bootstrap, `abastecimento_relatado` com volume e foto opcional.
- *`km_por_litro` determinístico.* Itens 6, 15: bandas p10/p50/p90 no perfil e na categoria, empate técnico quando bandas se sobrepõem.

**Low — aceito:**
- */rota* fora do MVP (itens 2, 51), com contrato real de detour-delta planejado pra fase 2.
- *Faixas de preço hard-coded.* Itens 6, 30: tabela `faixa_preco` por cidade × combustível, com override auditado.

**Nada rejeitado nesta rodada.**

### Round 2 (Codex security & data-integrity review) — 2026-05-02

**High — aceito integralmente:**
- *`/melhor-posto` sem auth/consent boundary, confia em `veiculo_perfil_id` do cliente.* Item 1 reescrito: endpoint autenticado (sessão admin ou bot service token), `veiculo_perfil_id` resolvido server-side via `usuario_id`/`telegram_id`, opt-in LGPD obrigatório, bind de cidade autorizada, caps de raio/`top_n`/autonomia, rate limit por usuário e IP.
- *`/apagar_meus_dados` não crash-safe nem restore-safe.* Item 9 reescrito: tombstone durável + transação única + drenagem da fila assíncrona filtrando o usuário (item 28) + bloqueio de INSERTs futuros (trigger + app check) + procedimento `replay_tombstones.py` pós-restore + backups encriptados com retenção curta. Tabela nova `tombstone_usuario` (item 6).
- *Logs vazam PII e segredos.* Item 40 reescrito: schema de telemetria allowlisted versionado, scrubber de headers e campos sensíveis, coordenadas só arredondadas a 3 casas ou hash, payload bruto proibido, retenção de destino ≤30 dias, acesso restrito.
- *Authz só clara para edição de preço.* Item 38 reescrito como matriz default-deny aplicada via dependência FastAPI única `require_authz`, cobrindo CSV import, station CRUD, overrides, role grants, exports, raw location reads, audit reads. Teste `test_authz_coverage` (item 45a) varre o router e falha se alguma rota mutadora não tiver a dependência.

**Medium — aceito integralmente:**
- *CSV/preço sem idempotência ou atomicidade.* Item 30a + tabela `import_batch` (item 6): `Idempotency-Key` no header, staging + commit transacional ou rollback total, chave natural com lat/lng arredondado pra deduplicar postos.
- *DDL com constraints que SQLite não roda.* Item 6 reescrito em SQLite-compatível: bbox via TRIGGER, ENUM via CHECK in-list, `interval` removido, validador app + teste de CI cobrindo cada constraint (item 31).
- *Bounds numéricos/strings incompletos.* Novo item 30b: bounds Pydantic + DB para lat/lng/autonomia/volume/top_n/km_por_litro/strings; foto via upload controlado (`foto_storage_key`), nunca URL externa.
- *Telegram webhook forjável.* Item 2: secret token validado antes do parse, segredo no path como defesa em profundidade, `long_polling` como fallback dev, updates exigem `ativo=1` e opt-in.
- *Jobs de fundo racing com SQLite single-writer.* Item 26 reescrito: APScheduler in-process, deploy stop-the-world com `flock` em arquivo, replicas=1; backup/anonimização/restore-test/audit-check rodam no mesmo processo.
- *Audit incompleto e mutável.* Item 41a: lista expandida de eventos auditados, hash chain SHA-256, verificação diária, cópia rotacionada em storage append-only fora do DB principal, sem rota de update/delete em `evento_audit`.

**Low — aceito:**
- *Renderização sem política de escape.* Item 36a: Jinja autoescape, escape Markdown v2 no Telegram, scrubber de logs, neutralização de fórmulas em CSV/XLSX export, CSP/HSTS/X-Frame-Options no admin.
- *JSON sem versão/limite.* Item 6: `schema_version` em `consulta`, `evento_audit` e `assumptions`; CHECK de tamanho em `top_n_resultado_json`, `assumptions_json`, `payload_json`.

**Nada rejeitado nesta rodada.**

### Round 3 (Codex ops & SRE review) — 2026-05-02

**High — aceito integralmente:**
- *`synchronous=NORMAL` undermina durabilidade de tombstone/audit/admin.* Item 27 reescrito: `synchronous=FULL` por default; escritas críticas usam `BEGIN IMMEDIATE` + checkpoint + verificação pós-commit; ack de `/apagar_meus_dados` só após confirmação durável.
- *`flock` local não coordena cross-machine no Railway/Fly.* Item 26 reescrito: lease em três camadas — plataforma (replicas=1, máquina pinada), filesystem (`flock`), e DB (`owner_lease` com heartbeat de 10s, panic-and-restart se perder). Healthcheck `/internal/ready` (item 31a) só responde 200 com lease + alembic head + smoke + audit chain ok; plataforma só direciona tráfego após 200.
- *Sem contrato de migração/rollback.* Item 31a novo: `upgrade()`+`downgrade()` testáveis em CI (ciclo upgrade→down→up), regra expand-then-contract com 1 release de compat backward, snapshot encriptado pre-deploy retido 30 dias, deploy bloqueia migração destrutiva sem aprovação manual, runbook `rollback.md` define quando re-deploy vs restore vs replay-tombstones.

**Medium — aceito integralmente:**
- *OSRM sem timeout/circuit/cache budget.* Item 12 reescrito: timeouts (1s/2s/3s), fanout máx 30, cache LRU 50 MB com TTL 24h, circuit breaker (3 falhas em 30s → 60s aberto), métricas + alerta de fallback > 20%, plano de saída do OSRM público antes de expandir.
- *Sem alertas/runbooks.* Item 41b novo: tabela de SLOs com threshold/severity/owner/runbook por sinal (latência, 5xx, OSRM, queue drop, staleness, no-result, backup, restore, anonimização, audit chain/verifier, lock retry, owner lease, authz, disco, WAL, cache).
- *Sem feature flags/canary.* Item 41c novo: tabela `feature_flag` com escopo global/cidade/usuário, kill switch global, shadow/dark mode para mudanças de fórmula, gate de canário (shadow ≥7d → canário 1 usuário ≥48h → cidade), CLI/UI pra acionar em < 1 min.
- *Estado e callback do bot não versionados.* Item 2: `bot_estado` com `schema_version` e `expira_em`, `callback_data` codifica versão, parser backward-compat por 1 release, expiração de 30 min, teste com update da versão N-1 contra código N.
- *`audit.chain_break` sem break-glass.* Item 41a expandido: três modos (íntegra, tampering → quarentena read-only, verifier_error → continua servindo + P2), comando `audit-unblock` com aprovação dupla + audit, runbook distingue tampering de bug.
- *`rate_limit` no DB = lock hotspot.* Item 39 reescrito: token bucket in-process para hot path; só estado durável (login bloqueado, export, import) persiste em colunas dedicadas; alerta em pico súbito.

**Low — aceito:**
- *Rotação de segredos não operacionalizada.* Item 36a novo: dual-secret window para Telegram webhook e bot service token, rotação periódica de backup key e telemetria, runbook `secrets.md`, audit em cada rotação.
- *Capacidade não coberta.* Item 36b novo: quotas e cleanup para volume, WAL, cache OSRM, audit append-only, backups, fotos, `consulta`; alertas de disco em 80%/90%, comportamento disk-full (503 "manutenção" + P1).

**Nada rejeitado nesta rodada.**
