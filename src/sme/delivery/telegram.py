"""Telegram delivery — one original card + at most one editable performance reply."""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from sme.config import Settings
from sme.delivery.cards import build_performance_text, build_telegram_card

logger = logging.getLogger(__name__)


class TelegramDelivery:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Optional[httpx.AsyncClient] = None
        self.enabled = bool(
            settings.telegram_enabled
            and settings.telegram_bot_token
            and settings.telegram_chat_id
        )

    async def connect(self) -> None:
        if self.enabled:
            self._client = httpx.AsyncClient(timeout=20.0)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send_signal(self, signal: dict[str, Any]) -> Optional[str]:
        """Send original immutable signal card. Returns message_id or None."""
        if not self.enabled or not self._client:
            logger.info("Telegram disabled — signal not sent: %s", signal.get("signal_uid"))
            return None
        text = build_telegram_card(signal)
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                mid = str(data["result"]["message_id"])
                logger.info("Telegram original card sent msg_id=%s", mid)
                return mid
            logger.error("Telegram API error: %s", data)
            return None
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)
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
        reply_to_message_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Create performance reply once, then edit in place.
        Returns the performance message_id.
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
        token = self.settings.telegram_bot_token
        chat = self.settings.telegram_chat_id

        if existing_perf_message_id:
            url = f"https://api.telegram.org/bot{token}/editMessageText"
            payload = {
                "chat_id": chat,
                "message_id": int(existing_perf_message_id),
                "text": text,
                "parse_mode": "HTML",
            }
            try:
                resp = await self._client.post(url, json=payload)
                if resp.status_code == 200 and resp.json().get("ok"):
                    return existing_perf_message_id
                # Message deleted or uneditable — fall through to send new
                logger.warning("Telegram edit failed, will create new performance reply")
            except Exception as exc:
                logger.warning("Telegram edit error: %s", exc)

        # Create new performance message (reply to original signal card when known)
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_to_message_id:
            try:
                payload["reply_to_message_id"] = int(reply_to_message_id)
            except (TypeError, ValueError):
                pass
        try:
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                return str(data["result"]["message_id"])
        except Exception as exc:
            logger.error("Telegram performance send failed: %s", exc)
        return existing_perf_message_id


    async def send_text(self, text: str) -> Optional[str]:
        """Send plain/HTML text (leaderboard reports). Returns message_id or None."""
        if not self.enabled or not self._client:
            logger.info("Telegram disabled — text not sent")
            return None
        if not text:
            return None
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.settings.telegram_chat_id,
            "text": text[:4000],  # Telegram hard limit ~4096
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                return str(data["result"]["message_id"])
            logger.error("Telegram text send error: %s", data)
            return None
        except Exception as exc:
            logger.error("Telegram text send failed: %s", exc)
            return None
