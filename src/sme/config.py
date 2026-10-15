"""Runtime configuration via environment variables."""

from __future__ import annotations

from typing import Optional

from pydantic import Field, SecretStr, field_validator, model_validator
from urllib.parse import quote
from pydantic_settings import BaseSettings, SettingsConfigDict

ALCHEMY_HOSTS = {
    "solana": "solana-mainnet", "base": "base-mainnet",
    "ethereum": "eth-mainnet", "arbitrum": "arb-mainnet",
    "bsc": "bnb-mainnet", "avalanche": "avax-mainnet",
    "robinhood": "robinhood-mainnet", "hyperevm": "hyperliquid-mainnet",
    "arc": "arc-mainnet",
}
ALCHEMY_ALIASES = {"sol": "solana", "eth": "ethereum", "arb": "arbitrum",
                   "avax": "avalanche", "hype": "hyperevm", "hyperliquid": "hyperevm"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_name: str = "smart-money-engine"
    app_env: str = "development"
    log_level: str = "INFO"
    admin_token: str = "change-me"

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/sme.db"

    # Queue / backpressure
    ingest_queue_size: int = 5000
    signal_queue_size: int = 1000
    drop_on_backpressure: bool = True

    # Provider timeouts / retries
    provider_timeout_seconds: float = 15.0
    provider_max_retries: int = 3
    provider_backoff_base: float = 0.5

    # Explicit provider selection; does not enable chains or change checkpoints.
    alchemy_api_key: SecretStr = Field(default=SecretStr(""), repr=False, exclude=True)
    alchemy_chains: str = "SOL,BASE,ETH,ARB,BSC,AVAX,ROBINHOOD,HYPE,ARC"
    alchemy_min_interval_seconds: float = Field(default=0.35, ge=0.05, le=60, allow_inf_nan=False)
    alchemy_rate_limit_cooldown_seconds: float = Field(default=2.0, ge=0.1, le=60, allow_inf_nan=False)

    # Finality
    solana_commitment: str = "confirmed"
    # Number of successor blocks required (not a guarantee of economic finality).
    evm_confirmation_depth: int = Field(default=3, ge=0)
    evm_max_blocks_per_cycle: int = Field(default=20, ge=1, le=1000)
    # Inclusive first block on a fresh database; None starts after safe head.
    ethereum_start_block: Optional[int] = Field(default=None, ge=0)
    bsc_start_block: Optional[int] = Field(default=None, ge=0)
    base_start_block: Optional[int] = Field(default=None, ge=0)
    robinhood_start_block: Optional[int] = Field(default=None, ge=0)

    # Solana
    solana_rpc_http: Optional[str] = Field(default=None, repr=False)
    solana_rpc_ws: Optional[str] = None
    solana_enabled: bool = True

    # Base (EVM)
    base_rpc_http: Optional[str] = Field(default=None, repr=False)
    base_rpc_ws: Optional[str] = None
    base_enabled: bool = True
    base_chain_id: int = 8453

    # BSC
    bsc_rpc_http: Optional[str] = Field(default=None, repr=False)
    bsc_rpc_ws: Optional[str] = None
    bsc_enabled: bool = True
    bsc_chain_id: int = 56

    # Other chains (config entries only — adapters stubbed as UNCONFIGURED)
    ethereum_rpc_http: Optional[str] = Field(default=None, repr=False)
    ethereum_enabled: bool = True
    arbitrum_rpc_http: Optional[str] = Field(default=None, repr=False)
    arbitrum_enabled: bool = False
    avalanche_rpc_http: Optional[str] = Field(default=None, repr=False)
    avalanche_enabled: bool = False
    robinhood_rpc_http: Optional[str] = Field(default=None, repr=False)
    robinhood_enabled: bool = True
    robinhood_chain_id: int = 4663
    sui_rpc_http: Optional[str] = None
    sui_enabled: bool = False
    injective_lcd: Optional[str] = None
    injective_enabled: bool = False
    monad_rpc_http: Optional[str] = None
    monad_enabled: bool = False

    # Endpoint preparation only; these chains do not yet have engine adapters.
    hyperevm_rpc_http: Optional[str] = Field(default=None, repr=False)
    arc_rpc_http: Optional[str] = Field(default=None, repr=False)
    hyperevm_enabled: bool = False
    arc_enabled: bool = False

    # Price / liquidity (optional)
    coingecko_api_key: Optional[str] = None
    dexscreener_enabled: bool = True
    price_cache_ttl_seconds: int = 30
    price_min_interval_seconds: float = 0.35
    jupiter_price_enabled: bool = False
    jupiter_price_url: Optional[str] = "https://api.jup.ag/price/v2"
    price_stale_after_seconds: int = 300
    price_poll_interval_seconds: float = 60.0

    # Signal thresholds
    min_signal_score: int = 40
    confluence_window_seconds: int = 3600
    confluence_min_wallets: int = 2
    whale_usd_threshold: float = 50_000.0


    # FOMO leaderboard (fomoapi.io — unofficial FOMO family data API)
    fomo_enabled: bool = True
    fomo_api_key: Optional[str] = Field(default=None, repr=False, exclude=True)
    fomo_leaderboard_window: str = "24h"  # 24h | 7d | 30d | all
    fomo_leaderboard_limit: int = 50
    fomo_refresh_seconds: float = 300.0  # re-fetch leaderboard every 5 min

    # Curated static lists (JSON files) — work without FOMO API key
    smart_money_list_path: Optional[str] = "data/smart_money.json"
    kol_list_path: Optional[str] = "data/kol.json"

    # Live automatic ingestion (wallet watchlist poller)
    # Comma-separated addresses. Required for auto alerts when chain is enabled.
    watch_wallets_solana: Optional[str] = None
    watch_wallets_base: Optional[str] = None
    watch_wallets_ethereum: Optional[str] = None
    watch_wallets_bsc: Optional[str] = None
    watch_wallets_robinhood: Optional[str] = None
    # Optional role map: addr:SMART_MONEY|KOL,addr2:WHALE
    watch_wallet_roles: Optional[str] = None
    poll_interval_seconds: float = 8.0
    poll_tx_limit: int = 15

    # Autonomous discovery: Solana co-activity and optional seedless EVM V2 scanning
    discovery_enabled: bool = False
    evm_discovery_enabled: bool = False
    evm_discovery_max_logs_per_block: int = Field(default=200, ge=1, le=5000)
    evm_discovery_min_hit_rate: float = Field(default=50.0, ge=0, le=100)
    discovery_max_candidates: int = 200
    discovery_max_new_per_day: int = 20
    discovery_candidate_ttl_days: int = 14
    discovery_min_evidence: int = 3
    discovery_max_promoted: int = 25
    discovery_max_watched_total: int = 100
    discovery_auto_promote: bool = False
    discovery_min_perf_samples: int = 3
    discovery_perf_hit_target: float = 2.0
    discovery_perf_primary_horizon: str = "1d"
    discovery_require_perf_for_promote: bool = False  # evidence-only promote unless true

    # Telegram
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_enabled: bool = False

    # Discord
    discord_webhook_url: Optional[str] = None
    discord_enabled: bool = False

    # Performance milestones (seconds)
    leaderboard_interval_seconds: float = 3600.0
    leaderboard_top_n: int = 10
    leaderboard_hit_target: float = 2.0

    performance_milestones: list[int] = Field(
        default_factory=lambda: [300, 900, 1800, 3600, 21600, 43200, 86400, 172800, 604800]
    )

    @field_validator("admin_token")
    @classmethod
    def _reject_insecure_default_in_prod(cls, v: str, info) -> str:
        # Soft check — hard reject happens at startup in production path
        return v

    @field_validator("alchemy_chains")
    @classmethod
    def _validate_alchemy_chains(cls, value: str) -> str:
        chains = {c.strip().lower() for c in value.split(",") if c.strip()}
        chains = {ALCHEMY_ALIASES.get(c, c) for c in chains}
        if chains - ALCHEMY_HOSTS.keys():
            raise ValueError("ALCHEMY_CHAINS supports " + ",".join(ALCHEMY_HOSTS))
        return ",".join(sorted(chains))

    @model_validator(mode="after")
    def _apply_alchemy(self):
        if self.hyperevm_enabled or self.arc_enabled:
            raise ValueError("HyperEVM/Arc endpoints are configured, but engine adapters are not implemented; keep their ENABLED flags false")
        key = self.alchemy_api_key.get_secret_value().strip()
        if not key:
            return self  # blank key keeps existing providers usable
        for chain in filter(None, self.alchemy_chains.split(",")):
            setattr(self, f"{chain}_rpc_http",
                    f"https://{ALCHEMY_HOSTS[chain]}.g.alchemy.com/v2/{quote(key, safe='')}")
        return self

    def is_production(self) -> bool:
        return self.app_env.lower() in ("production", "prod")

    def validate_security(self) -> None:
        if self.is_production() and self.admin_token in ("change-me", "", "admin", "secret"):
            raise ValueError(
                "ADMIN_TOKEN is insecure. Set a strong unique value before running in production."
            )


def get_settings() -> Settings:
    return Settings()
