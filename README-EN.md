<div align="center">

<img src="./frontend/src/assets/logo/beemarketbot-logo.jpg" alt="BeeMarketBot" width="360"/>

# BeeMarketBot

**Predict anything.** Upload a document, ask a question and get a report from a social simulation with AI agents.

[Español](./README.md) | English | [中文](./README-ZH.md)

</div>

## What it does

BeeMarketBot builds a simulated world from a document and lets it evolve to
answer "what if…?" questions.

You upload the source material (reports, data, any text in PDF, MD or TXT) and
describe in natural language what you want to know. The system does the rest in
five steps:

1. **Graph building.** It extracts the entities and relationships of the document
   and builds a knowledge graph with memory, searched by meaning.
2. **Environment setup.** It generates the agents' profiles from those entities
   and gives them their memory and configuration.
3. **Simulation.** The agents live together on two social platforms, one
   Twitter-like and one Reddit-like, and react to each other over as many rounds
   as you define, while their memory updates with what happens.
4. **Report.** A report agent goes through the simulation with query tools and
   writes a report with the answer.
5. **Interaction.** You can talk to any agent of the simulated world, or to the
   report agent, to go deeper.

You only have to upload the material and describe the question. BeeMarketBot
returns a report and the simulated world, open for further questions.

To prepare the starting material there is a
[seed template](./docs/plantilla-semilla.md) (in Spanish): context, main
question, audience segments and analysis dimensions. It can also be
downloaded from the app's start screen.

The interface is in Spanish (default), English and Chinese, and the model
answers in the chosen language.

The interface has two color modes, dark (the default) and light. The button in
the header, next to the language selector, switches between them, and each device
remembers the choice.

Simulations make many calls to the model. Start with few rounds (fewer than 40)
and increase afterwards.

## Access

Every page and API route asks for a session. There is no public sign-up and no
default password. There are three access levels:

| Level | What it can do |
|---|---|
| `admin` | Everything, and also manages the accounts (`/users`) |
| `editor` | Create and run simulations, generate reports |
| `viewer` | Read only |

The first administrator is created once, from the project folder:

```bash
# With Docker: prints the initial password once
docker compose exec -T beemarketbot-api sh -c "cd backend && uv run --frozen python -m app.auth create-user --username <name> --role admin --generate-password"

# Without Docker: asks for the password
cd backend && uv run python -m app.auth create-user --username <name> --role admin
```

