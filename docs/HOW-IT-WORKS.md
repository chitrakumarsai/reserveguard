# How ReserveGuard works

## In one paragraph

ReserveGuard is a settlement agent for a marketplace that runs on Airwallex. When customers pay,
the money lands in the platform's account. ReserveGuard takes the platform's 10% fee, holds back
a safety reserve for each seller, and sends the rest to that seller's own Airwallex account.
How much it holds depends on how much we trust the seller. When new evidence arrives about one
seller, such as complaints that orders never arrived, ReserveGuard raises that seller's
reserve. If the seller's wallet can't cover the higher reserve, it collects the gap from them.
When deliveries are confirmed, it lowers the reserve and gives the money back. With `USE_LLM` on, Claude reads the messy evidence and
labels it as risk signals; with it off (the default), a simple keyword rule does the same job.
The explanation memos are filled in from a fixed template. Every dollar amount is worked out by
plain, fixed rules in code. Claude never decides an amount and never touches the API keys.

## The flow

1. **Airwallex accounts.** At startup we ask Airwallex for the platform's connected accounts.
   Every account marked `ACTIVE` becomes a seller, and its nickname is the seller's name.
2. **Tier assignment.** Each seller gets a tier. If our seller file sets one, we use it.
   Otherwise we go by account age: under 90 days is *new*, under a year is *standard*, and a
   year or more is *trusted*.
3. **Reserve.** The tier sets the starting reserve rate: trusted 5%, standard 10%, new 15%.
   Risk evidence can add points on top. A complaint adds 5, a cluster of complaints adds 15,
   and a tracking failure adds 5. The rate is capped at 40%. Confirmed deliveries clear the
   complaint cluster. A big jump waits for a human to approve it in the ops console.
4. **Net payout.** For each sale: gross, minus the 10% fee, minus the reserve, is what gets
   transferred to the seller's Airwallex account. For example, Seller A sells $3,000. We keep a
   $300 fee and a $150 reserve (5%), and transfer $2,550.

After every step we reconcile. Each seller's payouts, reserve and refunds must add up to what
they're owed. No seller's money ever covers another seller. And the platform balance must
match what the ledger says.

## The demo sellers

| Seller | Where it comes from | Tier | Starting reserve | Why |
|---|---|---|---|---|
| **A** | Live Airwallex account; tier pinned in `data/sandbox_sellers.json` | Trusted | 5% | All sandbox accounts are only days old, so by age everyone would be "new". We pin A to show the trusted tier. |
| **B** | Live Airwallex account; tier pinned in `data/sandbox_sellers.json` | Standard | 10% | Same reason, to show the middle tier. |
| **C** | Live Airwallex account; tier from its real age | New | 15% | Only days old, so the age rule really does make it new. C is the seller the risk story happens to. |
| Leftover "Seller A" | Live account, hidden by the seller file | — | — | A duplicate left over from an earlier setup run. It holds no ledger money, so we hide it rather than show two "Seller A"s. |

Any other `ACTIVE` account you add in the sandbox shows up automatically, with its tier set by
its age.

## Real vs. demo-only

**Real (live Airwallex sandbox):**
- The seller list, account IDs, names and account ages come from Airwallex's account list.
- Balances, transfers to sellers, and charges back from sellers are real sandbox API calls,
  and we wait for each one to settle.
- Every money call carries an idempotency key, so a retry after a timeout can't pay twice.
- The ledger is saved to a database, so a restart loses nothing.
- Airwallex's own reconciliation report for the 2026-10-29 live run lists all five money
  movements as SETTLED, with amounts matching our ledger. It's saved in
  `evidence/2026-10-29-sandbox-recon-report.csv`. That run was before the idempotency fix
  described below. The report for the run after the fix matches too, and is saved in
  `evidence/2026-10-30-sandbox-recon-report.csv`. Each report takes about 70 minutes to build in
  the sandbox, so live we show the instant per-payment checks instead.

**Demo-only:**
- The tiers for A and B are pinned by `data/sandbox_sellers.json`, not worked out from account
  age, because every sandbox account is brand new. C's tier is the only one the age rule sets.
- Customer sales are simulated amounts ($3,000, $3,000 and $4,000). We don't take real card
  payments.
- The risk evidence for Seller C (complaints, then delivery confirmations) is sample data we
  wrote.
- The 90-day and 1-year cut-offs, and the 5% / 10% / 15% rates, are sensible choices, not
  industry-calibrated figures.
