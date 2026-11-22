# ZERO — Manual Required Implementations

Companion to `v2.0.MD`: everything that is **already implemented and verified**,
everything that is **not**, and the exact manual steps needed to finish or run
it on another machine.

Last reviewed: 2026-11-22 (integrity pass) · Engine schema `2.2`

---

## 1. Implemented and verified (no action needed)

| Area | Delivered | Evidence |
|---|---|---|
| Macro factor graph | 7 channels, provenance, rule audit, ±0.60% cap, confidence gate | `engine/v2/macro_factors.py`, panel → MACRO FACTOR GRAPH |
| Unified aggregation | quotes cache (60 s), institutional signals, composite per symbol | `engine/v2/aggregator.py` |
| Market structure | absorption / sliced / sweep / initiative events, Wyckoff phase, ATR levels | `engine/v2/market_structure.py` |
| Strategy lab | 4 momentum families, 20 specs, ATR stop + 6×ATR trail, gate unchanged | `engine/v2/strategy_lab.py`, `strategies/` |
| Validation | last-30% holdout + 5-fold consistency, reported beside the gate | `validate()` / `walk_forward()` |
| Research desks | macro, flow, sector, policy, risk memos | `engine/v2/research_agents.py` |
| MCP / REA bridge | stdio JSON-RPC client, tool catalogue (263 tools), task runner | `engine/v2/mcp_client.py`, `rea_bridge.py` |
| Integrity audit | 30/32 checks, 0 high, 93.8% | `python -m engine.v2 audit` |
| Session brief | once-per-day toast + banner, validated count | `engine/v2/session_brief.py` |
| Node override | `ZERO_NODE` / `ZERO_NODE_DIR`, PATH precedence, timeout env | `mcp_client.py`, `run.bat` |
| Tests | 16/16 v2, 6/6 calibrator, 10/10 news | `tests/` |
| Integrity pass | see §2.6 — 40/40 offline tests | `tests/test_integrity_fixes.py` |

---

## 2.6 Integrity pass (2026-11-22) — delivered

Three defects could manufacture an edge out of nothing. All three are fixed,
and the additions below exist so they cannot come back quietly.

| # | What was wrong | Fix | Files |
|---|---|---|---|
| 1 | `backend/main.py` served every `/api/v1` route with no authentication, `/auth/me` returned a hard-coded admin, `/auth/refresh` signed an access token from any string, `backend/auth.py` could not even be imported (`@app.post` with no `app`), and the signing key was `"your-secret-key-here"` | Pure auth library with env key, typed tokens (access ≠ refresh), `jti` revocation, role gates; every route takes `CurrentUser`/`TraderUser`; per-user scoping; CORS from config with credentials disabled while wildcard is on | `backend/auth.py`, `backend/main.py` |
| 2 | `load_market_frame` padded a short real series with a synthetic random walk and fell back to a fully synthetic frame; `pipeline.ensure_enough_bars` forced synthetic bars; nothing recorded which bars an exam used | Explicit provenance on every frame; promotion requires real + timestamped + OHLC-clean bars; synthetic only via `allow_synthetic=True` and can never pass; provenance stored on the exam result and on the bot | `engine/data_provenance.py`, `engine/aite/{exam,pipeline,models,portfolio}.py` |
| 3 | Portfolio correlation compared per-trade P&L by list position, and the book was padded to target with bots that had never passed an exam | Daily returns booked on trade exit dates on a shared calendar; correlation needs ≥20 shared days (else treated as redundant, not diversified); admission requires a passing exam on real data; no padding — a short book reports its shortfall | `engine/aite/portfolio.py` |

Also delivered (the roadmap items from the previous review):

| Addition | Command / entry point |
|---|---|
| Portfolio-level risk: book drawdown, vol, HHI, risk contribution, correlation matrix | `python -m engine.v2 risk`, `engine/v2/portfolio_risk.py` |
| PBO (CSCV) + Deflated Sharpe for lab selection bias | `python -m engine.v2 overfit`, `engine/v2/overfitting.py` |
| Real Indian cost stack wired into the lab (was a flat 12 bps proxy) | `python -m engine.v2 costs`, `engine/v2/costs.py` |
| Paper-forward shadow runner + reconciliation | `python -m engine.v2 shadow --record --score`, `engine/v2/shadow.py` |
| Alerting (webhook / Telegram / SMTP, deduped, audited) | `engine/v2/alerts.py` |
| Point-in-time Parquet bar store with `as_of()` and manifest | `data/pit_store.py` |
| Broker history adapters (Kite / Upstox / Dhan) + cross-source check | `engine/broker/historical.py` |
| Order state machine with idempotency, audit trail and reconciliation | `engine/broker/order_state.py` |
| Options-chain snapshotter (PCR, OI walls, IV skew) | `data/options_snapshot.py` |
| CI workflow + 24 new regression tests | `.github/workflows/integrity.yml`, `tests/test_integrity_fixes.py` |
| Audit gained 9 checks (provenance, timestamps, admission, API secret/CORS/guards, PIT store, broker history) | `python -m engine.v2 audit` |

