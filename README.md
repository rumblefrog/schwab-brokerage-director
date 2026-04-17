# Schwab Brokerage Director

A **Monday Portfolio Advisor** driven by a [Claude Code Routine]. Every Monday
evening, a Claude agent runs on Anthropic's infrastructure, pulls your Schwab
holdings via SnapTrade, reads market context via yfinance, consults a sticky
target allocation, picks one underweight category, and posts a single
$500-deposit recommendation to a Discord webhook.

You **execute the trade manually** in the Schwab app on Tuesday morning. The
routine never places orders. It only tells you where to put the money.

**This is a personal-use tool. Not financial advice. LLMs can be wrong.
Sanity-check every recommendation before trading.**

[Claude Code Routine]: https://claude.ai/code/routines

---

## How it works

```
Monday 6pm ET
     │
     ▼
┌────────────────────────── Claude Code Routine ─────────────────────────┐
│                                                                        │
│  1. python scripts/fetch_holdings.py    → SnapTrade → /tmp/…           │
│  2. python scripts/market_context.py    → yfinance → /tmp/…            │
│  3. Read config/target_allocation.yaml  ← sticky strategic anchor      │
│  4. (Optional) web_search for 2–3 news items relevant to the decision  │
│  5. Apply the decision algorithm in CLAUDE.md §7                       │
│  6. Write recommendations/YYYY-MM-DD.md                                │
│  7. python scripts/post_discord.py recommendations/YYYY-MM-DD.md       │
│  8. Commit recommendation on branch claude/recommendation-YYYY-MM-DD   │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
     │
     ▼
 Discord embed on your phone
     │
     ▼
 You open Schwab, enter a $500 fractional-share order for the ticker.
```

The routine's "brain" is [`CLAUDE.md`](CLAUDE.md) — that file is its program.
The Python scripts are thin, mechanical tools the agent calls.

---

## Setup

### 1. One-time SnapTrade setup (local)

[SnapTrade] is a broker aggregator with a free personal-use tier that holds the
Schwab OAuth refresh on their side — this solves Schwab's 7-day token-expiry
problem.

1. Sign up at <https://dashboard.snaptrade.com/> and grab your `clientId` and
   `consumerKey`.
2. Install deps: `uv sync`.
3. Run the setup script:
   ```
   SNAPTRADE_CLIENT_ID=... SNAPTRADE_CONSUMER_KEY=... \
       uv run python scripts/setup_snaptrade.py
   ```
4. The script prints a Connection Portal URL. Open it, pick Charles Schwab,
   log in on Schwab's own page (SnapTrade never sees your Schwab credentials),
   authorize read-only access.
5. The script then prints the 5 env vars you'll need in the next step. **Copy
   them to a safe place — the `userSecret` cannot be recovered if lost.**

[SnapTrade]: https://snaptrade.com/

### 2. Claude Code Routine setup (web, one-time)

Requires a Claude Pro or Max plan.

1. **Enable Claude Code on the web** at <https://claude.ai/code>.
2. **Connect GitHub.** At <https://claude.ai/settings/connectors>, install the
   GitHub connector and grant access to this repository.
3. **Create a custom environment.** At <https://claude.ai/settings> →
   Environments tab:
   - **Environment variables** (5): `SNAPTRADE_CLIENT_ID`,
     `SNAPTRADE_CONSUMER_KEY`, `SNAPTRADE_USER_ID`, `SNAPTRADE_USER_SECRET`,
     `DISCORD_WEBHOOK_URL`. You will have to explicitly accept the "not yet
     encrypted" caveat — see [Secrets caveat](#secrets-caveat) below.
   - **Setup script:** `uv sync`.
   - **Network access:** unrestricted (reaches SnapTrade, yfinance, Discord).
