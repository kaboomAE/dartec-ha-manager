"""Run the agent from this working tree inside a real Home Assistant.

For each Home Assistant version given, this:

1. starts `ghcr.io/home-assistant/home-assistant:<version>` with a config that
   loads `default_config`, the `demo` integration (dozens of devices, no
   network needed), this repo's `dartec_ha_manager`, and a canary integration
   that deliberately misbehaves;
2. onboards it and gets an access token;
3. starts a stub manager inside the container and pairs the agent to it
   through the real config flow;
4. waits for snapshots and for the answer to a `registry_query` command;
5. checks that the devices the agent reported are exactly the devices Home
   Assistant's own API lists, with the right names and areas (one device is
   put in an area first, since the demo leaves them all room-less), and the
   area id beside each name;
6. rotates the HACS token with `hacs_token_set` against a stand-in HACS
   (`hacs_stub/`) that reloads itself from an update listener the way HACS
   does, and checks the entry is still loaded, running with the new token,
   and that nothing in Home Assistant failed along the way
   (dartec-ha-manager#8);
7. reads the full entity inventory (`registry_query` with
   `include_unregistered`) and checks it holds every entity Home Assistant's
   REST API lists, that the entities outside the registry are exactly the ones
   the snapshot reports as unregistered, and that its digest is the
   snapshot's (dartec-ha-manager-server#16);
8. takes demo devices down, labels one as expected-offline and drains a
   battery through Home Assistant's own APIs (`device_health_setup.py`), and
   checks the agent's `offline_devices` and `batteries` sections report
   exactly what Home Assistant was told — no more, no less — and that every
   device row carries `available` and `last_seen` to match;
9. checks the log: nothing reported against `dartec_ha_manager`, in
   particular no device-registry mapping deprecation and no blocking read of
   `manifest.json`. The canary must be reported for the same things, so a
   clean log means a clean agent, not a detector that has changed wording.

Standard library only; needs Docker. Unit tests do not collect this file
(pytest only picks up `test_*.py`).

    python tests/live/run_live.py                      # 2026.8.3 and 2026.9.2
    python tests/live/run_live.py 2026.9.2 --keep      # leave the container up
    python tests/live/run_live.py stable beta          # moving tags work too
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
AGENT = REPO / "custom_components" / "dartec_ha_manager"
CANARY = HERE / "canary" / "dartec_live_canary"
HACS_STUB = HERE / "hacs_stub" / "hacs"

IMAGE = "ghcr.io/home-assistant/home-assistant"
# The last 2026.8 patch, where iterating the device registry yields ids, and
# the newest 2026.9, where it yields entries and mapping use is deprecated.
DEFAULT_VERSIONS = ["2026.8.3", "2026.9.2"]

AGENT_DOMAIN = "dartec_ha_manager"
CANARY_DOMAIN = "dartec_live_canary"
TOKEN = "live-test-pairing-token"
STUB_PORT = 8765
STATE_DIR = "/tmp/dartec-live"

# The same made-up tokens as stub_manager.py (which the driver cannot import:
# it needs aiohttp, which only the container has).
HACS_OLD_TOKEN = "github_pat_11LIVETESTOLD0000000000_" + "o" * 59
HACS_NEW_TOKEN = "github_pat_11LIVETESTNEW0000000000_" + "n" * 59
# What HACS's reload race looked like on the bench Pi, in Home Assistant's words.
HACS_BROKEN = re.compile(r"Error unloading entry|OperationNotAllowed|Task exception was never "
                         r"retrieved|FAILED_UNLOAD|Config entry was never loaded")

CONFIGURATION_YAML = f"""\
default_config:
demo:
{CANARY_DOMAIN}:
logger:
  default: warning
