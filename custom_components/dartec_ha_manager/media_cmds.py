"""Media staging — putting a file a blueprint needs into the home.

The athan blueprint plays `media-source://media_source/local/...`. That file
has to exist on the home, and asking an installer to copy an mp3 by hand is
the per-home manual step blueprints exist to remove.

**The file comes from the manager, not from the internet.** Same shape as
`backup_upload` in reverse: the server issues a short-lived token and a URL on
itself, and this streams from there. A customer's house needing outbound
access to GitHub (or anywhere else) to finish an install is a dependency worth
not having, and it also means every home gets byte-identical content.

**It is written through Home Assistant's own upload API**, not into the
filesystem. `media_dirs` is configurable, HAOS and Container disagree about
where `/media` is, and HA validates the filename and content type. Writing the
path ourselves would be guessing at all three.

Three guards, enforced here rather than trusted from the cloud:

* one folder, `dartec/` — a customer's own media is out of reach
* a size cap, checked while streaming, so a wrong URL cannot fill a disk
* a SHA-256 the server states up front and this verifies before uploading

Nothing here deletes. There is no path to remove a file from a home.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

FOLDER = "dartec"
LOCAL_ROOT = "media-source://media_source/local"
# HA's own limit is 20 MiB (media_source/local_source.py::MAX_UPLOAD_SIZE).
# Matching it means an oversized file fails here, while streaming, instead of
# after we have already pulled it all down.
DEFAULT_MAX_MB = 20
CHUNK = 256 * 1024

_FILENAME = re.compile(r"^[a-z0-9][a-z0-9_-]*\.(mp3|ogg|wav|flac|m4a|mp4|png|jpg|jpeg)$")


def normalise_filename(name: str) -> str | None:
    """A plain lowercase filename with a known media extension, or None.

    HA validates filenames too, but this runs first and is narrower: the point
    is that a compromised cloud cannot choose an arbitrary name, not that the
    name is merely legal.
    """
    name = (name or "").strip()
    if "/" in name or "\\" in name or ".." in name:
        return None
    return name if _FILENAME.match(name) else None


def target_id(filename: str) -> str:
    return f"{LOCAL_ROOT}/{FOLDER}/{filename}"


async def media_upload(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """Fetch one file from the manager and hand it to HA's media upload."""
    import aiohttp
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .ws_bridge import base_url, call_own_ws, mint_owner_token

    filename = normalise_filename(cmd.get("filename") or "")
    if filename is None:
        return {"ok": False,
                "detail": f"refused filename '{cmd.get('filename')}' — must be a "
                          "plain lowercase media filename"}
    url, token = cmd.get("download_url"), cmd.get("download_token")
    if not (url and token):
        return {"ok": False, "detail": "download_url and download_token required"}
    expected = (cmd.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return {"ok": False, "detail": "a sha256 of the file is required"}
    max_bytes = int(cmd.get("max_mb") or DEFAULT_MAX_MB) * 1024 * 1024

    # Already there? Uploading again would be a wasted transfer per home and,
    # once a blueprint is pointing at the file, a needless rewrite of something
    # the customer may have replaced with their own recording.
    browsed = await call_own_ws(hass, {"type": "media_source/browse_media",
                                       "media_content_id": f"{LOCAL_ROOT}/{FOLDER}"})
    if browsed.get("success"):
        children = (browsed.get("result") or {}).get("children") or []
        if any((c or {}).get("media_content_id") == target_id(filename) for c in children):
            return {"ok": True, "media_content_id": target_id(filename), "uploaded": False,
                    "detail": f"{filename} is already on this home"}

    session = async_get_clientsession(hass)
    payload = bytearray()
    digest = hashlib.sha256()
    try:
        async with session.get(url, headers={"X-Dartec-Token": token},
                               timeout=aiohttp.ClientTimeout(total=600)) as resp:
            if resp.status != 200:
                return {"ok": False,
                        "detail": f"the manager returned HTTP {resp.status} for {filename}"}
            async for chunk in resp.content.iter_chunked(CHUNK):
                payload += chunk
                digest.update(chunk)
                if len(payload) > max_bytes:
                    return {"ok": False,
                            "detail": f"{filename} is larger than {max_bytes // 1024 // 1024} MB; "
                                      "refusing before it reaches the disk"}
    except Exception as err:                                       # noqa: BLE001
        return {"ok": False, "detail": f"could not fetch {filename}: {err}"}

    if digest.hexdigest() != expected:
        # Not a paranoid check: this is the one command that moves bytes the
        # home will play, and a truncated download is far more likely than a
        # tampered one. Either way it must not be written.
        return {"ok": False,
                "detail": f"{filename} did not match the expected checksum — "
                          "nothing was written"}

    refresh, access = await mint_owner_token(hass)
    if refresh is None:
        return {"ok": False, "detail": access}
    try:
        form = aiohttp.FormData()
        form.add_field("media_content_id", f"{LOCAL_ROOT}/{FOLDER}")
        form.add_field("file", bytes(payload), filename=filename,
                       content_type=cmd.get("content_type") or "application/octet-stream")
        async with session.post(
                base_url(hass) + "/api/media_source/local_source/upload",
                data=form, ssl=False, timeout=aiohttp.ClientTimeout(total=300),
                headers={"Authorization": f"Bearer {access}"}) as up:
            body = await up.text()
            if up.status >= 300:
                return {"ok": False,
                        "detail": f"Home Assistant rejected the upload "
                                  f"(HTTP {up.status}): {body[:200]}"}
    except Exception as err:                                       # noqa: BLE001
        return {"ok": False, "detail": f"upload failed: {err}"}
    finally:
        hass.auth.async_remove_refresh_token(refresh)

    return {"ok": True, "media_content_id": target_id(filename), "uploaded": True,
            "bytes": len(payload),
            "detail": f"uploaded {filename} ({len(payload) // 1024} KB) to {FOLDER}/"}


async def media_list(hass: HomeAssistant, cmd: dict[str, Any]) -> dict:
    """What we have put in this home's media folder."""
    from .ws_bridge import call_own_ws

    browsed = await call_own_ws(hass, {"type": "media_source/browse_media",
                                       "media_content_id": f"{LOCAL_ROOT}/{FOLDER}"})
    if not browsed.get("success"):
        # A home that has never had a file from us has no dartec/ folder, which
        # is a normal state and not an error worth alarming anyone about.
        return {"ok": True, "media": [], "detail": "no Dartec media on this home"}
    children = (browsed.get("result") or {}).get("children") or []
    media = [{"media_content_id": c.get("media_content_id"), "title": c.get("title")}
             for c in children if isinstance(c, dict)]
    return {"ok": True, "media": media, "detail": f"{len(media)} file(s)"}


HANDLERS = {"media_upload": media_upload, "media_list": media_list}