4. **Create the routine** at <https://claude.ai/code/routines>:
   - **Repository:** this repo, default branch.
   - **Environment:** the one you just created.
   - **Trigger:** Schedule → Weekly → **Monday 6:00 PM ET (22:00 UTC)**.
     Evening schedule avoids the ACH-settlement race — see
     [Why Monday evening?](#why-monday-evening) below.
   - **Prompt** (short; the real instructions are in `CLAUDE.md`):

     > Read `CLAUDE.md` and follow the weekly workflow. Produce this week's
     > recommendation, write it to `recommendations/YYYY-MM-DD.md`, post it to
     > Discord via the script, and commit the recommendation file.
5. **Manual first run.** Trigger the routine manually from the Routines page
   (the equivalent of `workflow_dispatch`). Verify it:
   - Produces `recommendations/YYYY-MM-DD.md`.
   - Pushes to a `claude/recommendation-YYYY-MM-DD` branch.
   - Posts a green-sidebar embed to Discord.
6. Review the output on your phone. If the formatting looks good and the
   reasoning makes sense, merge the branch to `main`. Your routine is live.

### 3. Discord webhook

In your server → Edit channel → Integrations → Webhooks → New Webhook → Copy
URL. Paste into the `DISCORD_WEBHOOK_URL` env var in step 2.3.

---

## Local iteration

The fastest way to iterate on `CLAUDE.md` phrasing without burning routine
quota is the fixture-based dry-run:

```
uv run python scripts/local_dry_run.py
```

This chains `fetch_holdings.py --dry-run` + `market_context.py --dry-run`,
renders a mechanical-stub recommendation at `recommendations/DRY-RUN.md`
(gitignored), and prints the would-be Discord embed to stdout. No live APIs
are touched.

The dry-run's reasoning paragraph is a deterministic placeholder — the real
reasoning only happens inside a live Claude routine session. Use the dry-run
to exercise the plumbing (parse, truncate, post), not the reasoning.

### Tests

```
uv run pytest tests/ -v
```

All unit tests run offline with mocked SDKs.

---

## Re-link playbook

If the Discord embed is **red** with headline "Schwab connection needs
re-linking", SnapTrade has lost Schwab's OAuth. To recover:

```
uv run python scripts/relink_snaptrade.py
```

This prints a fresh Connection Portal URL. Open it, re-authorize Schwab. The
routine's existing env vars continue to work — you do **not** need to update
anything in the Claude Code environment.

---

## Quarterly strategic review

The target allocation in [`config/target_allocation.yaml`](config/target_allocation.yaml)
is **sticky**. The routine never modifies it. Every quarter (or whenever the
routine's `## Caveats` notes accumulate suggestions worth acting on), review
and re-derive it manually:

1. Pull the latest `main` and read the last ~12 `recommendations/*.md` for
   strategic notes accumulating in `## Caveats`.
2. Delete `config/target_allocation.yaml`.
3. Trigger a one-off routine run with a prompt like:

   > The strategic allocation has been removed. Derive a new one based on my
   > current holdings and profile in `CLAUDE.md`. Commit it to a
   > `claude/target-init` branch for my review. Do not proceed to a normal
   > weekly recommendation.

4. Review the branch on GitHub. Merge to `main` if you agree; edit and merge
   if not.

---

## Secrets caveat

Claude Code environment variables are **not yet encrypted at rest**. For this
project the scope is bounded:

- SnapTrade credentials grant **read-only** access to positions/balances. They
  cannot place trades.
- The Discord webhook URL is revocable at any time from Discord server
  settings.

If that threat model doesn't work for you, don't run this. You can revoke the
SnapTrade user at <https://dashboard.snaptrade.com/> and delete the Discord
webhook with one click.

---

## Why Monday evening?

A 9am Monday run risked allocating against stale cash — ACH deposits land
Monday morning but may not be settled/visible in SnapTrade's `cash_usd` field
until later in the day. A 22:00 UTC (6pm ET) run sees the deposit and
deploys against settled cash.

If your deposit arrives on a different day, adjust the schedule accordingly.
`CLAUDE.md` explicitly states the runtime assumption: "deploy whatever
`cash_usd` is present at run-time."

---

## What's in the repo

```
CLAUDE.md                        # routine operating instructions (the brain)
PLAN.md                          # phased implementation roadmap
config/target_allocation.yaml    # strategic anchor — sticky, user-owned
scripts/
  fetch_holdings.py              # SnapTrade → normalized JSON
  market_context.py              # yfinance → prices, returns, VIX, yields
  post_discord.py                # markdown → Discord embed
  setup_snaptrade.py             # one-time interactive local setup
  relink_snaptrade.py            # re-auth recovery script
  local_dry_run.py               # fixture-based E2E simulator
tests/
  test_fetch_holdings.py
  test_market_context.py
  test_post_discord.py
  test_setup_snaptrade.py
  test_local_dry_run.py
  fixtures/
    example_recommendation.md    # canonical output format — single source of truth
    holdings.json                # normalized holdings shape
    holdings_raw.json            # SnapTrade-shaped raw response
    market_context.json          # normalized market-context shape
    yfinance_raw.json            # minimal per-ticker history for yfinance mocks
recommendations/                 # committed weekly by the routine; DRY-RUN.md ignored
```

---

## Out of scope (v1)

- Options, futures, crypto
- Individual-stock recommendations (WPM is grandfathered; the tool won't
  suggest new single stocks)
- Tax-loss harvesting
- Multi-account aggregation
- Automated order placement — the routine posts to Discord and stops

---

## Disclaimer

**This is personal-use software. It is not financial advice. Large language
models can produce plausible-sounding errors. Verify every recommendation
against your own understanding before placing a trade. You are responsible
for your portfolio; this tool is not.**