### 2.6.1 The existing AITE book is not provable

`python -m engine.v2 provenance` on the checked-in state reports:
20/20 alive bots with no provenance record, 100% of 20,000 sampled trades with
`bar_<n>` timestamps (no usable calendar), and 20 bots in the portfolio against
1 recorded exam pass. That state predates provenance tagging, so it cannot be
repaired retroactively — it can only be re-derived. Recommended sequence:

```bash
# 1. keep the old state for the record
mv db/aite db/aite_pre_provenance

# 2. re-breed against real bars only (fails closed when a vendor is short)
python -m engine.aite.audit           # what the legacy book claims
python -m engine.aite.pipeline --help # see available entry points
```

Until that re-run happens, treat every existing survivor as unvalidated.

### 2.6.2 New environment variables

| Variable | Purpose | Default |
|---|---|---|
| `ZERO_SECRET_KEY` | JWT signing key, ≥32 chars. Required outside dev. | ephemeral in dev |
| `ZERO_ENV` | `development` / `production`; production refuses weak keys | `development` |
| `ZERO_CORS_ORIGINS` | comma-separated allow-list | wildcard (credentials off) |
| `ZERO_ALLOWED_HOSTS` | `TrustedHostMiddleware` allow-list | `*` |
| `ZERO_API_KEYS` | comma-separated API keys for tooling | unset (disabled) |
| `ZERO_AITE_ALLOW_SYNTHETIC` | test/demo only; synthetic cannot pass regardless | `0` |
| `ZERO_LAB_COST_MODEL` / `ZERO_LAB_SLIPPAGE_BPS` | `india` (default) or `bps` | `india` / `3` |
| `ZERO_PIT_DIR` | point-in-time store root | `db/pit` |
| `ZERO_KITE_API_KEY`, `ZERO_KITE_ACCESS_TOKEN` | Kite history | unset |
| `ZERO_UPSTOX_ACCESS_TOKEN` | Upstox history | unset |
| `ZERO_DHAN_ACCESS_TOKEN`, `ZERO_DHAN_CLIENT_ID` | Dhan history | unset |
| `ZERO_ALERT_WEBHOOK` / `ZERO_ALERT_TELEGRAM_*` / `ZERO_ALERT_SMTP_*` | alert channels | unset (log only) |

---

## 2. Manual — machine or account level

### 2.1 Machine-wide Node ≥ 24.22.1 (not done — blocked)
The app and REA use a portable Node 24.22.3 via `ZERO_NODE_DIR`, but
`C:\Program Files\nodejs\node.exe` is still 24.18.0 and the user PATH key is
policy-denied on this machine.

Manual step (needs an interactive UAC approval):

```powershell
winget upgrade --id OpenJS.NodeJS.24 --exact
node -v      # expect v24.22.3 or newer, in a NEW terminal
```

After that you may delete the portable runtime and remove `ZERO_NODE_DIR`
(user environment) and the `setup_node_runtime.ps1` line in `run.bat`.

### 2.2 Local REA checkout (optional)
`ZERO_REA_PATH` is no longer hard-coded (it was a personal path). The bridge
works without a checkout because it runs `npx rea-agents@6.10.1 mcp`.
Set it only if you want the panel to report the local package version:

```powershell
setx ZERO_REA_PATH "D:\path\to\REA"
```

### 2.3 Secrets (never committed)
Copy `.env.example` → `.env` and fill only what you use:
`GEMINI_API_KEY`, `HF_TOKEN`, broker credentials, `ZERO_API_KEY`.
`.env` is already git-ignored; `.env.example` is the tracked template.

### 2.4 Scheduler / always-on behaviour (not automated)
`run.bat` launches `setup_scheduler.bat` if present; nothing schedules the v2
loop yet. To run the daily job manually:

