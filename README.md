# ReserveGuard

ReserveGuard is a settlement agent for multi-tenant marketplaces on Airwallex. It holds a reserve
for each seller, pays out net proceeds through connected-account transfers, and re-rates one
seller's reserve when risk evidence arrives, with a short memo for every change.

Built for the Airwallex Agentic Banking Hackathon (Multi-Tenant Platforms, Kit 8: Marketplace Settlement).

## Architecture

| Component | Runs as | Role |
|---|---|---|
| `reserve_engine.py` | Pure Python functions | Reserve rate, fee, payout, shortfall and release maths |
| `ledger.py` | Immutable in-memory ledger | Per-seller entries and reconciliation checks |
| `settlement.py` | Service object | Runs settlement cycles and reserve changes through `AirwallexClient` |
| `airwallex/fake.py` | In-memory fake | Balances, idempotency and insufficient-funds rules for tests and the demo |
| `airwallex/rest.py` | HTTP client (httpx) | Real Airwallex API: token login, major-unit amounts, `request_id` idempotency with lookup-before-retry, 429 backoff, status polling |
| `airwallex/sandbox.py` | Setup helpers (sandbox only) | Global account and deposit funding, seller onboarding (create → submit → ACTIVE), platform reports |
| `agent/evidence.py` | Keyword rule, or Claude behind `USE_LLM` | Turns evidence into risk signals that cite evidence ids |
| `agent/tools.py` | Read-only helpers | Seller views and memo drafting for the LLM. Never moves money |
| `api.py` | FastAPI app | `/health`, `/sellers`, `/settle`, `/reconciliation` |

The LLM never sets amounts and never holds Airwallex keys. Every number comes from deterministic code.

## Quick start

```bash
# 1. Install uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Create the venv and install locked dependencies
uv venv --python 3.12
uv sync

# 3. (Optional) activate the venv in your own shell
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows

# 4. Configure
cp .env.example .env

# 5. Run
uv run pytest
uv run python scripts/demo.py
uv run uvicorn reserveguard.api:app --reload   # http://127.0.0.1:8000/docs
```


## Developing with Claude Code + ECC

Project memory is in `CLAUDE.md`, and the ECC rules are in `.claude/rules/ecc/`. See [docs/WORKFLOW.md](docs/WORKFLOW.md)
for the loop: plan, then TDD, then verify, then review, then commit.

## Airwallex sandbox setup

1. Sign up for an Airwallex sandbox account at https://www.airwallex.com.
2. Create API keys under **Account > Developer** and put them in `.env`.
3. Email **devhelp@airwallex.com** to ask for platform (connected accounts) access.
4. When creating connected accounts in the sandbox, use the business name **"Sandbox Business"**
   and give each connected account an EIN.
5. `RestAirwallexClient` and `SandboxAdmin` are implemented and tested against mocks only. The API server and demo still use the fake until they are wired to real seller account ids.

## Demo scenario

USD, platform fee 10% of gross.

| Seller | Tier | Gross | Fee | Reserve rate | Reserve | Payout |
|---|---|---|---|---|---|---|
| A | trusted | $3,000 | $300 | 5% | $150 | $2,550 |
| B | standard | $3,000 | $300 | 10% | $300 | $2,400 |
| C | new | $4,000 | $400 | 15% | $600 | $3,000 |
| Total | | $10,000 | $1,000 | | $1,050 | $7,950 |

Per the hackathon's Kit 8 guide, the newest seller carries the highest reserve: the new tier's base rate is 15%, above standard (10%) and trusted (5%).

The scenario then continues for Seller C:

1. Evidence arrives: four "item never arrived" complaints (counted as one cluster, +15 points) and one
   tracking failure (+5 points). The rate becomes 35% and the reserve $1,400. That is a 20-point increase, so it needs approval.
2. After approval the shortfall is $800. C's wallet has $500, so ReserveGuard charges $500 and records $300 as debt.
3. The deliveries are confirmed, which clears the cluster. The rate drops to 20% and the target to $800. C holds $1,100, so the $300 debt
   is cancelled and $300 is released. C's final position: net paid $2,500 + released $300 + held $800 = $3,600.

All reconciliation checks pass at every step.

## Project structure

```
src/reserveguard/
  config.py  models.py  ledger.py  reserve_engine.py  settlement.py  scenario.py  api.py
  airwallex/{client,fake,money,rest,sandbox}.py
  agent/{evidence,tools}.py
tests/            pytest suite (unit + integration)
scripts/demo.py   demo scenario runner
data/samples/     sample evidence for Seller C
```

## Demo video

_Link coming soon._
