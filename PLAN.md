# Monday Portfolio Advisor — Implementation Plan

## Context

This repo is driven by a **Claude Code Routine** running weekly on Anthropic's
infrastructure. The routine — not any standalone script — is the advisor:
every Monday it pulls Schwab holdings via SnapTrade, reads market context via
yfinance, consults a sticky target allocation, and posts a $500-deposit
recommendation to a Discord webhook.

The repo is not an "app." It's a **program that a weekly Claude agent
executes**. The most consequential artifact is `CLAUDE.md` (the routine's
operating instructions). Scripts, configs, and tests exist only to give the
weekly agent reliable tools and unambiguous rules.

## Decisions captured

1. **SWPPX/VTI overlap** → Flagged once in the allocation; then **starve SWPPX
   of new deposits** via `frozen_tickers: [SWPPX]` inside `us_equity_broad`.
   VTI is the sole buy-target. Never sell SWPPX (avoids taxable gains).
2. **Bond sleeve** → **0%**. SGOV models `cash_dry_powder`, not bonds. 20+ yr
   horizon + moderate-aggressive risk justifies all-equity-plus-commodities.
3. **Precious metals** → Target **9%** (8–10% band). `primary_ticker: GDX`;
   `frozen_tickers: [WPM]` (single-stock concentration already in place).
4. **Target allocation is committed directly**, not derived at first run. No
   first-run protocol; no `claude/target-init` branch dance. Quarterly
   re-derivation is a manual user operation.

## Target allocation

Committed at `config/target_allocation.yaml`. Summary:

| Category           | Target % | Primary ticker | Frozen           |
|--------------------|----------|----------------|------------------|
| us_equity_broad    | 55       | VTI            | [SWPPX]          |
| us_equity_growth   |  7       | QQQM           |                  |
| intl_equity        | 22       | VXUS           |                  |
| precious_metals    |  9       | GDX            | [WPM]            |
| cash_dry_powder    |  7       | SGOV           |                  |

`drift_tolerance_pct: 2` · `rebalance_via_sell_threshold_pct: 10` ·
`rebalance_horizon_weeks: 20` · `whitelist: []`.

**Frozen tickers** count toward category weight for drift but never receive
deposits. `primary_ticker` is the sole buy-target when the category is
underweight.

## Phased roadmap

Each phase ends with tests green + fixture dry-run + diff review before the
next phase.

### Phase 1 — CLAUDE.md + config + fixtures + canonical recommendation format

- `CLAUDE.md` — the routine's brain.
- `config/target_allocation.yaml` — committed strategic anchor.
- `tests/fixtures/example_recommendation.md` — canonical markdown format;
  single source of truth for both CLAUDE.md's output spec and
  `post_discord.py`'s parser.
- `tests/fixtures/{holdings,market_context,holdings_raw,yfinance_raw}.json`.
- `.env.example`, `.gitignore`, `pyproject.toml`, `.claude/settings.json`.

### Phase 2 — `scripts/fetch_holdings.py`

- Thin wrapper over `snaptrade-python-sdk`.
- Normalizes to `{as_of, total_usd, cash_usd, positions:[…]}`.
- Applies `category_map` from yaml.
- `--dry-run` reads `holdings_raw.json`.
- Exit code 2 on SnapTrade auth failure.
- Tests: mock SDK; verify normalization, category aggregation, auth-failure
  exit path.

### Phase 3 — `scripts/market_context.py`

- yfinance → per-ticker returns, VIX, 10y yield, index proxies, SPDR sectors.
- Per-indicator retry with backoff; graceful skip + `warnings:[…]`.
- `--dry-run` reads `market_context.json` passthrough.
- Tests: mock yfinance; verify partial-failure handling.

### Phase 4 — `scripts/post_discord.py`

- Parses the canonical frontmatter + six-section format → Discord embed.
- Colors from `kind` frontmatter: `buy` green, `warning` yellow, `alert` red.
- **Discord embed limit handling.** Truncation priority (always preserve in
  order): headline → recommendation → reasoning → caveats → market context →
  drift → positions. Truncated sections get `… (truncated — see
  recommendations/YYYY-MM-DD.md)`.
- Fractional-share line rendered from `share_estimate` + `price_used`.
- Retry once on 5xx; log + exit on 4xx.
- Tests: parse `example_recommendation.md`; verify color mapping, retry
  policy; **oversized fixture** exercises truncation + 6,000 char cap.

### Phase 5 — `scripts/setup_snaptrade.py` + `scripts/relink_snaptrade.py`

- One-time interactive setup → Connection Portal URL → Schwab OAuth.
- Relink script: just the login step for the red-alert recovery path.

### Phase 6 — `scripts/local_dry_run.py`

- End-to-end fixture simulator. Calls `--dry-run` modes of fetch_holdings +
  market_context, renders a stub recommendation, posts to `mock://` Discord.
- Developer's inner loop for iterating on CLAUDE.md.

### Phase 7 — README

- Plain-language summary.
- SnapTrade setup walkthrough.
- Claude Routine 6-step setup (Pro/Max plan → claude.ai/code → GitHub
  connector → custom environment → routine creation → manual first run).
  **Schedule: Monday 6pm ET (22:00 UTC)** — after ACH settlement, so
  `cash_usd` reflects this week's deposit.
- Secrets caveat (env vars not yet encrypted).
- Re-link playbook.
- Quarterly strategic review procedure.
- Disclaimer.

## Verification

Per phase:

1. `uv run pytest tests/ -v` — green.
2. `uv run python scripts/local_dry_run.py` — produces `recommendations/DRY-RUN.md`
   + logs the would-be Discord embed.
3. Diff review.

End-to-end (post-Phase 7):

1. Local SnapTrade setup.
2. Claude Code environment configured with 5 env vars.
3. Routine scheduled Monday 22:00 UTC.
4. Manual first run produces `recommendations/YYYY-MM-DD.md` on a
   `claude/recommendation-*` branch + posts green-sidebar embed.
5. Mobile Discord formatting sanity check.

## Out of scope (v1)

Options, futures, crypto; individual-stock suggestions (WPM grandfathered);
tax-loss harvesting; multi-account aggregation; any automated order placement.

## Risks

### Primary

- **CLAUDE.md phrasing is the product.** The reasoning happens inside the
  weekly Claude session — no Python code covers it. Budget iteration time in
  the first month. Diff `recommendations/*.md` week-over-week; tighten
  CLAUDE.md when the agent drifts.
- **SnapTrade vendor lock-in.** Free-tier aggregator, no SLA. Fallback is a
  manually-maintained positions file matching `holdings.json`'s shape —
  documented so SnapTrade isn't an existential dependency.
- **Claude mid-run re-deriving the allocation.** Anti-whipsaw contract in
  CLAUDE.md §4 is the only defense. Any drift into yaml edits is a CLAUDE.md
  phrasing bug and warrants immediate tightening.

### Secondary

- **yfinance flakiness** → per-indicator retry + graceful skip + Caveats note.
- **SnapTrade auth expiry** → exit code 2 + red alert + `relink_snaptrade.py`.
- **Secrets in Claude env (unencrypted)** → accepted given read-only scope +
  revocable webhook.
- **ACH settlement timing** → evening schedule + runtime-cash assumption.
- **Discord embed overflow** → truncation priority + oversized fixture test.