"""

# report_usage's wording (homeassistant/helpers/frame.py) and the device
# registry's own message (helpers/device_registry.py, 2026.9).
DEVICE_MAPPING = "uses `device_registry.devices` as a mapping"
# homeassistant/util/loop.py, for a call made from an integration's frame.
BLOCKING = re.compile(r"Detected blocking call to (\w+) with args (.*?) inside the event loop "
                      r"by (?:custom )?integration '([\w]+)'")
REPORTED = re.compile(r"Detected that (?:custom )?integration '([\w]+)' (.*)")


class Failure(AssertionError):
    pass


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def docker(*args: str, check: bool = True, capture: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=capture, text=True,
                            encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise Failure(f"docker {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return (result.stdout or "") + ("" if check else (result.stderr or ""))


def http(method: str, url: str, *, token: str | None = None, json_body=None,
         form=None, timeout: float = 30):
    headers = {}
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        return json.loads(body) if body else None


def wait_for(what: str, probe, timeout: float, interval: float = 2.0):
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = probe()
            if value:
                return value
        except Exception as err:  # noqa: BLE001 — retried until the deadline
            last_error = err
        time.sleep(interval)
    raise Failure(f"timed out after {timeout:.0f}s waiting for {what}"
                  + (f" (last error: {last_error})" if last_error else ""))


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version)[:3])


def is_pinned(tag: str) -> bool:
    """A release tag like 2026.9.2, rather than `stable` or `beta`."""
    return bool(re.fullmatch(r"\d{4}\.\d+\.\d+", tag))


def prepare_config(root: Path) -> Path:
    config = root / "config"
    components = config / "custom_components"
    components.mkdir(parents=True)
    (config / "configuration.yaml").write_text(CONFIGURATION_YAML, encoding="utf-8")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(AGENT, components / AGENT_DOMAIN, ignore=ignore)
    shutil.copytree(CANARY, components / CANARY_DOMAIN, ignore=ignore)
    shutil.copytree(HACS_STUB, components / "hacs", ignore=ignore)
    return config


def start_container(name: str, version: str, config: Path) -> int:
    docker("rm", "-f", "-v", name, check=False)
    docker("create", "--name", name, "-p", "127.0.0.1::8123", "-e", "TZ=UTC",
           f"{IMAGE}:{version}")
    docker("cp", f"{config}{'/'}.", f"{name}:/config")
    docker("cp", str(HERE / "stub_manager.py"), f"{name}:/stub_manager.py")
    docker("cp", str(HERE / "ha_registry.py"), f"{name}:/ha_registry.py")
    docker("cp", str(HERE / "device_health_setup.py"), f"{name}:/device_health_setup.py")
    docker("start", name)
    mapping = docker("port", name, "8123/tcp").strip().splitlines()[0]
    return int(mapping.rsplit(":", 1)[1])


def onboard(base: str) -> str:
    client_id = f"{base}/"
    created = http("POST", f"{base}/api/onboarding/users", json_body={
        "client_id": client_id, "name": "Live test", "username": "livetest",
        "password": "live-test-password", "language": "en"})
    tokens = http("POST", f"{base}/auth/token", form={
        "grant_type": "authorization_code", "code": created["auth_code"],
        "client_id": client_id})
    return tokens["access_token"]


def pair(base: str, token: str) -> None:
    flow = http("POST", f"{base}/api/config/config_entries/flow", token=token,
                json_body={"handler": AGENT_DOMAIN, "show_advanced_options": False})
    if flow.get("type") != "form":
        raise Failure(f"config flow did not open a form: {flow}")
    done = http("POST", f"{base}/api/config/config_entries/flow/{flow['flow_id']}",
                token=token, json_body={"server_url": f"http://127.0.0.1:{STUB_PORT}",
                                        "pairing_token": TOKEN})
    if done.get("type") != "create_entry":
        raise Failure(f"config flow did not create an entry: {done}")


def fingerprint(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()[:16]


def setup_hacs_stub(base: str, token: str) -> None:
    flow = http("POST", f"{base}/api/config/config_entries/flow", token=token,
                json_body={"handler": "hacs", "show_advanced_options": False})
    done = http("POST", f"{base}/api/config/config_entries/flow/{flow['flow_id']}",
                token=token, json_body={"token": HACS_OLD_TOKEN})
    if done.get("type") != "create_entry":
        raise Failure(f"the stand-in HACS was not set up: {done}")


def hacs_entry_state(base: str, token: str) -> str | None:
    entries = http("GET", f"{base}/api/config/config_entries/entry?domain=hacs", token=token)
    return entries[0].get("state") if entries else None


def check_hacs_swap(name: str, base: str, token: str, result: dict, timeout: float) -> list[str]:
    problems = []
    new_fp, old_fp = fingerprint(HACS_NEW_TOKEN), fingerprint(HACS_OLD_TOKEN)
    if not (result.get("ok") is True and result.get("changed") is True
            and result.get("fingerprint") == new_fp):
        problems.append(f"hacs_token_set did not swap cleanly: "
                        f"{ {k: v for k, v in result.items() if k != 'token'} }")
    state = hacs_entry_state(base, token)
    if state != "loaded":
        problems.append(f"HACS's entry is {state} after the swap, not loaded")
    try:
        running = http("GET", f"{base}/api/states/switch.hacs_stub", token=token)
        attributes = running.get("attributes") or {}
    except urllib.error.HTTPError as err:
        attributes = {"token_fingerprint": f"no switch.hacs_stub ({err.code})"}
    if attributes.get("token_fingerprint") != new_fp:
        problems.append(f"HACS is running with token {attributes.get('token_fingerprint')}, "
                        f"expected the new {new_fp} (old was {old_fp})")
    if attributes.get("listener_reloads") != 0:
        problems.append(f"the swap set off HACS's own update listener "
                        f"({attributes.get('listener_reloads')} reload(s)); that reload races the "
                        "swap's and can leave HACS in failed_unload")

    def reported():
        files = docker("exec", name, "ls", STATE_DIR).split()
        latest = max((int(f[9:-5]) for f in files if re.fullmatch(r"snapshot-\d+\.json", f)),
                     default=0)
        snap = read_state(name, f"snapshot-{latest}.json") if latest else None
        section = (snap or {}).get("hacs_token") or {}
        return section.get("token_fingerprint") == new_fp

    try:
        wait_for("a snapshot reporting the new HACS token", reported, timeout)
    except Failure as err:
        problems.append(str(err))
    broken = [line.strip() for line in ha_log(name).splitlines() if HACS_BROKEN.search(line)]
    if broken:
        problems.append("Home Assistant failed while the token was swapped:\n  "
                        + "\n  ".join(broken[:10]))
    return problems


def latest_snapshot(name: str) -> tuple[int, dict | None]:
    files = docker("exec", name, "ls", STATE_DIR).split()
    latest = max((int(f[9:-5]) for f in files if re.fullmatch(r"snapshot-\d+\.json", f)),
                 default=0)
    return latest, (read_state(name, f"snapshot-{latest}.json") if latest else None)


def check_inventory(name: str, base: str, token: str, timeout: float) -> tuple[list[str], dict]:
    """The manager's full inventory against every state Home Assistant runs."""
    result = wait_for("the inventory answer", lambda: read_state(name, "result-inventory.json"),
                      timeout)
    problems: list[str] = []
    if not result.get("ok"):
        return [f"the inventory query failed: {result}"], {}
    items = result.get("items") or []
    if result.get("total") != len(items):
        problems.append(f"inventory returned {len(items)} of {result.get('total')} in one "
                        "1000-row page; the larger inventory page is not in effect")
    ids = {row["entity_id"] for row in items}
    states = {s["entity_id"] for s in http("GET", f"{base}/api/states", token=token)}
    missing = sorted(states - ids)
    if missing:
        problems.append(f"{len(missing)} entities Home Assistant runs are not in the "
                        f"inventory: {missing[:8]}")
    outside = {row["entity_id"] for row in items if row.get("registered") is False}
    if not outside:
        problems.append("no unregistered entities in the inventory; the demo has some, "
                        "so the blind spot is not covered")

    _, snap = latest_snapshot(name)
    snap = snap or {}
    reported = {row["entity_id"] for row in snap.get("unregistered_entities") or []}
    if reported != outside:
        problems.append(f"snapshot unregistered_entities differ from the inventory's: "
                        f"only in snapshot {sorted(reported - outside)[:5]}, only in "
                        f"inventory {sorted(outside - reported)[:5]}")
    if snap.get("unregistered_count") != len(outside):
        problems.append(f"unregistered_count {snap.get('unregistered_count')} != {len(outside)}")
    if (snap.get("entity_registry_count") or 0) + len(outside) != result.get("total"):
        problems.append(f"registry {snap.get('entity_registry_count')} + unregistered "
                        f"{len(outside)} != inventory total {result.get('total')}")
    if not snap.get("entity_inventory_digest") or \
            snap.get("entity_inventory_digest") != result.get("inventory_digest"):
        problems.append(f"snapshot digest {snap.get('entity_inventory_digest')} != "
                        f"inventory digest {result.get('inventory_digest')}")
    return problems, {"total": result.get("total"), "states": len(states),
                      "unregistered": len(outside)}


