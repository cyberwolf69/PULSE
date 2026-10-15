"""Shared enumerations."""

from __future__ import annotations

from enum import Enum


class ChainId(str, Enum):
    SOLANA = "solana"
    BSC = "bsc"
    BASE = "base"
    ROBINHOOD = "robinhood"
    SUI = "sui"
    INJECTIVE = "injective"
    ETHEREUM = "ethereum"
    ARBITRUM = "arbitrum"
    AVALANCHE = "avalanche"
    MONAD = "monad"


class ProviderHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    UNCONFIGURED = "UNCONFIGURED"


class TradeDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


class ConfirmationState(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FINALIZED = "FINALIZED"
    REORGED = "REORGED"


class PositionState(str, Enum):
    NO_POSITION = "NO_POSITION"
    INITIAL_ENTRY = "INITIAL_ENTRY"
    ACCUMULATING = "ACCUMULATING"
    HOLDING = "HOLDING"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    REDUCING = "REDUCING"
    FULL_EXIT = "FULL_EXIT"


class SignalLifecycle(str, Enum):
    DETECTED = "DETECTED"
    CONFIRMED = "CONFIRMED"
    STRENGTHENING = "STRENGTHENING"
    ACTIVE = "ACTIVE"
    WEAKENING = "WEAKENING"
    EXITING = "EXITING"
    CLOSED = "CLOSED"


class SignalPriority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class WalletRole(str, Enum):
    SMART_MONEY = "SMART_MONEY"
    KOL = "KOL"
    FOMO_TOP_TRADER = "FOMO_TOP_TRADER"
    DISCOVERED_WATCHLIST = "DISCOVERED_WATCHLIST"  # promoted candidate; not SM/KOL
    WHALE = "WHALE"


class SignalType(str, Enum):
    SMART_MONEY_BUY = "SMART_MONEY_BUY"
    SMART_MONEY_ACCUMULATION = "SMART_MONEY_ACCUMULATION"
    MULTIWALLET_CONFLUENCE = "MULTIWALLET_CONFLUENCE"
    KOL_ACTIVITY = "KOL_ACTIVITY"
    FOMO_TRADER_ACTIVITY = "FOMO_TRADER_ACTIVITY"
    WHALE_ACTIVITY = "WHALE_ACTIVITY"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    SMART_MONEY_DISTRIBUTION = "SMART_MONEY_DISTRIBUTION"
    FULL_EXIT = "FULL_EXIT"


class EnrichmentStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"
