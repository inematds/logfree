# LogFree

Solução local por cidade que indica, para frotas de última milha (moto, carro, caminhão leve), o posto de combustível com **melhor custo real por km rodado** — não apenas o preço mais baixo na bomba.

Acesso por **bot Telegram** (motorista) e **web** (gestor/operador).

---

## Setup (dev)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# Migrations + seed cidade_demo
alembic upgrade head
python -m app.db.seed

# Bootstrap admin local
logfree bootstrap-admin --email admin@local --password trocar-em-prod-12345 --cidade-id 1

# Sobe o servidor
uvicorn app.api.main:app --host 0.0.0.0 --port 8000

# Em outra janela: roda os testes
pytest -q
ruff check app tests
```

Variáveis de ambiente em `.env.example`. Em produção, configure pela plataforma (Fly secrets / Railway env).

## Comandos úteis

```bash
logfree bootstrap-admin --email <e> --password <s> [--cidade-id <id>]
logfree audit-verify                      # checa cadeia hash do evento_audit
logfree audit-unblock --approver=... --motivo=...   # break-glass (precisa AUDIT_UNBLOCK_KEY)
logfree flag-set --chave=ranking.osrm_enabled --valor=true
logfree flag-set --chave=kill_switch.global --valor=true
logfree replay-tombstones                 # pós-restore: re-anonimiza usuários tombstoned
```

Endpoints principais:

- `POST /melhor-posto` — auth obrigatória (sessão admin OU `X-Bot-Token` + `X-Bot-User-Telegram-Id`).
- `POST /bot/webhook/{path_secret}` — webhook Telegram (header `X-Telegram-Bot-Api-Secret-Token` validado).
- `GET /admin/login`, `GET /admin/`, `GET /admin/postos/{cidade_id}` — UI HTMX.
- `GET /internal/ready`, `GET /internal/metrics` — healthcheck e métricas.

Plano completo: ver [`PLAN.md`](PLAN.md) (passou por 3 rodadas de revisão adversarial — engenharia, segurança, ops/SRE).

## Runbooks

[`runbook/README.md`](runbook/README.md) lista todos os runbooks (rollback, restore, audit chain break, OSRM degradação, segredos, capacidade, etc).

---

## 1. Problema

Motoristas de última milha perdem margem abastecendo no posto errado. A decisão é tomada pelo preço da placa, mas o que importa é o **custo efetivo por km**, que depende de:

- preço do litro
- consumo do veículo naquele combustível
- desvio que o motorista faz pra chegar até o posto

Hoje ninguém entrega isso. Waze e Google mostram rota. Apps de preço mostram preço. Nenhum junta os dois com o perfil do veículo e o contexto de entrega urbana.

## 2. Proposta de valor

> "O posto mais barato **pra você, hoje, considerando seu veículo e onde você está.**"

Diferenciais:

- Curadoria **local por cidade** — dados confiáveis, sem crowdsourcing caótico.
- Cálculo de **custo efetivo por km** por veículo, não preço bruto.
- Acesso por **Telegram**, onde o motorista já vive, e **web** pro gestor da frota.
- Pensado pra **última milha**: raio curto, muitas paradas, decisão rápida.

## 3. Escopo do MVP

**Entra:**

- Cadastro manual de postos e preços (operador da cidade piloto).
- Cadastro de veículos (tipo, modelo, combustível, km/l).
- Bot Telegram respondendo:
  - `/postos` — lista os N postos mais baratos por combustível na cidade.
  - `/melhor <combustível>` — melhor posto pro veículo do usuário, considerando localização enviada.
  - `/rota origem → destino` — rota sugerida + posto recomendado no caminho.
- Web: mapa da cidade com postos, tabela ordenável, painel admin pra atualizar preços.
- Relatório simples por motorista/frota (economia estimada).

**Fica pra depois:** IA, previsão de preços, integração com cartão-frota, OBD-II, multi-cidade, app nativo.

## 4. Fluxos principais

**Motorista (Telegram):**

1. `/start` — cadastra veículo (tipo + km/l médio).
2. Envia localização.
3. `/melhor diesel` — bot consulta DB, calcula custo efetivo, devolve top 3 postos com preço, distância, desvio e economia estimada vs. o mais próximo.

**Gestor (Web):**

1. Login admin, tela de postos, edita preço do dia.
2. Dashboard: mapa, lista, histórico de preços.
3. Cadastro de frota e motoristas (fase 2).

## 5. Modelagem de dados

Banco local em **SQLite** no MVP, migração posterior para **Supabase (Postgres + PostGIS)** sem mudança de schema.

```sql
cidade(id, nome, uf, bbox_lat_lng)

posto(id, cidade_id, nome, bandeira, endereco, lat, lng, ativo)

combustivel(id, codigo)
  -- gasolina_comum, gasolina_aditivada,
  -- etanol, diesel_s10, diesel_s500, gnv

preco(id, posto_id, combustivel_id, valor, coletado_em, fonte)
  -- histórico completo, nunca sobrescreve; query pega o mais recente

veiculo_tipo(id, nome)                    -- moto, carro, caminhao_leve
veiculo_perfil(id, tipo_id, modelo, combustivel_id, km_por_litro)

usuario(id, telegram_id, nome, perfil)    -- motorista, gestor, admin
usuario_veiculo(usuario_id, veiculo_perfil_id)