def check_device_health(name: str, token: str, timeout: float) -> tuple[list[str], dict]:
    """Stage the outage and the flat battery, then read them back from a snapshot
    taken afterwards. A snapshot from before the staging would pass the
    negative checks vacuously, so the wait is for one numbered after it."""
    truth = json.loads(docker("exec", name, "python3", "/device_health_setup.py", token))
    staged_at, _ = latest_snapshot(name)
    offline_id = truth["offline"][0]

    def reported():
        number, snap = latest_snapshot(name)
        if number <= staged_at or not snap:
            return None
        ids = {row.get("device_id") for row in snap.get("offline_devices") or []}
        return snap if offline_id in ids else None

    problems: list[str] = []
    try:
        snap = wait_for("a snapshot reporting the staged outage", reported, timeout)
    except Failure as err:
        return [str(err)], {}
    offline = {row["device_id"]: row for row in snap.get("offline_devices") or []}
    if set(offline) != set(truth["offline"]):
        problems.append(f"offline_devices are {sorted(offline)}, expected exactly "
                        f"{truth['offline']} (not the labelled or half-down device, not "
                        "Push's never-pressed button, not the Backup service)")
    if snap.get("offline_count") != len(truth["offline"]):
        problems.append(f"offline_count {snap.get('offline_count')} != {len(truth['offline'])}")
    row = offline.get(offline_id) or {}
    if not row.get("since") or row.get("integration") != "demo":
        problems.append(f"the offline row is incomplete: {row}")
    for device_id in truth["not_offline"]:
        if device_id in offline:
            problems.append(f"{device_id} was reported offline and should not have been")

    batteries = snap.get("batteries") or []
    reported_levels = {r["device_id"]: r.get("level") for r in batteries}
    if reported_levels != truth["batteries"]:
        problems.append(f"batteries {reported_levels} != Home Assistant's {truth['batteries']}")
    levels = [r.get("level") for r in batteries]
    if levels != sorted(levels):
        problems.append(f"batteries are not lowest first: {levels}")
    if snap.get("battery_count") != len(truth["batteries"]):
        problems.append(f"battery_count {snap.get('battery_count')} != "
                        f"{len(truth['batteries'])} (the demo's battery_charging sensor "
                        "must not count)")
    if snap.get("device_health_error"):
        problems.append(f"the device health collector failed: {snap['device_health_error']}")

    # Per-device presence on the device rows themselves, which the manager
    # reads for every planned device rather than only the ones down.
    rows = {row.get("id"): row for row in snap.get("devices") or []}
    presence = {}
    for device_id, expected in ((offline_id, False), (truth["labelled"], False),
                                (truth["half_down"], True)):
        row = rows.get(device_id) or {}
        presence[device_id] = (row.get("available"), row.get("last_seen"))
        if row.get("available") is not expected:
            problems.append(f"device {device_id} has available={row.get('available')!r}, "
                            f"expected {expected}")
        if not row.get("last_seen"):
            problems.append(f"device {device_id} has no last_seen: {row}")
    if not any(row.get("available") is True for row in rows.values()):
        problems.append("no device row reports available=True")
    missing = [device_id for device_id, row in rows.items()
               if "available" not in row or "last_seen" not in row]
    if missing:
        problems.append(f"device rows without available/last_seen: {missing[:5]}")
    return problems, {"offline": list(offline.values()), "batteries": batteries,
                      "presence": presence}


