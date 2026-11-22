# ZERO — Manual Required Implementations

Companion to `v2.0.MD`: everything that is **already implemented and verified**,
everything that is **not**, and the exact manual steps needed to finish or run
it on another machine.

Last reviewed: 2026-11-22 · Engine schema `2.1`

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
| 1 | **Portfolio-level risk** (combined equity curve, correlation between the 14 passers, capital allocation) | Each strategy's 12% DD is per strategy; the combined book can draw down more than any single one | nothing — pure code |
| 2 | **Paper-forward shadow runner + reconciliation** | The only way to know the edge still exists after the backtest window | scheduler (2.4) |
| 3 | **Real Indian cost stack** (brokerage, STT, exchange, stamp, GST, volatility-scaled slippage, capacity vs ADV) | Current model is a flat 12 bps per leg; real charges differ by product | product/broker choice |
| 4 | **Second data source + bar integrity** (NSE bhavcopy or broker historical, adjustment and gap checks) | All prices come from yfinance; one bad adjustment silently invalidates a backtest | a data source + credentials |
| 5 | **Regime-conditional gating** (use macro graph + market structure to allow/deny families) | Turns two display modules into an actual filter | needs trades per regime bucket |
| 6 | **REA evidence desk** (research memos cite inspected artifacts, allowlist + run log) | 263 tools are connected but only one generic task box uses them | a concrete verification target |
| 7 | **Alerting** (Telegram/SMTP on regime flip, new pass, audit regression, drawdown breach) | The terminal only speaks when you open it | channel choice + secrets |
| 8 | **Decision journal / calibration scoring** | Would show whether the macro confidence number is informative | #5 |
| 9 | **Walk-forward parameter selection** (train window chooses parameters, test window scores them) | Current folds measure consistency, not selection | #1 |

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