That password is changed at first sign-in. From then on, accounts are created in
**Users**, in the administrator's menu, or with the
[command line](#account-management).

Read-only accounts see the controls that change something greyed out (create a
project, start a simulation, generate a report, talk to the agents). Opening a
screen never triggers an action either.

## Deploy with Docker

You need Docker and Docker Compose v2. Three containers start: the API and
simulation application, the GraphRAG worker, and FalkorDB.

```bash
git clone https://github.com/npalmae/BeeMarketBot.git
cd BeeMarketBot

# 1. Configuration
cp .env.example .env
#    Edit .env: model, embeddings and SECRET_KEY (openssl rand -hex 32)

# 2. Build and start (API + GraphRAG worker + FalkorDB)
docker compose up -d --build

# 3. Create the first administrator (see «Access»)

# 4. Check the graph memory against your real configuration
docker compose exec beemarketbot-graphrag-worker sh -c "cd backend && uv run --frozen python scripts/validate_graph_memory.py"
```

The application reads `.env` from the root folder and exposes port `3000`
(interface) and `5001` (API). If they are taken, set `WEB_PORT` and `API_PORT` in
`.env`. FalkorDB is reachable only from the machine, at `127.0.0.1:6379` (change
the port with `FALKORDB_PORT`).

Data is kept outside the container: `./backend/uploads` (projects, simulations,
reports, the durable GraphRAG queue, memory state and accounts) and the
`falkordb-data` volume (the graph).
To update, run `docker compose up -d --build` again.

The API and worker communicate through `backend/uploads/graphrag_queue`. Pending
jobs survive restarts; a GraphRAG failure does not invalidate a completed
simulation or block its report.

## Deploy from the source code

| Tool | Version | For |
|---|---|---|
| Node.js | 20.19 or later (or 22.12 and later) | Interface |
| Python | 3.11 or 3.12 | Backend |
| uv | recent | Python environment |
| FalkorDB | 6.0.x (or Neo4j 5.26 and later) | Graph database of the memory |

```bash
# Dependencies (root + frontend + backend)
npm run setup:all

# Graph database (first time only)
docker run -d --name falkordb -p 127.0.0.1:6379:6379 -v falkordb-data:/data falkordb/falkordb:6.0.4

# Terminal 1: interface and backend
npm run dev

# Terminal 2: GraphRAG worker
npm run graphrag-worker
```

The interface is at `http://localhost:3000` and the API at
`http://localhost:5001`. The configuration is the same root `.env` file.

## Configuration

All variables are set in `.env`; `.env.example` explains them one by one.

| Variable | Required | Purpose |
|---|---|---|
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL_NAME` | yes | Language model. Any provider with an OpenAI-format API works. |
| `GRAPH_DB_URL` | yes | Graph database: FalkorDB (`falkordb://host:6379`) or Neo4j (`neo4j://user:password@host:7687`). With Docker it is already set. |
| `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` | yes | Model that turns text into vectors, and the size of those vectors. |
| `SECRET_KEY` | on a server | Signs the sessions. Without it one is generated at start, and everyone signs in again after every restart. |
| `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL` | no | Only if the embeddings come from a provider other than the model. |
| `GRAPH_LLM_*` | no | Another model for graph extraction, and other memory settings. |
| `GRAPHRAG_POLL_INTERVAL` | no | Seconds between durable queue checks (1 by default). |
| `GRAPHRAG_STALL_TIMEOUT` | no | Seconds without loop progress before the worker restarts itself (1200 by default). |
| `LLM_TEMPERATURE`, `LLM_EXTRA_BODY` | no | Providers that only accept a fixed temperature or ask for their own parameters. |
| `LLM_BOOST_*` | no | A second provider for the parallel simulation, to get more simultaneous calls. |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | no | Creates the first administrator at start, only when no account exists yet. |
| `SESSION_HOURS`, `COOKIE_SECURE`, `CORS_ORIGINS` | no | Session lifetime (24 hours), HTTPS-only cookies, external origins allowed to call the API. |

## Account management

Commands run from `backend/` as `uv run python -m app.auth <command>`.

| Command | What it does |
|---|---|
| `create-user --username <name> --role <level>` | Creates an account; it must change its password at first sign-in. |
| `list-users` | Lists the accounts. |
| `set-password --username <name>` | Assigns a new password; it must be changed at next sign-in. |
| `set-role --username <name> --role <level>` | Changes the access level. |
| `disable --username <name>` | Stops the account from signing in. |
| `enable --username <name>` | Lets the account sign in again. |

With Docker they run inside the container:

```bash
docker compose exec -T beemarketbot-api sh -c "cd backend && uv run --frozen python -m app.auth list-users"
```

The password is never passed as an argument: it is typed in the terminal, read
from standard input (`--password-stdin`) or generated (`--generate-password`,
which shows it once).

Accounts are kept in `backend/uploads/auth/users.db`, next to the rest of the
data, with scrypt-hashed passwords. Failed sign-ins are limited. Over plain HTTP
the password travels unencrypted: put HTTPS in front if the app is reachable
from outside a trusted network.

## Development

```bash
# Backend tests
cd backend && uv run python -m pytest -q tests

# Build the interface
cd frontend && npm run build

# Regenerate the interface style after changing a component
cd frontend && node scripts/build-web2-theme.mjs
```

Tests never write to the real data folder. Five of them check the memory against
a real graph database and only run when `GRAPH_MEMORY_TEST_URL` is set.

```
backend/app/api           API routes
backend/app/auth          accounts, sessions and access levels
backend/app/graph_memory  graph memory (Graphiti)
backend/app/services      ontology, graph, simulation and report
backend/scripts           simulation runners and memory validation
backend/tests             backend tests
frontend/src              interface (Vue 3)
locales                   interface and message texts
```

## License and origin

BeeMarketBot is free software under the
[GNU Affero General Public License v3.0](./LICENSE). It is a derivative of
[MiroFish](https://github.com/666ghj/MiroFish), by 666ghj. The social simulation
builds on [OASIS](https://github.com/camel-ai/oasis) (camel-ai) and the graph
memory on [Graphiti](https://github.com/getzep/graphiti).