def read_state(name: str, filename: str):
    out = subprocess.run(["docker", "exec", name, "cat", f"{STATE_DIR}/{filename}"],
                         capture_output=True, text=True, encoding="utf-8")
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout else None


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def ha_log(name: str) -> str:
    """The log file, or the console if there is no file yet.

    Both carry the same records; reading both reported every line twice."""
    text = subprocess.run(["docker", "exec", name, "cat", "/config/home-assistant.log"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout
    return ANSI.sub("", text or docker("logs", name, check=False))


def check_devices(snapshot: dict, query: dict, truth: dict) -> list[str]:
    problems = []
    areas = {area["area_id"]: area["name"] for area in truth["areas"]}
    expected = {device["id"]: device for device in truth["devices"]}
    if not expected:
        problems.append("Home Assistant lists no devices; the demo integration did not load, "
                        "so there is nothing to resolve")
    if not any(device.get("area_id") for device in expected.values()):
        problems.append("no device is in an area, so area names went unchecked")

    if snapshot.get("device_count") != len(expected):
        problems.append(f"snapshot device_count {snapshot.get('device_count')} != "
                        f"{len(expected)} devices in Home Assistant")
    for source, rows in (("snapshot", snapshot.get("devices") or []),
                         ("registry_query", query.get("items") or [])):
        seen = {row.get("id"): row for row in rows}
        if set(seen) != set(expected):
            missing = sorted(set(expected) - set(seen))[:5]
            extra = sorted(set(seen) - set(expected))[:5]
            problems.append(f"{source} device ids differ from Home Assistant's: "
                            f"missing {missing}, unexpected {extra}")
            continue
        for device_id, row in seen.items():
            device = expected[device_id]
            name = device.get("name_by_user") or device.get("name")
            area = areas.get(device.get("area_id")) if device.get("area_id") else None
            if (row.get("name"), row.get("manufacturer"), row.get("area")) != \
                    (name, device.get("manufacturer"), area):
                problems.append(f"{source} row for {device_id} is "
                                f"{(row.get('name'), row.get('manufacturer'), row.get('area'))}, "
                                f"Home Assistant says {(name, device.get('manufacturer'), area)}")
                break
            # The id beside the name, which the manager keys room pairs on.
            if row.get("area_id") != (device.get("area_id") or None):
                problems.append(f"{source} row for {device_id} has area_id "
                                f"{row.get('area_id')!r}, Home Assistant says "
                                f"{device.get('area_id')!r}")
                break
    if query.get("total") != len(expected):
        problems.append(f"registry_query total {query.get('total')} != {len(expected)}")
    return problems


def check_log(text: str, version: str) -> tuple[list[str], dict]:
    problems = []
    reported: dict[str, list[str]] = {AGENT_DOMAIN: [], CANARY_DOMAIN: []}
    blocking: dict[str, list[str]] = {AGENT_DOMAIN: [], CANARY_DOMAIN: []}
    for line in text.splitlines():
        if match := REPORTED.search(line):
            reported.setdefault(match.group(1), []).append(line.strip())
        if match := BLOCKING.search(line):
            blocking.setdefault(match.group(3), []).append(line.strip())

    # The detector has to be working before its silence means anything.
    mapping_is_deprecated = version_tuple(version) >= (2026, 9)
    canary_mapping = [line for line in reported[CANARY_DOMAIN] if DEVICE_MAPPING in line]
    if mapping_is_deprecated and not canary_mapping:
        problems.append("canary's device registry mapping access was not reported — the "
                        "deprecation check cannot be trusted on this image")
    if not mapping_is_deprecated and canary_mapping:
        problems.append("canary's mapping access was reported on a version that should "
                        "not deprecate it; the version boundary assumption is wrong")
    if not any("manifest.json" in line for line in blocking[CANARY_DOMAIN]):
        problems.append("canary's blocking open of manifest.json was not reported — the "
                        "blocking-call check cannot be trusted on this image")

    agent_mapping = [line for line in reported[AGENT_DOMAIN] if DEVICE_MAPPING in line]
    agent_manifest = [line for line in blocking[AGENT_DOMAIN] if "manifest.json" in line]
    if agent_mapping:
        problems.append("agent used the device registry as a mapping:\n  "
                        + "\n  ".join(agent_mapping))
    if agent_manifest:
        problems.append("agent read manifest.json in the event loop:\n  "
                        + "\n  ".join(agent_manifest))
    # Anything else Home Assistant holds against the agent is just as much a
    # future break, even if it is not one of the two this test was written for.
    others = [line for line in reported[AGENT_DOMAIN] + blocking[AGENT_DOMAIN]
              if line not in agent_mapping and line not in agent_manifest]
    if others:
        problems.append("Home Assistant reported the agent for:\n  " + "\n  ".join(others))
    errors = [line.strip() for line in text.splitlines()
              if re.search(r"\b(ERROR|CRITICAL)\b", line) and AGENT_DOMAIN in line]
    if errors:
        problems.append("agent logged errors:\n  " + "\n  ".join(errors[:20]))
    return problems, {"canary_reported": canary_mapping,
                      "canary_blocking": blocking[CANARY_DOMAIN]}


def run_version(version: str, keep: bool, artifacts: Path | None, timeout: float) -> list[str]:
    name = f"dartec-live-{version.replace('.', '-')}"
    manifest_version = json.loads((AGENT / "manifest.json").read_text(encoding="utf-8"))["version"]
    with tempfile.TemporaryDirectory(prefix="dartec-live-") as tmp:
        config = prepare_config(Path(tmp))
        log(f"{version}: starting {IMAGE}:{version} as {name}")
        port = start_container(name, version, config)
    base = f"http://127.0.0.1:{port}"
    problems: list[str] = []
    try:
        wait_for("Home Assistant to serve onboarding",
                 lambda: http("GET", f"{base}/api/onboarding") is not None, timeout)
        token = onboard(base)
        log(f"{version}: onboarded on port {port}")

        docker("exec", "-d", name, "python3", "/stub_manager.py")
        wait_for("the stub manager", lambda: docker(
            "exec", name, "curl", "-sf", f"http://127.0.0.1:{STUB_PORT}/health"), 30, 1)

        # Home Assistant has to have started (the canary runs then) and the
        # demo devices have to exist before the snapshot means anything.
        wait_for("Home Assistant to finish starting",
                 lambda: http("GET", f"{base}/api/config", token=token).get("state") == "RUNNING",
                 timeout)
        docker("exec", name, "python3", "/ha_registry.py", token, "--assign-area")
        setup_hacs_stub(base, token)
        wait_for("the stand-in HACS to load", lambda: hacs_entry_state(base, token) == "loaded",
                 timeout, 1)
        pair(base, token)
        log(f"{version}: paired with the stub manager")

        query = wait_for("the registry_query answer",
                         lambda: read_state(name, "result-devices.json"), timeout)
        # The agent pushes a snapshot straight after answering, so a second
        # one means the registry was read again after the command.
        wait_for("a second snapshot", lambda: read_state(name, "snapshot-2.json"), timeout)
        snapshot = read_state(name, "snapshot-2.json")
        truth = json.loads(docker("exec", name, "python3", "/ha_registry.py", token))

        running = http("GET", f"{base}/api/config", token=token)["version"]
        if is_pinned(version) and running != version:
            problems.append(f"{IMAGE}:{version} is running Home Assistant {running}")
        core = snapshot.get("core") or {}
        if core.get("version") != running:
            problems.append(f"snapshot reports Home Assistant {core.get('version')}, "
                            f"expected {running}")
        if core.get("agent_version") != manifest_version:
            problems.append(f"snapshot reports agent {core.get('agent_version')}, "
                            f"manifest says {manifest_version}")
        if not query.get("ok"):
            problems.append(f"registry_query failed: {query}")
        # Hardware identity and disk health (0.19). In a container there is no
        # Supervisor or UDisks2, so the health section must still arrive and
        # say why it is short, never be missing or crash the snapshot.
        hardware = snapshot.get("hardware") or {}
        if not hardware.get("ha_uuid"):
            problems.append(f"snapshot hardware has no ha_uuid: {sorted(hardware)}")
        disk = snapshot.get("disk_health")
        if not isinstance(disk, dict) or not ("sources" in disk or "unavailable" in disk):
            problems.append(f"snapshot disk_health missing or malformed: {disk!r}")
        problems += check_devices(snapshot, query, truth)

        swap = wait_for("the hacs_token_set answer",
                        lambda: read_state(name, "result-hacs-swap.json"), timeout)
        problems += check_hacs_swap(name, base, token, swap, timeout)
        log(f"{version}: HACS token swap answered {swap.get('reason') or 'ok'}")

        inventory_problems, inventory = check_inventory(name, base, token, timeout)
        problems += inventory_problems
        log(f"{version}: inventory: {inventory.get('total')} entities for "
            f"{inventory.get('states')} states, {inventory.get('unregistered')} outside "
            "the registry")

        health_problems, health = check_device_health(name, token, timeout)
        problems += health_problems
        log(f"{version}: device health: {len(health.get('offline', []))} offline, "
            f"batteries {[b.get('level') for b in health.get('batteries', [])]}")

        text = ha_log(name)
        # The deprecation boundary is judged on what is running, so `stable`
        # and `beta` work as well as a pinned tag.
        log_problems, evidence = check_log(text, running)
        problems += log_problems
        log(f"{version}: Home Assistant {running}, {len(truth['devices'])} devices; "
            "canary reports seen: "
            f"{len(evidence['canary_reported'])} mapping, "
            f"{len(evidence['canary_blocking'])} blocking")

        if artifacts:
            out = artifacts / version
            out.mkdir(parents=True, exist_ok=True)
            (out / "home-assistant.log").write_text(text, encoding="utf-8")
            (out / "snapshot.json").write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
            (out / "registry_query.json").write_text(json.dumps(query, indent=1),
                                                     encoding="utf-8")
            (out / "ha_registry.json").write_text(json.dumps(truth, indent=1), encoding="utf-8")
            (out / "device_health.json").write_text(json.dumps(health, indent=1),
                                                    encoding="utf-8")
    except Failure as err:
        problems.append(str(err))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as err:
        problems.append(f"{type(err).__name__}: {err}")
    finally:
        if problems and artifacts:
            out = artifacts / version
            out.mkdir(parents=True, exist_ok=True)
            (out / "container.log").write_text(ha_log(name), encoding="utf-8")
        if keep:
            log(f"{version}: left running at {base} (docker rm -f -v {name})")
        else:
            docker("rm", "-f", "-v", name, check=False)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("versions", nargs="*", default=DEFAULT_VERSIONS)
    parser.add_argument("--keep", action="store_true", help="leave containers running")
    parser.add_argument("--artifacts", type=Path, help="write logs and snapshots here")
    parser.add_argument("--timeout", type=float, default=300,
                        help="seconds allowed for each wait (default 300)")
    args = parser.parse_args()

    failed = False
    for version in args.versions:
        started = time.monotonic()
        problems = run_version(version, args.keep, args.artifacts, args.timeout)
        took = time.monotonic() - started
        if problems:
            failed = True
            log(f"{version}: FAILED after {took:.0f}s")
            for problem in problems:
                print(f"  - {problem}")
        else:
            log(f"{version}: passed in {took:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
