# ShopWave Autonomous Support Resolution Agent
### Agentic AI Hackathon 2026 — KSOLVES En(AI)bling™

> "The world has enough chatbots. We are here to build agents."

---

## What This Is

A production-grade autonomous support agent that resolves 20 ShopWave customer support tickets concurrently using a **LangGraph ReAct loop**, chained tool calls, realistic failure injection, and full decision auditability.

---

## Tech Stack

| Layer | Technology |
|---|---|
| LLM | Azure OpenAI GPT-4o-mini |
| Orchestration | LangGraph (ReAct graph) |
| Concurrency | Python asyncio — 5 parallel workers |
| API + Dashboard | FastAPI + SSE real-time streaming |
| Data Validation | Pydantic v2 |
| Language | Python 3.11 |
| Infra | Docker + docker-compose |

---

## Project Structure

```
shopwave-agent/
├── main.py                    ← ENTRY POINT — run this
├── config.py                  ← All config from environment variables
├── models.py                  ← Pydantic schemas for all data
├── agent/
│   ├── orchestrator.py        ← asyncio.Queue + 5 concurrent workers
│   ├── react_loop.py          ← LangGraph ReAct graph (classify → loop → finalize)
│   ├── llm_client.py          ← Azure OpenAI wrapper
│   └── prompts.py             ← All system prompts (versioned)
├── tools/
│   ├── base.py                ← @tool_call decorator: retry + timeout + ToolResult
│   ├── read_tools.py          ← get_order, get_customer, get_product, search_kb
│   └── write_tools.py         ← check_refund, issue_refund, send_reply, escalate, cancel
├── data/
│   ├── tickets.json           ← 20 mock support tickets
│   ├── orders.json            ← Order data
│   ├── customers.json         ← Customer profiles + tiers
│   ├── products.json          ← Product metadata + return windows
│   └── knowledge_base.json    ← Policy & FAQ entries
├── audit/
│   └── logger.py              ← Writes audit_log.json with full reasoning traces
├── api/
│   └── server.py              ← FastAPI server + SSE streaming
├── static/
│   └── dashboard.html         ← Live real-time monitoring dashboard
├── failure_modes.md           ← 5 documented failure scenarios
├── architecture.png           ← Agent architecture diagram
├── audit_log.json             ← Generated after running (required deliverable)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example               ← Copy to .env and fill credentials
```

---

## Quick Start

### Option A — Plain Python (venv)

```bash
# 1. Clone and enter project
git clone https://github.com/YOUR_USERNAME/hackathon2026-YOUR_NAME
cd shopwave-agent

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure credentials
cp .env.example .env
# Edit .env and fill in your Azure OpenAI credentials

# 5. Run the agent (CLI mode — processes all 20 tickets)
python main.py

# OR: Run with live dashboard
python main.py --serve
# Then open http://localhost:8000
```

### Option B — Docker

```bash
# Build and run with dashboard
docker-compose up --build

# Open http://localhost:8000 in browser, click "Run Agent"

# OR: CLI mode only
docker-compose run --rm shopwave-agent-cli
```

### Option C — Single Ticket Debug Mode

```bash
python main.py --ticket TKT-001
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

```env
AZURE_OPENAI_ENDPOINT=https://your-resource.cognitiveservices.azure.com/
AZURE_OPENAI_API_KEY=your_key_here
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o-mini
AZURE_OPENAI_API_VERSION=2024-12-01-preview

MAX_WORKERS=5
MAX_REACT_STEPS=10
CONFIDENCE_THRESHOLD_ESCALATE=0.4
CONFIDENCE_THRESHOLD_CLARIFY=0.6
```

**Never commit `.env` to git. It is in `.gitignore`.**

---

## Architecture

```
Tickets (JSON)
     │
     ▼
Schema Validator (Pydantic)
     │
     ▼
asyncio.Queue ──────────────────────────────────────────┐
     │                                                   │
  ┌──▼──┐  ┌─────┐  ┌─────┐  ┌─────┐  ┌─────┐         │
  │ W01 │  │ W02 │  │ W03 │  │ W04 │  │ W05 │  ← 5 concurrent workers
  └──┬──┘  └──┬──┘  └──┬──┘  └──┬──┘  └──┬──┘         │
     │        │         │        │         │            │
     └────────┴─────────┴────────┴─────────┘            │
                         │                              │
                         ▼                              │
              LangGraph Agent Graph                     │
                         │                              │
              ┌──────────▼──────────┐                   │
              │  classify_node      │  (LLM triage)     │
              └──────────┬──────────┘                   │
              ┌──────────▼──────────┐                   │
              │  react_step_node    │  ← loops          │
              │  Thought→Act→Observe│                   │
              └──────┬──────┬───────┘                   │
                     │      │                           │
              continue?   done/max_steps                │
                     │      │                           │
              ┌──────▼──────▼───────┐                   │
              │  finalize_node      │                   │
              └──────────┬──────────┘                   │
                         │                              │
              ┌──────────▼──────────┐                   │
              │  AuditLogger        │──── audit_log.json│
              └─────────────────────┘                   │
                                                        │
              Dead-letter queue ◄──────────────────────┘
```


### LangGraph ReAct Loop


```
START → classify → react_step ──(loop)──► react_step → finalize → END
                        │
                    tool calls (min 3 per chain):
                    get_customer → get_order → check_refund → issue_refund → send_reply
```

### Tool Architecture

```
@tool_call decorator (tools/base.py)
  ├── asyncio.wait_for(timeout=8s)    ← catches hangs
  ├── Exponential backoff retry       ← 1s → 2s → 4s
  ├── ToolResult schema               ← always structured output
  └── @inject_realistic_failure       ← chaos: 15% timeout, 8% malformed

WRITE tool safety gate (tools/write_tools.py):
  issue_refund() → checks _eligibility_confirmed dict
  If check_refund_eligibility() was not called first → BLOCKED
```

---

## Hackathon Constraints — How We Satisfy Each

| Constraint | Implementation |
|---|---|
| ≥3 tool calls per chain | Enforced in system prompt + verified in audit log per ticket |
| Graceful tool failure | `@tool_call` decorator: timeout → retry → ToolResult(success=False) |
| Concurrent processing | `asyncio.Queue` + 5 worker coroutines, all tickets fan out in parallel |
| Explainable decisions | Full `thought` + `action` + `observation` logged per ReAct step |
| No hardcoded secrets | `.env` file + `.gitignore` — credentials only via environment |
| Working demo | `python main.py --serve` → live dashboard at localhost:8000 |
| README mandatory | This file |

---

## API Endpoints (when running --serve)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Live real-time dashboard |
| POST | `/run` | Start processing all 20 tickets |
| GET | `/stream` | SSE event stream (connects dashboard) |
| GET | `/results` | Full audit_log.json |
| GET | `/status` | Current run status |
| GET | `/health` | Health check |

---

## Output Files

After running, you get:

- `audit_log.json` — Full structured log with all 20 tickets, every tool call, reasoning step, outcome, confidence score
- `agent.log` — Human-readable runtime log

---