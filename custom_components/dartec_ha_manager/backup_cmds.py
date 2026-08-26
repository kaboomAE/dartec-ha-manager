"""Backups: in-home, and optionally copied offsite to Dartec.

Two independent layers, because they fail differently:

1. **In-home** — Home Assistant's own backup system (2025.1+). We can list,
   create, delete, and configure HA's *automatic* schedule and retention, so
   a home keeps protecting itself even if the manager is unreachable for
   months. This is the layer that matters most and costs the customer nothing.

2. **Offsite copy** — the agent streams a chosen backup to the manager. This
   is the layer that survives the house burning down, and it is optional
   because it moves the customer's whole configuration (and recorder history)
   onto our storage.

The upload streams in chunks rather than reading the archive into memory:
these files are routinely hundreds of megabytes and a home may be a
Raspberry Pi with 2 GB of RAM.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .ws_bridge import call_own_ws, mint_owner_token

_LOGGER = logging.getLogger(__name__)

UPLOAD_CHUNK = 1024 * 1024        # 1 MiB
DEFAULT_MAX_UPLOAD_MB = 2048


def _fail(msg: str) -> dict:
    return {"ok": False, "detail": msg}


def _error_text(result: dict, what: str) -> str:
    error = result.get("error") or {}
    return f"{what} failed: {error.get('message') or error.get('code') or 'unknown error'}"


def _summarise(backup: dict) -> dict:
    return {
        "backup_id": backup.get("backup_id"),
        "name": backup.get("name"),
        "date": backup.get("date"),
        "size": backup.get("size"),
        "protected": backup.get("protected"),
        "with_automatic_settings": backup.get("with_automatic_settings"),
        "agent_ids": list((backup.get("agents") or {}).keys()),
        "failed_agent_ids": backup.get("failed_agent_ids") or [],
    }


async def backup_list(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    result = await call_own_ws(hass, {"type": "backup/info"})
    if not result.get("success"):
        return _fail(_error_text(result, "backup list"))
    data = result.get("result") or {}
    backups = [_summarise(b) for b in (data.get("backups") or [])]
    backups.sort(key=lambda b: b.get("date") or "", reverse=True)

    agents = await call_own_ws(hass, {"type": "backup/agents/info"})
    agent_ids = [a.get("agent_id") for a in ((agents.get("result") or {}).get("agents") or [])]

    config = await call_own_ws(hass, {"type": "backup/config/info"})
    cfg = (config.get("result") or {}).get("config") or {}
    schedule = cfg.get("schedule") or {}

    return {
        "ok": True,
        "backups": backups,
        "agents": agent_ids,
        "automatic": {
            "configured": cfg.get("automatic_backups_configured"),
            "recurrence": schedule.get("recurrence"),
            "days": schedule.get("days"),
            "time": schedule.get("time"),
            "next": schedule.get("next_automatic_backup"),
            "retention": cfg.get("retention"),
            "agent_ids": (cfg.get("create_backup") or {}).get("agent_ids"),
        },
        "detail": f"{len(backups)} backup(s) on this home",
    }


async def backup_create(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Start a backup. HA runs it in the background — the reply means
    'accepted', not 'finished'; backup_list shows when it lands."""
    agents = await call_own_ws(hass, {"type": "backup/agents/info"})
    available = [a.get("agent_id") for a in ((agents.get("result") or {}).get("agents") or [])]
    if not available:
        return _fail("this home has no backup location configured")

    agent_ids = cmd.get("agent_ids") or available
    unknown = [a for a in agent_ids if a not in available]
    if unknown:
        return _fail(f"unknown backup location(s): {unknown}; available: {available}")

    payload: dict[str, Any] = {
        "type": "backup/generate",
        "agent_ids": agent_ids,
        "include_database": bool(cmd.get("include_database", True)),
        "include_homeassistant": True,
        "include_all_addons": bool(cmd.get("include_all_addons", True)),
        "include_folders": cmd.get("include_folders") or ["share", "ssl", "media"],
    }
    if cmd.get("name"):
        payload["name"] = str(cmd["name"])[:100]
    if cmd.get("password"):
        payload["password"] = cmd["password"]

    result = await call_own_ws(hass, payload, timeout=180)
    if not result.get("success"):
        return _fail(_error_text(result, "backup"))
    backup_id = (result.get("result") or {}).get("backup_job_id")
    return {"ok": True, "job_id": backup_id,
            "detail": f"backup started on {', '.join(agent_ids)} (runs in the background)"}


