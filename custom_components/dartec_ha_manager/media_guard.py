"""No picture, snapshot, stream or video of a home ever leaves it.

The owner's decision, 2026-10-18 (kaboomAE/dartec-ops#45): Baytec monitors
cameras for **health only** — online, recording, recorder disk space. It
never sees what a camera sees. Recordings stay on the recorder in the house.

That rule is kept in three places, so that no single mistake breaks it:

1. **What is read.** `ha_facts.entity_facts` copies only an allowlist of
   state attributes (`ALLOWED_ATTRIBUTES` there). `entity_picture`, a camera's
   `access_token` and `frontend_stream_type` are never read, so the network and
   camera adapters cannot pass one on even by accident.
2. **What can be asked for.** `service_policy` classes `camera.snapshot`,
   `camera.record`, `camera.play_stream` and `image.snapshot` as NEVER: no
   consent window, commissioning or opt-in brings them back. This agent has no
   command that fetches a camera image, proxies a stream or browses a media
   source other than its own `dartec/` folder (`media_cmds`).
3. **What is sent.** `strip` runs at the only door the agent has to the
   manager (`cloud_link`), on every snapshot and every command reply, after
   household names are hidden. It drops every key that names a picture or a
   stream, and every string that is a camera or image URL, wherever it
   appears. Anything a future collector adds is covered without having to
   remember this rule.

`strip` returns the paths it removed, so a test can say what it caught and the
agent can log that something tried, without logging the URL itself (a camera
proxy URL carries a token that would open the picture).

No Home Assistant imports: the rule is tested without Home Assistant
(tests/test_media_guard.py).
"""
from __future__ import annotations

import re
from typing import Any

# Keys that only ever hold a picture, a stream, or the token that opens one.
# Matched case-insensitively. `access_token` is the camera entity's attribute;
# this agent's own owner token never travels in a snapshot or a reply.
FORBIDDEN_KEYS = frozenset({
    "entity_picture", "entity_picture_local",
    "access_token",
    "stream_source", "stream_url", "stream_uri", "rtsp_url", "rtsp_alias",
    "frontend_stream_type",
    "still_image_url", "snapshot_url", "thumbnail_url", "image_url",
})
# Deliberately not here: Lovelace's `camera_image` and `image`. In a dashboard
# they name an entity or a file for the household's own screen, not a
# picture, and stripping them would make a dashboard read through the manager
# lose them when it is saved back.

# Strings that address a picture or a stream. Home Assistant's own camera,
# image and stream views; the integrations' proxy views (UniFi Protect's
# event thumbnails and clips, Reolink's playback); media sources that browse
# recordings; and streaming URLs a camera's config could expose.
FORBIDDEN_VALUE = re.compile(
    r"/api/camera_proxy|/api/image_proxy|/api/hls/"
    r"|/api/webrtc|/api/unifiprotect/|/api/reolink/|/api/frigate/"
    r"|media-source://(?:camera|image|unifiprotect|reolink|frigate|nest)"
    r"|^\s*(?:rtsps?|rtmps?|srt)://",
    re.IGNORECASE)

REMOVED = "[removed: camera media]"


def is_forbidden_key(key: Any) -> bool:
    return isinstance(key, str) and key.lower() in FORBIDDEN_KEYS


def is_forbidden_value(value: Any) -> bool:
    return isinstance(value, str) and FORBIDDEN_VALUE.search(value) is not None


def strip(obj: Any, _path: str = "", _found: list[str] | None = None) -> tuple[Any, list[str]]:
    """(a copy of `obj` with every picture and stream removed, the paths removed).

    A forbidden key is dropped with its value. A forbidden string, wherever
    it is, is replaced by REMOVED, so a list keeps its length and positions.
    A dict key that is itself such a string is dropped. Everything else is
    copied unchanged.
    """
    found = [] if _found is None else _found
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            here = f"{_path}.{key}" if _path else str(key)
            if is_forbidden_key(key) or is_forbidden_value(key):
                found.append(_path or "<root>" if is_forbidden_value(key) else here)
                continue
            out[key], _ = strip(value, here, found)
        return out, found
    if isinstance(obj, (list, tuple)):
        return [strip(item, f"{_path}[{i}]", found)[0] for i, item in enumerate(obj)], found
    if is_forbidden_value(obj):
        found.append(_path or "<root>")
        return REMOVED, found
    return obj, found
