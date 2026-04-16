# Crítica externa #01 — avaliação do estado do repositório

> Análise externa recebida sobre o repositório `inematds/logfree` no estado do primeiro commit (README + `doc/logfree.txt`, sem código).

---

Analisei o `inematds/logfree` e, hoje, ele está muito mais próximo de um **PRD/blueprint de produto** do que de um sistema implementado. O repositório é público, tem **1 commit**, sem releases, sem issues abertas visíveis, e a estrutura principal é `README.md` + pasta `doc` com um `logfree.txt`; não há sinais, na árvore exibida, de backend, frontend, migrations, testes ou código executável.

**Leitura executiva:** a ideia é boa, o recorte de problema é forte, o MVP está muito bem delimitado, mas ainda falta transformar isso em **arquitetura executável, contratos técnicos e prova operacional**. O texto do README define com clareza o problema, a proposta de valor, o MVP, a modelagem inicial, o algoritmo sem IA, a stack e um roadmap por fases.

## O que está muito bom

O maior acerto do projeto é o **posicionamento**: ele não promete "a rota mais rápida", e sim o **menor custo real por km**, combinando preço do litro, consumo do veículo e desvio até o posto. Isso é um diferencial mais defensável do que só comparar preço de bomba ou rota.

Outro ponto forte é o **escopo do MVP**. O README evita o erro comum de começar com IA desnecessária e deixa explícito que a fase inicial deve funcionar com cadastro manual de postos/preços, bot no Telegram, painel web simples e cálculo heurístico. Essa decisão reduz risco e acelera validação.

A **modelagem conceitual** também está bem encaminhada. A separação entre `posto`, `combustivel`, `preco` histórico, `veiculo_perfil`, `usuario` e `consulta` cria uma base razoável para depois evoluir para analytics e IA. O fato de `preco` guardar histórico, em vez de sobrescrever o valor atual, é uma decisão correta.

A **stack sugerida** para MVP é coerente: Python 3.12, FastAPI, `python-telegram-bot`, SQLite, SQLAlchemy/Alembic, HTMX/Jinja, Leaflet e OSRM. Para um piloto de cidade única, é uma escolha pragmática.

## Onde o projeto ainda está fraco

A principal limitação é simples: **ainda não existe produto, só especificação**. O repositório não mostra código-fonte do bot, API, interface web, schema real, migrações, seeds, testes, Docker, CI/CD nem instruções de setup. Hoje ele comunica visão, mas não sustenta execução técnica.

Também há um ponto conceitual importante no algoritmo. O README calcula `custo_efetivo = preco_C(p) + (custo_desvio / capacidade_tanque_usuario)`. Essa fórmula pode ser útil como aproximação, mas mistura **preço por litro** com **custo do desvio rateado pela capacidade do tanque**, o que pode distorcer a recomendação. Em operação real, o ideal é comparar cenários como:

- custo incremental do desvio para abastecer naquele posto;
- volume efetivo pretendido de abastecimento;
- custo marginal por km ou por viagem;
- efeito do abastecimento parcial vs. cheio.

Ou seja: a lógica está boa como rascunho de MVP, mas **a função objetivo ainda precisa ser refinada**. O próprio README já indica que a métrica central é custo real por km; a implementação deve refletir isso de forma mais consistente.

Outro ponto frágil é a **dependência operacional da atualização manual de preços**. O documento reconhece esse risco e sugere mitigação com bot interno e OCR no futuro, mas, no curto prazo, a confiança do sistema vai depender totalmente de disciplina operacional. Se o preço estiver atrasado, a proposta de valor quebra.

Também senti falta de decisões técnicas sobre:

- autenticação e autorização no painel web;
- estratégia de geocodificação;
- como determinar se um posto está "no caminho" da rota;
- política de qualidade e validade dos dados;
- observabilidade mínima por fluxo;
- testes do cálculo e da recomendação.

