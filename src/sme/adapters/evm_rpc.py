"""Bounded, read-only JSON-RPC transport. Errors never include provider text/URLs."""

import asyncio
import random

import httpx
from sme.adapters.rpc_pacing import rpc_post, retry_after_seconds


class EvmRpcError(RuntimeError):
    def __init__(self, message, *, contract_reverted=False):
        super().__init__(message)
        self.contract_reverted = contract_reverted


async def evm_rpc(client, url, method, params, settings):
    if method not in {
        "eth_chainId", "eth_blockNumber", "eth_getBlockByNumber",
        "eth_getLogs", "eth_getTransactionReceipt", "eth_call", "eth_getCode",
    }:
        raise EvmRpcError("unsupported read-only RPC method")
    retries = max(0, min(3, settings.provider_max_retries))
    for attempt in range(retries + 1):
        retry = False
        contract_reverted = False
        reason = "invalid_response"
        retry_after = 0.0
        try:
            response = await rpc_post(client, url, {
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
            }, settings)
            if response.status_code != 200:
                reason = f"HTTP_{response.status_code}"
                retry = response.status_code == 429 or response.status_code >= 500
                if response.status_code == 429:
                    retry_after = retry_after_seconds(response)
            else:
                data = response.json()
                if not isinstance(data, dict) or data.get("id") != 1:
                    reason = "invalid_envelope"
                elif "error" in data:
                    error = data["error"]
                    code = error.get("code") if isinstance(error, dict) else None
                    # Provider messages may contain credentials; never retain them.
                    reason = "rpc_error"
                    contract_reverted = method == "eth_call" and (code == 3 or (code == -32000 and isinstance(error.get("message"), str) and "execution reverted" in error["message"].lower()))
                    retry = code in (429, -32005, -32016, -32603)
                elif "result" in data:
                    return data["result"]
        except httpx.TransportError:
            reason, retry = "transport_error", True
        except (ValueError, TypeError):
            reason = "invalid_json"
        if not retry or attempt == retries:
            raise EvmRpcError(f"{method}: {reason}", contract_reverted=contract_reverted) from None
        delay = min(8.0, max(0.0, settings.provider_backoff_base) * 2**attempt)
        await asyncio.sleep(max(retry_after, delay + random.uniform(0, min(0.25, delay))))


def quantity(value):
    """Strict JSON-RPC quantity; reject null/boolean/negative values."""
    if not isinstance(value, str) or not value.startswith("0x"):
        raise EvmRpcError("invalid RPC quantity")
    try:
        result = int(value, 16)
    except ValueError:
        raise EvmRpcError("invalid RPC quantity") from None
    if result < 0:
        raise EvmRpcError("negative RPC quantity")
    return result
