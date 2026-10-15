"""Discord webhook delivery — one original embed + one editable performance follow-up."""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from sme.config import Settings
from sme.delivery.cards import build_discord_embed, build_performance_text, format_roi_label

logger = logging.getLogger(__name__)


class DiscordDelivery:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Optional[httpx.AsyncClient] = None
        self.enabled = bool(settings.discord_enabled and settings.discord_webhook_url)

    async def connect(self) -> None:
        if self.enabled:
            self._client = httpx.AsyncClient(timeout=20.0)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send_signal(self, signal: dict[str, Any]) -> Optional[str]:
        if not self.enabled or not self._client:
            logger.info("Discord disabled — signal not sent: %s", signal.get("signal_uid"))
            return None
        embed = build_discord_embed(signal)
        # wait=true so we get message id
        url = self.settings.discord_webhook_url
        if url and "?" not in url:
            url = url + "?wait=true"
        elif url and "wait=true" not in url:
            url = url + "&wait=true"
        try:
            resp = await self._client.post(url, json={"embeds": [embed]})
            resp.raise_for_status()
            data = resp.json()
            mid = str(data.get("id", ""))
            logger.info("Discord original embed sent id=%s", mid)
            return mid or None
        except Exception as exc:
            logger.error("Discord send failed: %s", exc)
            return None

    async def send_or_edit_performance(
        self,
        *,
        existing_perf_message_id: Optional[str],
        current_roi: Optional[float],
        peak_roi: Optional[float],
        current_price: Optional[float],
        entry_price: Optional[float],
        wallets_holding: Optional[int] = None,
    ) -> Optional[str]:
        """
        Performance update via Discord webhook.

        Create: POST webhook?wait=true → persist message id.
        Edit:   PATCH /webhooks/{id}/{token}/messages/{message.id}
                (supported by Discord for messages originally sent by that webhook).

        If edit fails (deleted message, auth), falls back to a new POST once.
        Not live-verified in this environment without DISCORD_WEBHOOK_URL.
        """
        if not self.enabled or not self._client:
            return existing_perf_message_id

        text = build_performance_text(
            current_roi=current_roi,
            peak_roi=peak_roi,
            current_price=current_price,
            entry_price=entry_price,
            wallets_holding=wallets_holding,
        )
        content = text.replace("<b>", "**").replace("</b>", "**").replace("<i>", "_").replace("</i>", "_")
        base = (self.settings.discord_webhook_url or "").split("?")[0]

        # Discord webhooks CAN edit their own messages:
        # PATCH /webhooks/{id}/{token}/messages/{message.id}
        if existing_perf_message_id and base:
            edit_url = f"{base}/messages/{existing_perf_message_id}"
            try:
                resp = await self._client.patch(edit_url, json={"content": content})
                if resp.status_code == 200:
                    logger.debug("Discord performance message edited id=%s", existing_perf_message_id)
                    return existing_perf_message_id
                logger.warning("Discord edit returned %s — will send new message", resp.status_code)
            except Exception as exc:
                logger.warning("Discord edit failed: %s — will send new message", exc)

        url = base + ("?wait=true" if base else "")
        try:
            resp = await self._client.post(url, json={"content": content})
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("id", "")) or None
        except Exception as exc:
            logger.error("Discord performance send failed: %s", exc)
            return existing_perf_message_id


    async def send_text(self, text: str) -> Optional[str]:
        """Send plain text (leaderboard reports). Returns message id or None."""
        if not self.enabled or not self._client:
            logger.info("Discord disabled — text not sent")
            return None
        if not text:
            return None
        url = self.settings.discord_webhook_url
        if url and "?" not in url:
            url = url + "?wait=true"
        elif url and "wait=true" not in url:
            url = url + "&wait=true"
        # Discord content limit 2000 chars
        content = text[:1900]
        try:
            resp = await self._client.post(url, json={"content": content})
            resp.raise_for_status()
            data = resp.json()
            return str(data.get("id", "")) or None
        except Exception as exc:
            logger.error("Discord text send failed: %s", exc)
            return None