Essas ausências não invalidam o projeto, mas mostram que ele ainda está no estágio de **documento de concepção**.

## Avaliação técnica

Como produto: **8/10**. O problema é real, o recorte é inteligente e o MVP está enxuto.

Como repositório de engenharia: **3/10**. Falta praticamente toda a camada de implementação, qualidade e operação.

## O que fazer imediatamente para evoluir

A melhor próxima etapa não é "colocar IA". É criar o **primeiro backbone executável**:

1. Estruturar o repositório com `app/api`, `app/bot`, `app/core`, `app/db`, `app/web`, `tests`, `alembic`.
2. Implementar o schema real com migrations.
3. Criar seed de uma cidade piloto com 10–30 postos.
4. Entregar primeiro o endpoint `/melhor-posto`.
5. Cobrir com testes unitários a função de ranking.
6. Depois plugar o comando `/melhor` no Telegram.
7. Só então subir o painel admin para editar preços.

Essa ordem preserva o coração do negócio: **o motor de decisão**.

## Veredito

O `logfree` é um **bom projeto de produto/MVP**, com visão clara e recorte promissor para logística de última milha. Mas, no estado atual, ele ainda não é um software em desenvolvimento de fato; é uma proposta muito bem escrita esperando virar código.

Opções de próximo passo prático:

- um **diagnóstico técnico com backlog priorizado**,
- a **estrutura inicial do repositório**,
- ou um **README reescrito como plano de implementação real**.

---

## Resposta do time (resumo da discussão interna)

A crítica é, na maior parte, justa e útil.

**Pontos aceitos integralmente:**

- Estado "PRD, não sistema" é factualmente correto — foi deliberado (aguardando decisões de cidade piloto e operação de preços), mas o observador externo vê o mesmo. Lição: paralelizar scaffold do motor de decisão, que não depende dessas duas decisões.
- Decisões técnicas faltando — auth do painel, geocodificação, critério de "posto no caminho" (buffer da polyline / distância perpendicular), política de validade de preço, observabilidade, estratégia de testes. Vira seção no README ou ADRs.
- Ordem de construção sugerida (endpoint → testes → bot → admin) já está alinhada ao plano por semana do README, mas vai ser reforçada.

**Ponto técnico aceito — fórmula do custo efetivo:**

A fórmula original:

```
custo_efetivo = preco_C(p) + (custo_desvio / capacidade_tanque)
```

só é dimensionalmente correta se o motorista sempre enche o tanque completo. Para abastecimento parcial — comum em última milha — o divisor correto é o volume efetivamente abastecido `V`, não a capacidade. Duas formulações melhores:

```
# (a) custo por litro efetivamente comprado, dado V:
custo_por_litro(p, V) = preco_C(p) + (2*d/k) * preco_C(p) / V

# (b) custo marginal por km de operação (mais alinhado à proposta):
custo_por_km(p) = preco_C(p) / k_veiculo
                + (2*d * preco_C(p) / k_veiculo) / km_ate_proximo_abastecimento
```

A formulação (b) é preferível porque reflete literalmente a promessa do produto: *"menor custo real por km"*. Vai substituir a original no README.

**Contraponto leve:**

Nota 3/10 de engenharia é dura para um repositório de um dia. Não há código ausente por estagnação — há código ainda não escrito por decisão. Mas o efeito prático pro observador externo é o mesmo, então não é terreno pra brigar.

**Ações derivadas:**

1. Corrigir a fórmula no README para a versão (b).
2. Adicionar seção "Decisões técnicas abertas" no README cobrindo auth, geocoding, roteamento, validade de preço, observabilidade, testes.
3. Criar o scaffold (`app/api`, `app/bot`, `app/core`, `app/db`, `app/web`, `tests`, `alembic`) com o motor de ranking e testes unitários funcionando, usando uma `cidade_demo` com 10 postos fake no seed — sem depender das decisões pendentes.
4. Commits separados para histórico legível.
