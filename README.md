# FALZH

> **AI-powered travel service — on WhatsApp.**

> **🚧 Work in progress — not yet published. Coming soon.**

<p align="center">
  <img src="assets/Falsa.png" alt="FALZH logo" width="100%">
</p>

FALZH lets passengers search trips and connect with drivers, and lets drivers publish, update, and manage their trips — all through a natural conversation on WhatsApp. No app to install, no complex UI to learn. Just type what you want.

---

## Problem

Travel booking platforms in my country are poorly adopted. Not because people don't *want* a better way — but because:

- Users don't want to learn yet another app with a steep learning curve.
- People prefer something that works *inside* the tools they already use daily.
- Asking users to download a new app is a barrier. WhatsApp is already on their phone.
- Existing UIs are complicated and unintuitive.

## Solution

FALZH removes all of that. The entire interface is a single chat conversation. **You say where you're going and when — FALZH handles the rest.**

- **Passengers** describe their trip in natural language; FALZH searches available trips, shows options, and connects them with drivers.
- **Drivers** publish trips, manage schedules, and receive trip interest notifications — all by chatting.
- No app install, no account creation flow, no confusing dashboard.

---

## Features

- **Natural-language trip search** — "I want to go from Sana'a to Taiz tomorrow morning"
- **WhatsApp-native interaction** — interactive lists, text replies, no extra UI
- **Driver tools** — publish trips, add cars, modify/delete trips, receive trip interest notifications
- **Passenger tools** — search trips, select trips, get driver contact, get FALZH information
- **AI-powered orchestration** — tool-calling LLM (Groq primary, Hugging Face fallback) routes intent to the right action
- **RAG knowledge base** — company info, pricing, policies embedded via Jina AI and retrieved on demand
- **Trip vector search** — semantic search over driver trips for flexible departure matching
- **Multi-mode personas** — new-user onboarding, passenger mode, driver mode

---

## Why Vector Search?

Users describe trips in natural language: *"I want to go to Taiz tomorrow afternoon"*. Traditional SQL can't handle that. It would need exact dates, predefined routes, rigid filters.

**Semantic search** solves this. Trip departures ("tomorrow afternoon", "Friday morning") and routes are embedded into vectors using Jina AI. When a user says something, the query gets embedded the same way — and pgvector finds the closest matching trips by meaning, not by keyword.

This means:
- Users can phrase trips however they want — the system understands intent.
- Spelling mistakes or missing letters don't break the search — semantic similarity handles typos and partial input gracefully.
- No rigid dropdowns, no calendar pickers, no predefined route lists.
- The same vector index powers both trip search and RAG knowledge retrieval.
- It lives inside Postgres (pgvector) — no separate vector database to manage.

---

## Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| **Language** | Python 3.12+ | Core comfort zone; best fit for the backend services, AI orchestration, and data work |
| **Framework** | FastAPI (async) | High performance, native async, great DX with Pydantic validation |
| **Primary LLM** | Groq (via OpenAI-compatible API) | Fast inference, generous free tier, tool-calling support |
| **Fallback LLM** | Hugging Face (OpenAI-compatible) | Backup provider for resilience when Groq is unavailable |
| **Embeddings** | Jina AI (`jina-embeddings-v5`) | Multilingual-capable, 1024-dim vectors, simple REST API |
| **Database** | Supabase (PostgreSQL + pgvector) | Managed Postgres with built-in vector search, real-time, and REST API |
| **WhatsApp** | Meta WhatsApp Cloud API | Official business API for reliable message delivery; webhook-based |
| **WhatsApp Bridge** | Baileys (Node.js) | Temporary bridge for WhatsApp Web protocol until Cloud API is fully onboarded; the system is designed to interact with Baileys identically to how it interacts with Cloud API, making the switch seamless |
| **Containerization** | Docker / Compose | Consistent dev environment, easy deployment |
| **Testing** | pytest + pytest-asyncio | Async-native testing with fully mocked external services |
| **Linting** | ruff | Fast, modern Python linter |

---

## Architecture

```
WhatsApp User
     │
     ▼  (message)
Baileys Bridge ──► FALZH API ──► AI Orchestrator ──► Tools
(Node.js,            (FastAPI)     (Groq / HF)          │
 separate repo)           │                              │
                          ▼                              ▼
                   Supabase (pgvector)           WhatsApp Cloud API
                   (trips, customers,            (outbound replies)
                    messages, embeddings)
```

