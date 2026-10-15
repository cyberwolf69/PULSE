# PULSE + R4ISE Office

## Multichain Smart Money Tracking System

<p align="center">
  <b>On-chain wallet intelligence with automatic candidate discovery, signal evaluation, persistent tracking, and a local monitoring dashboard.</b>
</p>

<p align="center">
  Wallet Discovery · EVM Polling · Solana Tracking · Telegram &amp; Discord Signals · R4ISE Dashboard
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/RPC-Alchemy-363FF9" alt="RPC: Alchemy">
  <img src="https://img.shields.io/badge/Tracking-Multichain-238636" alt="Tracking: Multichain">
  <img src="https://img.shields.io/badge/Signals-Discord-5865F2?logo=discord&amp;logoColor=white" alt="Signals: Discord">
  <img src="https://img.shields.io/badge/Alerts-Telegram-26A5E4?logo=telegram&amp;logoColor=white" alt="Alerts: Telegram">
  <img src="https://img.shields.io/badge/Dashboard-R4ISE-red" alt="Dashboard: R4ISE">
</p>

<p align="center"><b>Tracking supported</b></p>

<p align="center">
  <a href="#chain-support"><img src="https://img.shields.io/badge/Ethereum-627EEA?style=for-the-badge" alt="Ethereum — Tracking supported"></a>
  <a href="#chain-support"><img src="https://img.shields.io/badge/Base-0052FF?style=for-the-badge" alt="Base — Tracking supported"></a>
  <a href="#chain-support"><img src="https://img.shields.io/badge/BNB%20Smart%20Chain-F0B90B?style=for-the-badge" alt="BNB Smart Chain — Tracking supported"></a>
  <a href="#chain-support"><img src="https://img.shields.io/badge/Solana-9945FF?style=for-the-badge" alt="Solana — Tracking supported"></a>
  <a href="#chain-support"><img src="https://img.shields.io/badge/Robinhood-00C805?style=for-the-badge" alt="Robinhood — Tracking supported"></a>
</p>
<p align="center"><b>Endpoints configured · Tracking pending</b></p>

<p align="center">
  <a href="#chain-support"><img src="https://img.shields.io/badge/Arbitrum-28A0F0?style=for-the-badge" alt="Arbitrum — Endpoints configured; Tracking pending"></a>
  <a href="#chain-support"><img src="https://img.shields.io/badge/Avalanche-E84142?style=for-the-badge" alt="Avalanche — Endpoints configured; Tracking pending"></a>
  <a href="#chain-support"><img src="https://img.shields.io/badge/HyperEVM-247F68?style=for-the-badge" alt="HyperEVM — Endpoints configured; Tracking pending"></a>
  <a href="#chain-support"><img src="https://img.shields.io/badge/Arc-555555?style=for-the-badge" alt="Arc — Endpoints configured; Tracking pending"></a>
</p>

---

## Overview

PULSE monitors supported blockchain activity, discovers wallet candidates, evaluates observed token performance, and generates signals when configured rules are satisfied. R4ISE Office provides a local visual dashboard for the engine's activity and health.

The system combines blockchain polling, trade decoding, wallet evidence, price observations, and configurable notification delivery in one Python application.

Core capabilities include:

- Automatic wallet candidate discovery on Ethereum, Base, BSC, and Robinhood
- Confirmed EVM block polling with persistent checkpoints
- Seeded wallet tracking on Solana
- Performance-gated promotion into the discovered watchlist
- Telegram and Discord signal delivery
- Local engine telemetry through the R4ISE Office dashboard

PULSE does not require an LLM API key or a wallet private key, and it does not execute exchange orders or submit blockchain transactions.