consulta(id, usuario_id, lat, lng, combustivel_id, feita_em, resposta_json)
  -- log de consultas: base pra análise e treino de IA na fase 3
```

Índices principais:

- `preco(posto_id, combustivel_id, coletado_em DESC)`
- `posto(cidade_id, ativo)`
- Geoespacial em `posto(lat, lng)` — PostGIS quando migrar pro Supabase.

## 6. Algoritmo inicial — sem IA

Usuário na posição `(lat0, lng0)` quer combustível `C` com veículo de consumo `k` km/l:

```
Para cada posto p da cidade com preço atual de C:
  d = distancia(p, posicao_usuario)          # Haversine no MVP
  desvio_ida_volta = 2 * d
  litros_desvio = desvio_ida_volta / k
  custo_desvio = litros_desvio * preco_C(p)
  custo_efetivo = preco_C(p) + (custo_desvio / capacidade_tanque_usuario)
Ordena ascendente por custo_efetivo
Retorna top 3
```

Regras de negócio embutidas:

- Ignora postos com preço desatualizado além de X dias (parametrizável por cidade).
- Ignora postos inativos ou fora da `bbox` da cidade.
- Se `d > raio_maximo` (ex.: 8 km moto, 15 km caminhão), descarta.
- Empate é desempatado pelo preço bruto mais baixo.

Isso valida o produto **sem nenhum modelo de ML** e já gera economia mensurável. Todo log em `consulta` vira dataset pra fase de IA.

## 7. Stack recomendada

| Camada             | Escolha                                | Motivo                                               |
| ------------------ | -------------------------------------- | ---------------------------------------------------- |
| Linguagem          | Python 3.12                            | Ecossistema de dados, bot e web num stack só         |
| Backend/API        | FastAPI                                | Rápido, async, OpenAPI automático                    |
| Bot                | python-telegram-bot v21                | Maduro, suporta webhooks                             |
| DB MVP             | SQLite (arquivo local)                 | Zero infra, backup fácil                             |
| DB fase 2          | Supabase (Postgres + PostGIS)          | Auth, realtime, API pronta, migração trivial         |
| ORM                | SQLAlchemy 2 + Alembic                 | Migrations iguais em SQLite e Postgres               |
| Web frontend       | HTMX + Jinja (ou Next.js se quiser SPA)| HTMX acelera MVP e evita stack duplo                 |
| Mapa               | Leaflet + OpenStreetMap                | Grátis, suficiente pro MVP                           |
| Roteamento         | OSRM público ou self-hosted            | Grátis, sem Google Maps no começo                    |
| Deploy MVP         | Railway ou Fly.io                      | 1 container, barato                                  |
| Observabilidade    | Logfire ou Sentry free                 | Erros e traces                                       |

Tudo roda num único processo Python no MVP. Sem microserviços.

## 8. Roadmap

- **Fase 1 — MVP (4–6 semanas):** cidade piloto, bot + web, cadastro manual.
- **Fase 2 — Escala operacional:** migração pra Supabase, multi-cidade, atualização de preços pelo celular via bot interno, OCR (Tesseract) em foto de bomba.
- **Fase 3 — Inteligência:** previsão de preço por posto (Prophet / LightGBM), detecção de anomalia, ranking personalizado por histórico do motorista.
- **Fase 4 — Frota:** dashboard de economia agregada, integração com rastreador, relatório fiscal, API pra TMS.
- **Fase 5 — Rede:** expansão nacional, freemium, parcerias com redes de postos.

## 9. Riscos e mitigação

| Risco                                            | Mitigação                                                                 |
| ------------------------------------------------ | ------------------------------------------------------------------------- |
| Preços desatualizados matam a confiança          | Mostrar timestamp do preço no bot; alerta diário pro operador             |
| Operador cansa de atualizar                      | Bot interno `/atualizar_preco` pra operador fazer do celular em 10s       |
| Cidade piloto pequena demais pra provar ROI      | Escolher cidade com volume real de entregas                               |
| Motorista não adota bot                          | Onboarding via grupo de WhatsApp da frota e vídeo curto                   |
| Precisão de consumo (km/l) é chute               | Começar com médias da categoria e refinar com histórico do próprio motorista |

## 10. Plano de execução

| Semana | Entrega                                                                     |
| ------ | --------------------------------------------------------------------------- |
| 1      | Schema SQLite, seed de postos/veículos da cidade piloto, importação CSV     |
| 2      | Core do cálculo de custo efetivo e testes; endpoint FastAPI `/melhor-posto` |
| 3      | Bot Telegram: `/start`, `/melhor`, `/postos`, recebimento de localização    |
| 4      | Web admin (HTMX) pra atualizar preço e mapa Leaflet público                 |
| 5      | Deploy, piloto com 3–5 motoristas reais, coleta de feedback                 |
| 6      | Ajustes, métricas de economia real, decisão de migrar pra Supabase          |

---

## Decisões pendentes

Dois pontos ainda precisam ser definidos antes da estrutura de código ser criada:

1. **Cidade piloto** — qual cidade vai rodar o MVP? Idealmente onde já exista contato com uma frota disposta a testar.
2. **Operação dos preços** — quem atualiza os preços no dia a dia: o próprio mantenedor, um operador contratado, ou a frota parceira mandando foto da bomba pelo bot?

Uma vez definidos, o próximo passo é criar o scaffold Python (FastAPI + bot + SQLite + Alembic) e o primeiro seed de dados.