```powershell
python -m engine.v2 macro
python -m engine.v2 lab
python -m engine.v2 audit
python -m engine.v2 brief
```

If you want it scheduled, create a Task Scheduler entry pointing at those four
commands (a `.bat` wrapper is simplest). No alerting channel exists.

### 2.5 Docker build (unverified)
`docker compose config` was never executed — Docker is not installed here.
Install Docker Desktop, then:

```bash
docker compose config
docker build -f Dockerfile .
```

---

## 3. Product features NOT implemented (ranked backlog)

These were proposed and reviewed but deliberately not built. Each needs design
decisions, and several need data you do not currently have.

| # | Feature | Why it matters | Blocked on |
|---|---|---|---|
| 5 | **Regime-conditional gating** (use macro graph + market structure to allow/deny families) | Turns two display modules into an actual filter | needs trades per regime bucket |
| 6 | **REA evidence desk** (research memos cite inspected artifacts, allowlist + run log) | 263 tools are connected but only one generic task box uses them | a concrete verification target |
| 8 | **Decision journal / calibration scoring** | Would show whether the macro confidence number is informative | #5 |
| 9 | **Walk-forward parameter selection** (train window chooses parameters, test window scores them) | Current folds measure consistency, not selection; PBO/DSR now report the risk but do not re-select | #1 |
| 10 | **Capacity vs ADV** (participation cap, impact model) | The cost model charges per-trade charges but not market impact | needs ADV history |
| 11 | **Scheduler wiring for the shadow runner** | `shadow` exists but nothing runs it nightly yet | Task Scheduler entry (2.4) |

Explicitly **not** recommended yet: options/multi-asset expansion, ML model
layers, cloud hosting — all multiply surface area before the current edge has
forward evidence.

---

## 4. Repo / release tasks you must decide

1. **License** — `README.md` links a `LICENSE` badge, but no `LICENSE` file
   exists. Choose one (MIT/Apache-2.0/proprietary) and add it; this is a legal
   decision, so it was not invented here.
2. **CI** — `.gitignore` currently ignores `.github/`. If you want CI, remove
   that rule and add a workflow running
   `python tests/test_v2_engine.py` and `python -m engine.v2 audit`.
3. **Generated artifacts** — `strategies/*.md` and
   `strategies/index.json` are intentionally tracked so a clone shows results
   immediately. Re-running the lab rewrites them; commit those diffs or add
   `strategies/` to `.gitignore` if you prefer a clean tree.
4. **Local state is now ignored**: `zero_terminal.db`, `db/v2/rea_tools.json`,
   `db/v2/quotes_cache.json`, `db/v2/session_brief.json`, `db/v2/cache/`,
   `db/v2/research/`. They were untracked with `git rm --cached` and remain on
   disk.
5. **Portable Node directory** (`%LOCALAPPDATA%\Programs\node-v24.22.3\`) lives
   outside the repo — nothing to commit, but note it when moving machines.

---

## 5. Set up and run on another machine

```bash
# 1. Clone
git clone <your-repo-url> ZERO-Quantitative-Intelligence-Terminal
cd ZERO-Quantitative-Intelligence-Terminal

# 2. Python 3.11+ environment
python -m venv .venv
# Windows: .venv\Scripts\activate    POSIX: source .venv/bin/activate
pip install -r requirements.txt

# 3. Configuration (never commit .env)
cp .env.example .env      # fill only what you use

# 4. Optional: MCP / REA bridge
node -v                   # must satisfy ^22.23.4 || ^24.22.1 || >=26.12.1
# If it does not, install a side-by-side Node and set ZERO_NODE_DIR in .env,
# or run setup_node_runtime.ps1 on Windows.

# 5. Rebuild the strategy outputs (fast: history is cached for a day)
python -m engine.v2 lab
python -m engine.v2 audit

# 6. Run the terminal
streamlit run app.py          # or run.bat on Windows
```

Notes for a fresh clone:

* `db/v2/macro_inputs.json` and `institutional_signals.json` **are** tracked —
  they carry dated sources, so the macro graph works before any network call.
* The strategy docs are tracked; `python -m engine.v2 lab` regenerates them and
  rewrites `strategies/index.json`.
* Every panel action is lazy: nothing downloads until you press its button.
* Without network, quote/aggregation panels show "no cached quotes" rather than
  fabricated data.
