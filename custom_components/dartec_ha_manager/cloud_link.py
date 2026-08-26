"""Cloud link — the persistent outbound WebSocket from this Home Assistant
instance to the Dartec HA Manager cloud.

The home never opens inbound ports: the agent dials out, authenticates with
its pairing token, and pushes a snapshot every 60 seconds. Reconnects with
exponential backoff on any failure. Server->agent commands (service calls,
dashboard pushes) will arrive on this same socket in a later milestone.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .collector import collect_snapshot
from .commands import execute_command
from .const import RECONNECT_MAX_S, RECONNECT_MIN_S, SNAPSHOT_INTERVAL_S

_LOGGER = logging.getLogger(__name__)


class CloudLink:
    def __init__(self, hass: HomeAssistant, server_url: str, pairing_token: str) -> None:
        self._hass = hass
        self._ws_url = server_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://") + "/agent/ws"
        self._token = pairing_token
        self._task: asyncio.Task | None = None
        self._stopping = False

    def start(self) -> None:
        self._task = self._hass.async_create_background_task(self._run(), name="dartec_cloud_link")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        backoff = RECONNECT_MIN_S
        session = async_get_clientsession(self._hass)
        while not self._stopping:
            try:
                async with session.ws_connect(self._ws_url, heartbeat=30) as ws:
                    await ws.send_json({"type": "auth", "token": self._token})
                    reply = await ws.receive_json()
                    if reply.get("type") != "auth_ok":
                        _LOGGER.error("Dartec cloud rejected pairing token: %s", reply.get("reason"))
                        return  # bad token will not fix itself — stop, user must re-pair
                    _LOGGER.info("Connected to Dartec HA Manager cloud")
                    backoff = RECONNECT_MIN_S
                    await self._snapshot_loop(ws)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                _LOGGER.warning("Dartec cloud link lost (%s); retrying in %ss", err, backoff)
            except asyncio.CancelledError:
                raise
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_S)

    async def _snapshot_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Send a snapshot every cycle while handling server-pushed commands.

        The sender and reader run as separate tasks on one socket: acks are
        consumed silently, commands are executed and answered immediately, so
        a command arriving mid-sleep is not mistaken for an ack.
        """
        send_lock = asyncio.Lock()

        async def sender() -> None:
            while not self._stopping:
                snapshot = await collect_snapshot(self._hass)
                async with send_lock:
                    await ws.send_json({"type": "snapshot", "data": snapshot})
                await asyncio.sleep(SNAPSHOT_INTERVAL_S)

        async def reader() -> None:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                data = msg.json()
                if data.get("type") == "command":
                    result = await execute_command(self._hass, data)
                    async with send_lock:
                        await ws.send_json({"type": "command_result", "id": data.get("id"), **result})
                    # push a fresh snapshot right away so the dashboard reflects the outcome
                    snapshot = await collect_snapshot(self._hass)
                    async with send_lock:
                        await ws.send_json({"type": "snapshot", "data": snapshot})
            raise aiohttp.ClientError("socket closed by server")

        sender_task = asyncio.create_task(sender())
        try:
            await reader()
        finally:
            sender_task.cancel()