The system is designed so the WhatsApp integration layer is swappable. The Baileys bridge (Node.js) handles the Web protocol; the FALZH Python backend talks to it the same way it talks to the Cloud API. When Cloud API onboarding is complete, swapping the bridge requires zero changes to the core logic.

---

## Quick Start

### Prerequisites

- Python 3.12+
- A Supabase project (with pgvector enabled)
- API keys: Groq, Jina AI, Hugging Face, Meta WhatsApp Cloud

### Setup

```bash
# 1. Clone and enter the repo
git clone <repo-url> && cd falzh

# 2. Configure environment
cp .env.example .env
# Fill in your credentials (Supabase, Groq, Jina, WhatsApp, etc.)

# 3. Install dependencies
pip install -r requirements.txt

# 4. Apply database migrations
# Run the SQL files in supabase/migrations/ in order via Supabase SQL editor or psql

# 5. Start the server
uvicorn main:app --reload
```

The API is now running at `http://localhost:8000`.

### Seed Knowledge Base

```bash
./scripts/setup_and_seed.sh
```

This embeds the FALZH info document (`prompts/falzh_info.md`) and all active trips into Supabase pgvector for RAG retrieval.

### Run Tests

```bash
pytest
ruff check .
```

> Note: I'm still learning testing best practices through this project. Tests exist and pass, but coverage and structure will improve over time.

---

## Docker

```bash
docker compose up
```

Mounts the current directory with hot-reload enabled.

---

## WhatsApp Baileys Bridge

FALZH uses the official Meta Cloud API for production messages. During early stages — while waiting for commercial approval — a **Baileys-based bridge** acts as the WhatsApp gateway.

The bridge is a separate Node.js project:

#### [https://github.com/codeBOKER/wh_baileys](https://github.com/codeBOKER/wh_baileys)

It connects to WhatsApp via the Web protocol, relays inbound messages to the FALZH API, and forwards responses back to the user. The FALZH API treats the bridge identically to the Cloud API, so switching later requires no backend changes.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/healthz` | Health check |
| `GET` | `/webhooks/whatsapp` | Webhook verification (challenge) |
| `POST` | `/webhooks/whatsapp` | Receive WhatsApp messages |
| `POST` | `/admin/seed-info` | Seed FALZH RAG info |
| `POST` | `/admin/sync-trips` | Sync trip embeddings |
| `POST` | `/admin/jina-embed` | Test embedding |
| `POST` | `/admin/llm-tool-call` | Debug tool calling |

---

## Project Structure

```
falzh/
├── app/
│   ├── ai/                # LLM orchestration, providers, tool schemas
│   ├── api/               # FastAPI routes, dependency injection
│   ├── database/          # Supabase repository (CRUD + vector search)
│   ├── models/            # Pydantic request/response & domain models
│   ├── services/          # Business logic (conversation, admin, embeddings)
│   ├── tools/             # Tool handler implementations + registry
│   ├── utils/             # Logging, time helpers, departure parsing
│   └── whatsapp/          # WhatsApp client, parser, security, trip UI
├── prompts/               # AI system prompts + RAG seed data
├── scripts/               # Admin CLI scripts (seed, sync, test)
├── supabase/migrations/   # SQL schema migrations (apply in order)
├── tests/                 # pytest suite with mocked externals
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── requirements.txt
└── .env.example
```

---

## FAQ

**How do you find trips in the early stage with few users and drivers?**

We scrape public trip listings from WhatsApp groups and seed them into the database. This bootstraps the system with real trip data so passengers get results from day one. As adoption grows, drivers publish trips directly through the assistant.

**Why not use JavaScript across the entire project?**

Baileys (Node.js) is used only as a temporary WhatsApp bridge. My strongest area is Python — the core backend, AI orchestration, and data pipeline all benefit from Python's ecosystem, so the main service is built in Python. The bridge is minimal and swappable.

**How does the Baileys bridge differ from the Cloud API?**

It doesn't — that's the point. The FALZH backend speaks the same protocol to both. The Baileys bridge simply translates the WhatsApp Web protocol into the same format the Cloud API uses. When Cloud API approval comes through, we swap the bridge with zero backend changes.

---

## License

MIT