Ethereum, Base, BSC, and Robinhood share the EVM polling pipeline. Solana retains its existing wallet-based polling path. Four additional Alchemy endpoints are configurable; their tracking status is detailed in [Chain Support](#chain-support).

---

## Contents

- [Overview](#overview)

- [Quick Start](#quick-start)
- [Installation on Windows](#installation-on-windows)
- [Configuration Guide](#configuration-guide)
- [Chain Support](#chain-support)
- [Core Workflow](#core-workflow)
- [Automatic Wallet Discovery](#automatic-wallet-discovery)
- [Notifications](#notifications)
- [Running the Application](#running-the-application)
- [Dashboard](#dashboard)
- [Checkpoints, Restart & Recovery](#checkpoints-restart--recovery)
- [Daily Operating Workflow](#daily-operating-workflow)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

### 1. Open the project folder

Extract `PULSE-R4ISE.zip`, then open the extracted `PULSE-R4ISE` folder in VS Code using **File → Open Folder**. Open **Terminal → New Terminal** inside that folder.

If you already have a configured local `pulse_work` folder, use that folder directly. You do not need to extract another copy to run it.

Requirements:

- Python **3.11 or newer** with `pip` and `venv`.
- Internet access for dependency installation and live provider calls.
- An Alchemy API key with access to the chains you intend to use.
- A writable project directory for the SQLite database.

Check Python on macOS or Linux:

```bash
python3 --version
```

### 2. Create and activate a virtual environment

Run these commands from the directory containing `run.py` and `pyproject.toml`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

Dependencies are declared in `pyproject.toml`. The distribution does not need a separate `requirements.txt`.

### 3. Create your configuration

**For a new installation only:**

```bash
cp .env.example .env
```

**If `.env` already exists, keep it.** Copying the template over it would replace your saved API key and settings. There is one configuration template: `.env.example`. The application reads your local `.env`.

Open `.env` in VS Code and replace the empty value:

```dotenv
ALCHEMY_API_KEY=YOUR_ALCHEMY_API_KEY
FOMO_API_KEY=YOUR_FOMOAPI_IO_KEY
```

The template enables Ethereum, Base, BSC, Solana, Robinhood, and FOMO. Alchemy starts the configured chain pollers when a key is available; FOMO supplies wallet addresses when its own key is present. Discovery is enabled; performance-gated automatic promotion remains an explicit opt-in. Notification delivery remains disabled.

Set `ADMIN_TOKEN` to your own random value instead of `change-me`. You can generate a value locally with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Keep `.env` private. You do not need to paste your API key into the README, source files, or browser dashboard.

### 4. Check configuration

```bash
python run.py --check
```

This prints a redacted configuration summary without making network requests. A successful check confirms that configuration can be loaded; it does **not** verify provider credentials, quotas, or live tracking.

### 5. Start the engine and dashboard

For a bounded initial run:

```bash
python run.py --seconds 60
```

Open **http://127.0.0.1:8765** while the process is running. Inspect the terminal for provider errors and the dashboard for engine activity. Sixty seconds is a connectivity smoke test, not enough to establish wallet performance or long-term reliability.

For continuous operation:

```bash
python run.py
```

Stop with **Ctrl+C**. The process runs in the foreground; closing the terminal, stopping the process, or putting the computer to sleep interrupts monitoring.

---

## Installation on Windows

Open PowerShell in the project folder:

```powershell
py --version
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
```

Create `.env` only if it does not already exist:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

After entering your configuration:

```powershell
python run.py --check
python run.py
```

If PowerShell prevents activation, use the virtual environment's Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe run.py --check
.\.venv\Scripts\python.exe run.py
```

---

## Configuration Guide

Restart the application after editing `.env` so the new configuration is loaded.

### Alchemy access

```dotenv
ALCHEMY_API_KEY=YOUR_ALCHEMY_API_KEY
ALCHEMY_CHAINS=SOL,BASE,ETH,ARB,BSC,AVAX,ROBINHOOD,HYPE,ARC
ALCHEMY_MIN_INTERVAL_SECONDS=0.35
ALCHEMY_RATE_LIMIT_COOLDOWN_SECONDS=2.0
```

PULSE builds the selected Alchemy mainnet RPC URLs from your key. For the standard Alchemy setup, you do not need to enter a base URL for each chain.

`ALCHEMY_CHAINS` selects endpoint configuration. It does **not** enable every chain or create missing pollers. Actual access also depends on the provider's support and your account permissions.

The shared limiter spaces Alchemy request starts and coordinates rate-limit cooldowns within one process. It does not coordinate other programs using your account or measure your entire account's compute-unit budget.

### Enable supported chains

The default tracking set is:

```dotenv
ETHEREUM_ENABLED=true
BASE_ENABLED=true
BSC_ENABLED=true
SOLANA_ENABLED=true
ROBINHOOD_ENABLED=true
FOMO_ENABLED=true
```

Explicitly setting an enable flag to `false` still disables that chain. Solana can receive its seed wallets automatically from FOMO; a manual list is optional when FOMO returns valid Solana addresses. Without credentials or wallets, an enabled component may remain in standby. Enabled status alone is not proof of received transactions.

### Settings reference

These are the values supplied by the distribution template, not necessarily the values in an existing `.env`.

| Setting | Template value | Purpose |
|---|---|---|
| `ALCHEMY_API_KEY` | Empty | Your provider credential |
| `ETHEREUM_ENABLED` | `true` | Start Ethereum polling |
| `BASE_ENABLED`, `BSC_ENABLED`, `SOLANA_ENABLED`, `ROBINHOOD_ENABLED` | `true` | Start implemented pollers when RPC is configured |
| `EVM_CONFIRMATION_DEPTH` | `3` | Stay behind the latest EVM block by the configured depth |
| `EVM_MAX_BLOCKS_PER_CYCLE` | `20` | Bound EVM block processing per cycle |
| `POLL_INTERVAL_SECONDS` | `8` | Polling interval setting |
| `PROVIDER_TIMEOUT_SECONDS` | `15` | Provider request timeout |
| `PROVIDER_MAX_RETRIES` | `3` | Retry budget for retryable provider failures |
| `PROVIDER_BACKOFF_BASE` | `0.5` | Retry backoff base |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/sme.db` | Local persistent state |
| `DEXSCREENER_ENABLED` | `true` | Enable token price enrichment |
| `PRICE_POLL_INTERVAL_SECONDS` | `60` | Price observation interval |
| `FOMO_ENABLED` | `true` | Import leaderboard wallets when `FOMO_API_KEY` is present |
| `TELEGRAM_ENABLED`, `DISCORD_ENABLED` | `false` | Explicit delivery switches |
| `APP_ENV` | `development` | Environment setting |
| `LOG_LEVEL` | `INFO` | Logging level |
| `ADMIN_TOKEN` | `change-me` | Replace with a private random value |

Confirmation depth is a lag policy, not a guarantee of economic finality. Poll cycles can take longer than their interval when requests, retries, or discovery work are slow.

---

## Chain Support

| Chain | Alchemy selector | Enable setting | Tracking scope in this build |
|---|---|---|---|
| Ethereum | `ETH` | `ETHEREUM_ENABLED` | Shared EVM poller; watched-wallet decoding; V2 candidate discovery |
| Base | `BASE` | `BASE_ENABLED` | Shared EVM poller; watched-wallet decoding; V2 candidate discovery |
| BNB Smart Chain | `BSC` | `BSC_ENABLED` | Shared EVM poller; watched-wallet decoding; V2 candidate discovery |
| Solana | `SOL` | `SOLANA_ENABLED` | Existing seeded-wallet poller and co-activity discovery |
| Arbitrum | `ARB` | `ARBITRUM_ENABLED` | Endpoint/adapter preparation; no complete live polling pipeline |
| Avalanche | `AVAX` | `AVALANCHE_ENABLED` | Endpoint/adapter preparation; no complete live polling pipeline |
| Robinhood | `ROBINHOOD` | `ROBINHOOD_ENABLED` | Shared EVM poller, chain ID 4663; watched-wallet V2/V3 decoding; verified V2 candidate discovery |
| HyperEVM | `HYPE` | `HYPEREVM_ENABLED` | Endpoint preparation only; enable guard rejects unsupported tracking |
| Arc | `ARC` | `ARC_ENABLED` | Endpoint preparation only; enable guard rejects unsupported tracking |

Keep Arbitrum, Avalanche, HyperEVM, and Arc disabled. A successful RPC response proves endpoint access only. `HYPE` refers to HyperEVM here; it does not provide HyperCore trading data.

---

## Core Workflow

```text
.env configuration + persistent database
                   |
                   v
Enabled chain pollers
   |                                  |
   | ETH / Base / BSC / Robinhood      | Solana
   v                                  v
Chain identity + confirmed blocks      Seeded wallet activity
   |                                  |
   +-------------+--------------------+
                 |
                 v
Durable ingestion -> decoding -> deduplication -> stored observations
                 |
                 +--> Watched-wallet activity -> signal rule evaluation
                 |                                  |
                 |                                  v
                 |                        Qualified signal / delivery queue
                 |                                  |
                 |                                  v
                 |                        Enabled Telegram / Discord channel
                 |
                 +--> Candidate evidence -> price observations
                                             |
                                             v
                                  Performance and capacity gates
                                             |
                                             v
                                  Optional watchlist promotion

R4ISE Office reads local engine telemetry throughout the workflow.
```

Receiving a transaction does not guarantee a decoded trade. A decoded trade does not guarantee a signal, and a discovered candidate does not immediately become a watched wallet.

---

## Automatic Wallet Discovery

### Do I need to enter wallets manually?

For **Ethereum, Base, BSC, and Robinhood**, seedless discovery can scan supported V2 activity without an initial wallet list:

```dotenv
DISCOVERY_ENABLED=true
EVM_DISCOVERY_ENABLED=true
```

For **Solana**, seed wallets can come automatically from FOMO or from a local list. Its poller does not perform seedless network-wide discovery. Optional comma-separated manual watchlists are supported:

```dotenv
WATCH_WALLETS_ETHEREUM=
WATCH_WALLETS_BASE=
WATCH_WALLETS_BSC=
WATCH_WALLETS_ROBINHOOD=
WATCH_WALLETS_SOLANA=
```

Enter actual addresses for the relevant chain. Static role lists are provided at `data/smart_money.json` and `data/kol.json`; preserve their existing JSON schema when editing them.

### Candidate discovery versus automatic tracking

The template records candidates but leaves automatic promotion off:

```dotenv
DISCOVERY_AUTO_PROMOTE=false
```

To allow qualified candidates to enter the discovered watchlist automatically, set:

```dotenv
DISCOVERY_AUTO_PROMOTE=true
DISCOVERY_REQUIRE_PERF_FOR_PROMOTE=true
DISCOVERY_MIN_EVIDENCE=3
DISCOVERY_MIN_PERF_SAMPLES=3
DISCOVERY_PERF_PRIMARY_HORIZON=1d
DISCOVERY_PERF_HIT_TARGET=2.0
EVM_DISCOVERY_MIN_HIT_RATE=50
```

With these settings, promotion requires enough evidence, completed performance samples at the configured horizon, the required hit rate, and available capacity. The target `2.0` means a 2× token-price target. A one-day horizon requires completed observations; a short smoke test cannot establish eligibility.

Promoted wallets receive the `DISCOVERED_WATCHLIST` role. Promotion does not certify them as `SMART_MONEY` or `KOL`. Watchlist refresh and subsequent matching activity are still needed before later signals can occur.

Capacity controls:

| Setting | Template value |
|---|---:|
| `EVM_DISCOVERY_MAX_LOGS_PER_BLOCK` | 200 |
| `DISCOVERY_MAX_CANDIDATES` | 200 |
| `DISCOVERY_MAX_NEW_PER_DAY` | 20 |
| `DISCOVERY_MAX_PROMOTED` | 25 |
| `DISCOVERY_MAX_WATCHED_TOTAL` | 100 |

### Discovery coverage and performance meaning

Seedless EVM discovery conservatively validates supported V2 pools and direct wallet-to-pool ERC-20 payment/receipt legs. It does not discover every DEX trade: native-coin paths without the required ERC-20 evidence, ambiguous multihop/aggregator paths, and V3/V4 discovery are outside this path's coverage. Watched-wallet decoding has separate V2/V3 support.

DexScreener enrichment supplies observed prices where available. Missing or stale prices leave evidence insufficient; historical entry prices are not invented. Performance measures subsequent observed token-price appreciation, not realized wallet profit or a guarantee of trading skill.

### Is a FOMO API key required?

**No.** Supported EVM discovery and manual watchlists can operate without it:

```dotenv
FOMO_ENABLED=false
FOMO_API_KEY=
```

FOMO is enabled by default in this release. Set `FOMO_API_KEY` to your **fomoapi.io** key to import wallets automatically; the snippet above shows how to explicitly disable it. A missing key leaves the integration in `missing_key` standby without requesting the leaderboard.

The client reads `/v2/leaderboard/24h` and imports Solana addresses plus EVM addresses for each enabled EVM polling chain. An EVM address is a monitoring target on those chains, not evidence that its owner has traded on every chain. Imported wallets receive `FOMO_TOP_TRADER`, not a verified smart-money classification. Actual trades must still be observed and decoded through RPC.

FOMO status distinguishes missing/invalid keys, exhausted credits, rate limits, malformed responses, and successful imports. Malformed rows are skipped without stopping valid rows. The default registry refresh interval is 300 seconds (`FOMO_REFRESH_SECONDS`); authenticated leaderboard reads consume provider credits. Increase this interval to reduce usage. See the [FOMO API reference](https://fomoapi.io/docs) for its current contract and credit policy.

Robinhood uses the [official network configuration](https://docs.robinhood.com/chain/connecting/), [canonical WETH/USDG contracts](https://docs.robinhood.com/chain/contracts/), and [Uniswap V2 factory deployment](https://developers.uniswap.org/docs/protocols/v2/deployments). Native-only and V4 paths remain outside the existing ERC-20 V2/V3 decoder coverage.

---

## Notifications

Leave notifications disabled during initial validation. If you enable a channel, the application can send messages when its delivery rules are satisfied.

### Telegram

```dotenv
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
TELEGRAM_CHAT_ID=YOUR_TARGET_CHAT_ID
```

Use a bot and destination you control. The bot must have permission to post to the destination.

### Discord

```dotenv
DISCORD_ENABLED=true
DISCORD_WEBHOOK_URL=YOUR_DISCORD_WEBHOOK_URL
```

Use a webhook for the intended channel. Treat its URL as a secret. You can enable either delivery channel independently.

No message after startup is not, by itself, a failure: there may be no qualified signal. Check ingestion, decoding, signal evaluation, and delivery errors separately.

---

## Running the Application

Run commands from the project folder with the virtual environment active.

| Command | Behavior |
|---|---|
| `python run.py --check` | Load and print redacted configuration; no provider requests |
| `python run.py --demo` | Run the simulated office without starting the engine |
| `python run.py --seconds 60` | Start engine and dashboard, then stop after the specified duration |
| `python run.py` | Run engine and dashboard until stopped |
| `python run.py --port 8766` | Use another local dashboard port |
| `python run.py --help` | Show available startup options |

The installed `sme-office` command is also available. `run.py` explicitly uses its own project directory for configuration and relative paths, making it the recommended distribution entry point.

---

## Dashboard

Default address: **http://127.0.0.1:8765**.

The dashboard displays local engine telemetry, module activity, counters, and chain capability/health information. It binds to the loopback interface and exposes read-only local data. Its chain summary adds no extra RPC probes.

- **LIVE** means the UI is observing the local engine process. It is not proof that every chain is receiving mainnet transactions.
- Office characters represent instrumented engine components. Animation and cycle counts are not transaction counts or independent AI agents.
- Endpoint availability, enabled status, poller implementation, and observed activity are separate states.
- FOMO shows provider status (including missing/invalid keys, credits and rate limits) and aggregate wallet-chain targets. One shared EVM wallet can count once on each enabled chain; these are monitoring targets, not confirmed trades.
- Each chain shows its registry wallet count. Robinhood capability follows the current engine poller.
- Discovery shows enablement, EVM discovery, auto-promotion configuration and the engine counters for EVM observations and Solana additions. Scout observes candidate recording; Cipher observes evaluation and promotion attempts.
- A stale heartbeat indicates loss of current telemetry; inspect the process and terminal output.

**The dashboard's visual demo control does not stop an already running engine.** Use the startup command `python run.py --demo` when you want simulation without engine activity, or stop the live process with Ctrl+C.

---

## Checkpoints, Restart & Recovery

PULSE stores processing state in the database selected by `DATABASE_URL`. Keep this database when restarting or moving an installation.

The shared EVM pipeline validates chain identity, processes confirmed blocks sequentially, journals pending work, and persists checkpoints. Durable ingestion acknowledges work after database commit; deduplication prevents replayed observations from becoming duplicate stored trades.

On a fresh database with no explicit start block, polling begins after the confirmed head observed at startup. It does not automatically scan the chain's entire history.

Optional `ETHEREUM_START_BLOCK`, `BASE_START_BLOCK`, `BSC_START_BLOCK`, and `ROBINHOOD_START_BLOCK` settings are for deliberate backfills. Do not insert arbitrary heights to fix an empty dashboard. Large backfills can consume substantial provider capacity, and a stored checkpoint governs resumption.

If a checkpoint hash or chain identity does not match, treat the failure as a recovery issue. The pipeline can block processing rather than silently accepting inconsistent history. Preserve the database and logs, confirm the intended network, and investigate before changing state.

### Back up or move an installation

1. Stop the running process with Ctrl+C.
2. Copy `.env` securely and preserve the complete `data/` directory. If `DATABASE_URL` points elsewhere, back up that database too.
3. Keep any SQLite sidecar files with the database if present.
4. Put the new source files in a separate folder and install dependencies there.
5. Restore your configuration and data without replacing them with template values.
6. Run `python run.py --check`, then a bounded live run before resuming continuous operation.

Run only one engine against the same local state. The shared Alchemy limiter is process-local, so starting duplicate copies also increases provider pressure.

---

## Daily Operating Workflow

1. Activate the virtual environment and confirm that you are in the intended project folder.
2. Review enabled chains and keep incomplete chains disabled.
3. Run `python run.py --check` after configuration changes.
4. Start `python run.py` and open the dashboard.
5. Monitor provider errors, confirmed-block progress, ingestion, and decoded observations.
6. Allow candidate evidence and price samples to accumulate before expecting performance-based promotion.
7. Enable delivery only when you want qualifying signals sent to your configured destinations.
8. Stop cleanly before backups, upgrades, or moving the project.

The application does not install a background service or configure automatic startup. Continuous monitoring requires the process to remain running and the computer to remain awake.

---

## Troubleshooting

| Symptom | What to check |
|---|---|
| `python` is missing or too old | Check `python3 --version` on macOS/Linux or `py --version` on Windows; use Python 3.11+ |
| `ModuleNotFoundError` | Activate the correct `.venv` and run `python -m pip install .` from the project folder |
| `.env` is missing | For a new installation, copy `.env.example` to `.env`; hidden-file settings may hide it in your file manager |
| Chain still shows `false` after adding a key | The key configures access; set the supported chain's `*_ENABLED=true` and restart |
| RPC authentication or permission errors | Check the key locally and the provider's chain/account permissions; `--check` cannot validate them |
| HTTP 429 or provider rate-limit errors | Reduce enabled chains or workload, increase request spacing, and check other programs sharing the account |
| Dashboard works but has no trades | Confirm live mode, enabled poller, new confirmed blocks, relevant activity, and decoder coverage |
| No automatically watched wallets | Check `DISCOVERY_AUTO_PROMOTE`, evidence, completed price samples, hit rate, and capacity limits |
| Solana has no activity | Check FOMO status and returned Solana wallets, or provide a manual seed list; confirm supported activity occurs on those wallets |
| No Telegram/Discord messages | Check channel flags, credentials/permissions, qualified signals, and delivery logs |
| Port 8765 is already used | Stop the previous instance or use `python run.py --port 8766`; avoid duplicate engines |
| Resume fails on a checkpoint mismatch | Preserve state and investigate the network/block history before making changes |
| Data appears to reset | Confirm the intended project copy and `DATABASE_URL`; a different database starts with different state |