async def backup_delete(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    backup_id = cmd.get("backup_id")
    if not backup_id:
        return _fail("backup_id required")
    result = await call_own_ws(hass, {"type": "backup/delete", "backup_id": backup_id})
    if not result.get("success"):
        return _fail(_error_text(result, "backup delete"))
    return {"ok": True, "detail": "backup deleted"}


async def backup_schedule(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Configure Home Assistant's own automatic backups, so the home keeps
    protecting itself with no dependency on us."""
    recurrence = cmd.get("recurrence", "daily")
    if recurrence not in ("never", "daily", "custom_days"):
        return _fail("recurrence must be never, daily or custom_days")

    agents = await call_own_ws(hass, {"type": "backup/agents/info"})
    available = [a.get("agent_id") for a in ((agents.get("result") or {}).get("agents") or [])]
    agent_ids = cmd.get("agent_ids") or available
    if recurrence != "never" and not agent_ids:
        return _fail("this home has no backup location configured")

    schedule: dict[str, Any] = {"recurrence": recurrence}
    if recurrence == "custom_days":
        schedule["days"] = cmd.get("days") or ["sun"]
    if cmd.get("time"):
        schedule["time"] = cmd["time"]          # "HH:MM:SS"

    payload: dict[str, Any] = {
        "type": "backup/config/update",
        "automatic_backups_configured": recurrence != "never",
        "schedule": schedule,
        "create_backup": {"agent_ids": agent_ids,
                          "include_database": bool(cmd.get("include_database", True)),
                          "include_all_addons": True},
        "retention": {"copies": cmd.get("keep_copies", 3), "days": cmd.get("keep_days")},
    }
    result = await call_own_ws(hass, payload)
    if not result.get("success"):
        return _fail(_error_text(result, "backup schedule"))
    when = ("disabled" if recurrence == "never"
            else f"{recurrence}{' ' + str(schedule.get('days')) if recurrence == 'custom_days' else ''}"
                 f" at {cmd.get('time', 'HA default')}")
    return {"ok": True,
            "detail": f"automatic backups {when}, keeping {payload['retention']['copies']} copies"}


async def backup_upload(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Stream one backup from this home to the manager for offsite keeping."""
    backup_id = cmd.get("backup_id")
    upload_url = cmd.get("upload_url")
    upload_token = cmd.get("upload_token")
    if not (backup_id and upload_url and upload_token):
        return _fail("backup_id, upload_url and upload_token required")
    max_mb = int(cmd.get("max_mb") or DEFAULT_MAX_UPLOAD_MB)

    details = await call_own_ws(hass, {"type": "backup/details", "backup_id": backup_id})
    if not details.get("success"):
        return _fail(_error_text(details, "backup details"))
    backup = (details.get("result") or {}).get("backup") or {}
    size_mb = round((backup.get("size") or 0) / 1024 / 1024, 1)
    if size_mb > max_mb:
        return _fail(f"backup is {size_mb} MB, over the {max_mb} MB limit for offsite copies")

    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from .ws_bridge import base_url

    refresh, access = await mint_owner_token(hass)
    if refresh is None:
        return _fail(access)
    session = async_get_clientsession(hass)
    try:
        agent_id = (cmd.get("agent_id")
                    or next(iter((backup.get("agents") or {}).keys()), "backup.local"))
        download = f"{base_url(hass, ws=False)}/api/backup/download/{backup_id}?agent_id={agent_id}"
        async with session.get(download, ssl=False,
                              headers={"Authorization": f"Bearer {access}"},
                              timeout=3600) as src:
            if src.status != 200:
                return _fail(f"could not read the backup from Home Assistant (HTTP {src.status})")

            # Stream straight through: read a chunk, write a chunk. The whole
            # archive never sits in memory on either side.
            async def pump():
                async for chunk in src.content.iter_chunked(UPLOAD_CHUNK):
                    yield chunk

            async with session.post(upload_url, data=pump(), timeout=7200, headers={
                "X-Dartec-Token": upload_token,
                "X-Dartec-Backup-Id": str(backup_id),
                "X-Dartec-Backup-Name": str(backup.get("name") or backup_id)[:100],
                "X-Dartec-Backup-Date": str(backup.get("date") or ""),
                "Content-Type": "application/octet-stream",
            }) as dst:
                body = await dst.text()
                if dst.status >= 300:
                    return _fail(f"upload rejected by the manager (HTTP {dst.status}): {body[:200]}")
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("backup upload failed: %s", err)
        return _fail(f"upload failed: {err}")
    finally:
        hass.auth.async_remove_refresh_token(refresh)

    return {"ok": True, "size_mb": size_mb,
            "detail": f"copied '{backup.get('name')}' ({size_mb} MB) offsite"}


HANDLERS = {
    "backup_list": backup_list,
    "backup_create": backup_create,
    "backup_delete": backup_delete,
    "backup_schedule": backup_schedule,
    "backup_upload": backup_upload,
}
