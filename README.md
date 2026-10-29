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
| `store.py` | SQLite (standard library) | Live mode saves the ledger and all service state after every command, so a restart keeps reserves, pending approvals and settled cycles |
| `settlement.py` | Service object | Runs settlement cycles and reserve changes through `AirwallexClient` |
| `airwallex/fake.py` | In-memory fake | Balances, idempotency and insufficient-funds rules for tests and the demo |
| `airwallex/rest.py` | HTTP client (httpx) | Real Airwallex API: token login, major-unit amounts, `request_id` idempotency with lookup-before-retry, 429 backoff, status polling |
| `airwallex/sandbox.py` | Setup helpers (sandbox only) | Global account and deposit funding, seller onboarding (create → submit → ACTIVE), platform reports |
| `agent/evidence.py` | Keyword rule, or Claude behind `USE_LLM` | Turns evidence into risk signals that cite evidence ids |
| `agent/tools.py` | Read-only helpers | Seller views and memo drafting for the LLM. Never moves money |
| `api.py` | FastAPI app | `/health`, `/sellers`, `/settle`, `/sellers/{id}/assess` (evidence in, re-rate out; increases above the thresholds and every release wait for approval; rate-limited), `/sellers/{id}/approve`; money-moving calls return once their payments have SETTLED (502 with `ledger_applied` otherwise), `/reconciliation`; bearer token on all but `/health`; fake or real client per `USE_FAKE_AIRWALLEX` |

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

The API needs `RESERVEGUARD_API_TOKEN` set in `.env` (any long random string, e.g.
`python -c "import secrets; print(secrets.token_urlsafe(32))"`). Without it every endpoint
except `/health` returns 503. Send it as a bearer token:

```bash
curl -H "Authorization: Bearer $RESERVEGUARD_API_TOKEN" http://127.0.0.1:8000/sellers
```

### Where state is kept

In live mode (`USE_FAKE_AIRWALLEX=false`) the API saves everything to SQLite at
`RESERVEGUARD_DB_PATH` (default `data/reserveguard.db`, git-ignored) after every command, in one
transaction. That covers ledger rows (append-only, queryable with plain SQL), each seller's rate,
signals, pending approval and memos, settled cycles, payments still pending, and the opening
platform balance used by reconciliation.

- A restart keeps everything. Replaying a cycle never pays twice, and a pending approval is
  still waiting.
- On startup the stored seller-to-account mapping is checked. Sellers added since the last
  save start fresh, but if a stored seller's account changed or it is gone, the server refuses
  to start rather than mix or drop sellers' money.
- To start a fresh ledger, stop the server and delete `data/reserveguard.db*`.

Fake mode stays in memory, and `scripts/demo.py` uses a throwaway database for each run because
it is a scripted scenario. Run one server process at a time.

### Ops console

Open **http://127.0.0.1:8000/console** and enter the API token. The token is kept only in that
browser tab. From the console an operator can:

- run a settlement cycle;
- paste evidence or load the samples, then assess a seller;
- see **why** the rate is what it is: the tier base, each signal with its evidence, cleared
  signals, and the 40% cap;
- review the pending charge or release before anything moves (amount, from/to account, seller
  wallet, memo), then approve it in two clicks;
- read each seller's own ledger with its Airwallex payment ids, and watch the reconciliation
  checks.

The page is static HTML, CSS and JS with no build step. It uses the same authenticated
endpoints (`/sellers/{id}/explain`, `/sellers/{id}/pending`, `/sellers/{id}/statement`). It is
served with a strict Content-Security-Policy and writes every value as text, never as HTML.


## Developing with Claude Code + ECC