- If the Airwallex account list can't be loaded at startup, we fall back to the last known
  sellers and show "using cached sellers" in the console. That's a demo safety net, not a
  production failover design.
- Without Airwallex keys, everything runs against a built-in fake Airwallex with the same three
  sellers.

## Bug caught by reconciliation

**What failed.** On 2026-10-30 we ran the live demo a second time. Two of 33 checks failed: the
platform's real Airwallex balance didn't match our ledger. All the payouts were fine, but
Seller C's reserve charge ($800) and release ($600) never actually happened. Airwallex sent back
the charge and release from the run on 2026-10-29: same IDs, no new money moved.

**The cause.** Every payment we send carries an idempotency key, so that if we retry after a
timeout, Airwallex recognises the retry and doesn't pay twice. The charge and release keys were
built only from the seller, the amount and the seller's position in the ledger. Each demo run
starts a fresh ledger, so the second run built exactly the same keys as the first. Airwallex
took that as a retry and handed back the old payment, and our ledger recorded money that never
moved. The reconciliation check is what caught it, which is exactly its job.

**The fix.** Every ledger now gets its own random ID, saved in its database, and that ID is part
of every payment key: payouts, charges and releases. A retry on the same ledger, even after a
restart, still sends the same key, so Airwallex still dedupes it. A new or reset ledger gets new
keys and can never pick up an old payment. A ledger saved before this change keeps its old keys,
so a payment it was in the middle of retrying can't go out twice.

**The test.** Two fresh ledgers against one fake Airwallex must produce separate payouts,
charges and releases, with no key used twice. Before the fix this test failed. Two more tests
check that a retry reuses its key, both in the same run and after a restart.

**Confirmed live.** After the fix we ran the live demo again on 2026-10-30. All 33 checks passed,
and the charge (`ch_nMTg0Ou5Pbeq7aCzwHE5bg`) and release (`tr_1bkF67DbP0WMmIja46S9Dg`) were new
payments, not the 2026-10-29 ones. Airwallex's reconciliation report for that run lists all five
movements as SETTLED with the same IDs and amounts as our ledger
(`evidence/2026-10-30-sandbox-recon-report.csv`).

## Known limitations

- Multi-page paging is unit-tested; not yet exercised against the live sandbox (needs 11+
  accounts; minimum page size is 10).

## Likely questions

**Does the AI decide how much money to hold?**
No. Claude only turns messy text, like customer complaints, into labelled risk signals, and
only when `USE_LLM` is on. Its labels are re-checked against the evidence, and if they don't
hold up we fall back to the keyword rule. Each signal's weight, the rates, the cap and every
dollar amount come from fixed rules in code that you can read and test. The rate is capped
at 40%. The memo is filled in from a fixed template, not written by Claude.

**What stops one seller's money being used for another?**
Each seller has their own Airwallex account, and the ledger tracks every seller separately.
Reconciliation checks after each step that no seller received, or was charged, more than
their own share. No decision ever reads another seller's balance. The only place sellers are
added up is the platform-balance check, which exists to catch exactly this kind of mix-up.
Startup refuses two sellers on one account, and a saved ledger won't load if a seller's
account has changed.

**What happens if the seller can't cover a higher reserve?**
We first try to charge the gap from the seller's Airwallex wallet. If their wallet is short,
the rest is recorded as debt the seller owes. When their reserve later comes down, that debt is
cancelled first, and only what's left over is released back to them. Until then, the debt is
taken out of the seller's own next payouts: that money stays in their reserve instead of being
transferred, and if one payout doesn't cover the debt, the next one continues. A later
reassessment can also charge it from their wallet.

**Why are A and B overridden instead of using their real age?**
The sandbox accounts were all created a few days ago, so the age rule would put everyone at the
new-seller rate. The override is one line per seller in a config file. C's tier comes from the
real age rule. A real marketplace with older sellers wouldn't need it.

**How do you know the money really moved?**
Each transfer and charge is checked against Airwallex's own record until it shows SETTLED,
against the right seller's account. On top of that, we fetch every payment back from Airwallex and check the amount, the seller's
account, the currency and the status against the ledger. Airwallex's reconciliation report for
both live runs matches our ledger line for line: the 2026-10-29 run, before the idempotency fix
above, and the 2026-10-30 run after it. The report takes about 70 minutes to build, so in a live
demo we show the per-payment checks.

*Has this ever caught anything?* Yes. The platform-balance check caught the bug where the second
run's $800 charge and $600 release never really happened (see "Bug caught by reconciliation").