This project is built with [Claude Code](https://claude.com/claude-code) and the
[ECC](https://github.com/affaan-m/ECC) plugin. Shared permissions are in `.claude/settings.json`:
Claude may run `uv` and local `git` commands, but cannot read `.env`, run `pip install` or `git push`.

Every change follows the same loop:

1. **Plan**: `/ecc:plan "<feature>"`, then review and confirm the plan before any code is written.
2. **Test first**: write a failing test, implement until it passes, then refactor.
3. **Verify**: `uv run pytest`, `uv run ruff check --fix && uv run ruff format`, `uv run mypy src`.
4. **Review**: `/ecc:code-review` or `/ecc:python-review`, and fix blocking findings.
5. **Commit**: small conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).

A change is done when tests pass with at least 80% coverage, ruff and mypy are clean, and
`uv run python scripts/demo.py` passes every reconciliation check.

## Airwallex sandbox setup

1. Sign up for an Airwallex sandbox account at https://www.airwallex.com.
2. Create a restricted API key under **Account > Developer** with only these account
   permissions, and put the Client ID and key in `.env`:

   | Section | Permission | Access |
   |---|---|---|
   | Core Resources | Balances | Read |
   | Core Resources | Global Accounts | Read + Write |
   | Simulations | Simulations | Write |
   | Embedded Finance | Connected Accounts | Read + Write |
   | Embedded Finance | Transfers | Read + Write |
   | Embedded Finance | Charges | Read + Write |
   | Embedded Finance | Platform Reports | Read + Write |

   Leave global permissions and **Payouts → Transfers** (bank payouts) off.
3. Email **devhelp@airwallex.com** with your sandbox email and **Client ID**, asking for both
   **connected accounts** and **platform payments**. They are separate switches; without platform
   payments, `connected_account_transfers/create` and `charges/create` fail. Until then,
   onboarding fails with `This account cannot onboard business accounts`.
4. Onboard the demo sellers (each is a "Sandbox Business" with an EIN, auto-approved in the
   sandbox). This writes their account ids and tiers to `data/sandbox_sellers.json`
   (git-ignored), saving after each seller so a failed run resumes where it stopped
   (`--force` starts over):

   ```bash
   uv run python scripts/sandbox_setup.py
   ```

   If you already have sandbox accounts, copy `data/sandbox_sellers.example.json` to
   `data/sandbox_sellers.json` and fill in your account ids instead. In live mode the seller
   list comes from Airwallex (`GET /api/v1/accounts`): every `ACTIVE` account is a seller,
   named by its nickname. The file maps accounts to demo sellers A/B/C, can set a seller's
   `tier`, and lists `hidden` accounts to leave out. A seller without a tier is tiered by
   account age: under 90 days is new (15%), under 365 is standard (10%), and older is trusted
   (5%). If the account list cannot be loaded, the server still starts with the sellers of the
   saved ledger (or the file), logs a warning, and the console shows "using cached sellers".
5. Run the demo against the sandbox, or start the API with `USE_FAKE_AIRWALLEX=false`:

   ```bash
   uv run python scripts/demo.py --live
   ```

   The live run waits for every transfer and charge to settle before reconciling. Seller C
   keeps its whole payout in the sandbox, so step 2 charges the full shortfall instead of
   recording debt, so the amounts from step 2 on differ from the scenario below.

   The demo classifies evidence with Claude when `.env` has `USE_LLM=true`, and with the
   keyword rule otherwise; it prints which one it used. Set `USE_LLM=false` for a repeatable
   offline run.

The full flow has run end to end on the live sandbox: seller onboarding, payouts, the
shortfall charge and the reserve release all settled, and every reconciliation check passed.

After the ledger checks, the live demo checks every transfer and charge against Airwallex's own
records (same seller account, amount and currency, SETTLED). It also requests a
`TRANSACTION_RECON_REPORT` for the run. The sandbox can take a long time to build it, so the demo
prints its id to fetch later rather than failing.

If login fails with 401 or 403 although `.env` is right, check your shell for exported
`AIRWALLEX_*` variables: environment variables override `.env`.

The sandbox platform account starts with money that is not part of this ledger, so the
`platform_balance` check compares only what moved after the service started.

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

If C's next cycle settled while it still owed the $300, ReserveGuard would keep $300 of that
payout back as reserve and transfer the rest. A payout smaller than the debt is kept back in
full, and the remainder waits for the following cycle. Only C's own payout ever repays C's debt.

## Project structure

```
src/reserveguard/
  config.py  models.py  ledger.py  reserve_engine.py  settlement.py  scenario.py  api.py
  airwallex/{client,fake,money,rest,sandbox}.py
  agent/{evidence,tools}.py
tests/            pytest suite (unit + integration)
scripts/demo.py   demo scenario runner (--live for the Airwallex sandbox)
scripts/sandbox_setup.py  onboards sellers A, B, C in the sandbox
data/samples/     sample evidence for Seller C
```

## Demo video

_Link coming soon._
