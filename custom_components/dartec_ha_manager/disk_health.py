"""The machine's identity, its disk's health, and how hot it is running.

Written for a sealed cabinet with no airflow, where the questions that matter
are "is the disk wearing out", "has it started failing", and "is it cooking".

WHAT THE AGENT CAN READ, AND WHY ONLY THIS. The agent runs inside Home
Assistant Core, not on the host, and has no smartctl. It uses only what Core
can already reach on Home Assistant OS, and asks for nothing new:

* **The Supervisor API**, with the token Core already holds: `/info`
  (machine, arch), `/os/info` (board, data disk), `/host/info` (chassis),
  `/network/info` (the primary MAC), `/hardware/info` (the drives, as UDisks2
  describes them: model, vendor, serial, size, bus).
* **UDisks2 on the host's system D-Bus**, which Core already talks to for
  Bluetooth. Read-only: object properties and `SmartGetAttributes`, which
  returns what udisksd last read from the drive and does not itself send a
  command to it. This is where real SMART comes from: an NVMe drive's health
  log (percentage used, spare, media errors, power-on hours, temperature) or
  an ATA drive's SMART attributes, whenever the host's udisks2 was built with
  that support.
* **sysfs**, which is readable in any container: the kernel's hwmon sensors
  (an NVMe drive's composite temperature, the CPU's), and an eMMC's own
  life-time estimate and pre-end-of-life flag.

An SD card reports none of this; there is nothing to read, and the snapshot
says so rather than sending zeros. Anything beyond it (running smartctl,
self-tests, raw device access) needs a privileged add-on, which is a decision
for the owner, not something this module does.

THREADING. Every sysfs read blocks and runs in an executor. The D-Bus calls
are async and time out. Nothing here may cost a snapshot: every failure
becomes a reason string in the result.

CADENCE. Identity every IDENTITY_TTL_S, health every HEALTH_TTL_S, both
cached in between and sent in every snapshot; the two temperatures are cheap
sysfs reads taken every cycle.
"""
from __future__ import annotations

import glob
import logging
import os
import re
import time

_LOGGER = logging.getLogger(__name__)

IDENTITY_TTL_S = 6 * 3600
HEALTH_TTL_S = 15 * 60
DBUS_TIMEOUT_S = 10

UDISKS = "org.freedesktop.UDisks2"
UDISKS_ROOT = "/org/freedesktop/UDisks2"
DRIVE_IFACE = "org.freedesktop.UDisks2.Drive"
ATA_IFACE = "org.freedesktop.UDisks2.Drive.Ata"
NVME_IFACE = "org.freedesktop.UDisks2.NVMe.Controller"

# UDisks reports NVMe critical-warning bits by name; the manager keys on the
# spec's bit values.
NVME_WARNING_BITS = {"spare": 0x01, "temperature": 0x02, "degraded": 0x04,
                     "readonly": 0x08, "volatile_mem": 0x10, "pmr_readonly": 0x20}

# ATA attributes whose normalised value counts down from 100 as a flash drive
# wears (vendors disagree on which one they use).
ATA_WEAR_ATTRIBUTES = {177: "Wear_Leveling_Count", 202: "Percent_Lifetime_Remain",
                       231: "SSD_Life_Left", 233: "Media_Wearout_Indicator"}

# hwmon chip names that are the processor, best first.
SOC_SENSORS = ("cpu_thermal", "k10temp", "coretemp", "soc_thermal", "cpu0_thermal",
               "zenpower", "acpitz")


# --- sysfs (blocking: executor only) -----------------------------------------

def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def base_device(dev_path: str | None) -> str | None:
    """The whole disk a partition is on: nvme0n1p8 -> nvme0n1,
    mmcblk0p8 -> mmcblk0, sda8 -> sda."""
    name = os.path.basename(str(dev_path or "")).strip()
    if not name:
        return None
    for pattern in (r"^(nvme\d+n\d+)(p\d+)?$", r"^(mmcblk\d+)(p\d+)?$",
                    r"^((?:sd|vd|hd|xvd)[a-z]+)\d*$"):
        match = re.match(pattern, name)
        if match:
            return match.group(1)
    return name


def storage_type(device: str | None, *, root: str = "", bus: str | None = None,
                 removable: bool | None = None, rotation_rate: int | None = None) -> str:
    """nvme | ssd | hdd | emmc | sd | usb | unknown."""
    if not device:
        return "unknown"
    if device.startswith("nvme"):
        return "nvme"
    if device.startswith("mmcblk"):
        kind = (_read(f"{root}/sys/block/{device}/device/type") or "").upper()
        if kind == "MMC":
            return "emmc"
        if kind == "SD":
            return "sd"
        return "sd" if removable else "emmc"
    if (bus or "").lower() == "usb":
        return "usb"
    if rotation_rate is not None and rotation_rate != -1:
        return "hdd" if rotation_rate > 0 else "ssd"
    rotational = _read(f"{root}/sys/block/{device}/queue/rotational")
    if rotational == "1":
        return "hdd"
    if rotational == "0":
        return "ssd"
    return "unknown"


def emmc_life(device: str, root: str = "") -> dict:
    """An eMMC's own wear estimate: `life_time` is two values (type A and B
    cells) from 0x01 (0-10% used) to 0x0A (90-100%) and 0x0B (exceeded);
    `pre_eol_info` is 0x01 normal, 0x02 warning (80% of reserved blocks
    used), 0x03 urgent. Reported as the lower bound, so 0x09 reads 80%."""
    out: dict = {}
    life = _read(f"{root}/sys/block/{device}/device/life_time")
    if life:
        try:
            worst = max(int(part, 16) for part in life.split())
        except ValueError:
            worst = 0
        if 1 <= worst <= 0x0B:
            out["wear_pct"] = min(100, (worst - 1) * 10)
    pre_eol = _read(f"{root}/sys/block/{device}/device/pre_eol_info")
    if pre_eol:
        try:
            out["pre_eol"] = {1: "normal", 2: "warning", 3: "urgent"}.get(int(pre_eol, 16))
        except ValueError:
            pass
    return out


def _millidegrees(path: str) -> float | None:
    raw = _read(path)
    try:
        value = int(raw) / 1000.0 if raw is not None else None
    except ValueError:
        return None
    # Sensors that are absent read as exactly 0 or as absurd values; neither
    # is a temperature an indoor machine had.
    return value if value is not None and value != 0 and -40 < value < 150 else None


def disk_temp_c(device: str | None, root: str = "") -> float | None:
    """The disk's own sensor through hwmon: NVMe registers one per
    controller, a SATA drive one when the drivetemp module is loaded."""
    if not device:
        return None
    patterns = [f"{root}/sys/block/{device}/device/hwmon*/temp1_input",
                f"{root}/sys/block/{device}/device/hwmon/hwmon*/temp1_input"]
    match = re.match(r"^nvme(\d+)n\d+$", device)
    if match:
        patterns.append(f"{root}/sys/class/nvme/nvme{match.group(1)}/hwmon*/temp1_input")
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            value = _millidegrees(path)
            if value is not None:
                return value
    return None


def soc_temp_c(root: str = "") -> float | None:
    """The processor's temperature, from the first hwmon chip that is one."""
    chips: dict[str, str] = {}
    for chip in sorted(glob.glob(f"{root}/sys/class/hwmon/hwmon*")):
        name = _read(f"{chip}/name")
        if name and name not in chips:
            chips[name] = chip
    for name in SOC_SENSORS:
        chip = chips.get(name)
        if chip:
            value = _millidegrees(f"{chip}/temp1_input")
            if value is not None:
                return value
    # ARM boards without a named chip still have thermal zone 0 on the SoC.
    return _millidegrees(f"{root}/sys/class/thermal/thermal_zone0/temp")


def config_device(path: str = "/config", root: str = "", stat=os.stat) -> str | None:
    """BLOCKING. The disk holding Home Assistant's config, for installs with
    no Supervisor to ask: the device number of the directory, looked up in
    /sys/dev/block."""
    try:
        st_dev = stat(path).st_dev
    except OSError:
        return None
    # Major 0 is a virtual filesystem (overlay, tmpfs, a container's bind of
    # one): there is no disk behind it, and no entry in /sys/dev/block. The
    # live CI job caught this reporting "0:49" as a disk's name.
    if os.major(st_dev) == 0:
        return None
    link = f"{root}/sys/dev/block/{os.major(st_dev)}:{os.minor(st_dev)}"
    if not os.path.exists(link):
        return None
    try:
        return base_device(os.path.basename(os.path.realpath(link)))
    except OSError:
        return None


def sysfs_storage(device: str | None, root: str = "") -> dict | None:
    """BLOCKING. The disk as sysfs describes it, for installs with no
    Supervisor. Size is in 512-byte sectors whatever the drive's own."""
    if not device:
        return None
    base = f"{root}/sys/block/{device}"
    size = _read(f"{base}/size")
    removable = _read(f"{base}/removable") == "1"
    return {
        "model": _read(f"{base}/device/model") or _read(f"{base}/device/name"),
        "vendor": _read(f"{base}/device/vendor"),
        "serial": _read(f"{base}/device/serial"),
        "size_bytes": int(size) * 512 if size and size.isdigit() else None,
        "type": storage_type(device, root=root, removable=removable),
        "bus": None,
        "device": device,
        "id": None,
    }


def default_route_mac(root: str = "") -> str | None:
    """BLOCKING. The MAC of the interface carrying the default route."""
    table = _read(f"{root}/proc/net/route") or ""
    for line in table.splitlines()[1:]:
        fields = line.split()
        if len(fields) > 2 and fields[1] == "00000000":
            mac = _read(f"{root}/sys/class/net/{fields[0]}/address")
            if mac and mac != "00:00:00:00:00:00":
                return mac
    return None


def read_temperatures(device: str | None, root: str = "") -> dict:
    """BLOCKING. The two per-cycle readings."""
    return {"disk_temp_c": disk_temp_c(device, root), "soc_temp_c": soc_temp_c(root)}


# --- the Supervisor's view ---------------------------------------------------

def primary_mac(network_info: dict | None) -> str | None:
    interfaces = (network_info or {}).get("interfaces") or []
    primary = next((i for i in interfaces if i.get("primary")), None) or \
        next((i for i in interfaces if i.get("type") == "ethernet"), None) or \
        (interfaces[0] if interfaces else None)
    return (primary or {}).get("mac") or None


def _fs_device(drive: dict) -> str | None:
    for fs in drive.get("filesystems") or []:
        device = base_device(fs.get("device"))
        if device:
            return device
    return None


def pick_data_drive(hardware_info: dict | None, data_disk: str | None) -> dict | None:
    """The drive Home Assistant's data lives on. The Supervisor names it in
    /os/info `data_disk` (a UDisks drive id, or on older releases a device
    path); failing that, it is the drive holding the `hassos-data`
    partition, or the only drive there is."""
    drives = (hardware_info or {}).get("drives") or []
    if not drives:
        return None
    if data_disk:
        for drive in drives:
            if drive.get("id") == data_disk:
                return drive
        wanted = base_device(data_disk)
        for drive in drives:
            if wanted and _fs_device(drive) == wanted:
                return drive
    for drive in drives:
        if any((fs.get("name") or "") == "hassos-data" for fs in drive.get("filesystems") or []):
            return drive
    return drives[0] if len(drives) == 1 else None


def storage_from_drive(drive: dict | None, kind: str) -> dict | None:
    if not drive:
        return None
    return {
        "model": drive.get("model") or None,
        "vendor": drive.get("vendor") or None,
        "serial": drive.get("serial") or None,
        "size_bytes": drive.get("size") or None,
        "type": kind,
        "bus": drive.get("connection_bus") or None,
        "device": _fs_device(drive),
        "id": drive.get("id") or None,
    }


# --- UDisks2 ------------------------------------------------------------------

def _plain(value):
    """dbus-fast Variants all the way down, to plain Python."""
    if hasattr(value, "signature") and hasattr(value, "value"):
        return _plain(value.value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _kelvin(value) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    return round(value - 273.15, 1)


def find_drive(objects: dict, drive_id: str | None, serial: str | None) -> tuple[str | None, dict]:
    """(object path, interfaces) of the drive in GetManagedObjects' answer."""
    for path, interfaces in (objects or {}).items():
        drive = interfaces.get(DRIVE_IFACE)
        if not drive:
            continue
        if (drive_id and drive.get("Id") == drive_id) or \
                (serial and drive.get("Serial") == serial):
            return path, interfaces
    return None, {}


def health_from_nvme(props: dict, attrs: dict | None) -> dict:
    out: dict = {"type": "nvme"}
    warnings = props.get("SmartCriticalWarning") or []
    out["critical_warning"] = sum(NVME_WARNING_BITS.get(w, 0) for w in warnings)
    if props.get("SmartPowerOnHours"):
        out["power_on_hours"] = int(props["SmartPowerOnHours"])
    temp = _kelvin(props.get("SmartTemperature"))
    if temp is not None:
        out["temperature_c"] = temp
    attrs = attrs or {}
    for key, name in (("percent_used", "wear_pct"), ("avail_spare", "available_spare_pct"),
                      ("spare_thresh", "spare_threshold_pct"), ("media_errors", "media_errors"),
                      ("num_err_log_entries", "error_log_entries"),
                      ("unsafe_shutdowns", "unsafe_shutdowns"), ("power_cycles", "power_cycles")):
        if isinstance(attrs.get(key), int):
            out[name] = attrs[key]
    if isinstance(attrs.get("total_data_written"), int):
        out["data_written_tb"] = round(attrs["total_data_written"] / 1e12, 3)
    for key, name in (("wctemp", "temperature_warn_c"), ("cctemp", "temperature_crit_c")):
        value = _kelvin(attrs.get(key))
        if value is not None:
            out[name] = value
    if props.get("SmartUpdated"):
        out["smart_updated"] = int(props["SmartUpdated"])
    return out


def health_from_ata(props: dict, attributes: list | None) -> dict:
    out: dict = {}
    if not props.get("SmartSupported"):
        return {"unavailable": "the drive does not report SMART over its connection"}
    if not props.get("SmartEnabled"):
        return {"unavailable": "SMART is switched off on the drive"}
    if not props.get("SmartUpdated"):
        return {"unavailable": "the host has not read this drive's SMART data yet"}
    out["smart_failing"] = bool(props.get("SmartFailing"))
    if props.get("SmartPowerOnSeconds"):
        out["power_on_hours"] = int(props["SmartPowerOnSeconds"]) // 3600
    temp = _kelvin(props.get("SmartTemperature"))
    if temp is not None:
        out["temperature_c"] = temp
    if isinstance(props.get("SmartNumBadSectors"), int):
        out["reallocated_sectors"] = props["SmartNumBadSectors"]
    for row in attributes or []:
        # (id, name, flags, value, worst, threshold, pretty, pretty_unit, expansion)
        try:
            attr_id, value, pretty = int(row[0]), int(row[3]), int(row[6])
        except (TypeError, ValueError, IndexError):
            continue
        if attr_id == 5:
            out["reallocated_sectors"] = pretty
        elif attr_id == 197:
            out["pending_sectors"] = pretty
        elif attr_id in ATA_WEAR_ATTRIBUTES and 0 < value <= 100 and "wear_pct" not in out:
            out["wear_pct"] = 100 - value
            out["wear_source"] = ATA_WEAR_ATTRIBUTES[attr_id]
    out["smart_updated"] = int(props["SmartUpdated"])
    return out


async def read_udisks(drive_id: str | None, serial: str | None) -> dict:
    """Health from the host's UDisks2, or {"unavailable": why}."""
    import asyncio

    try:
        from dbus_fast import BusType, Message, MessageType
        from dbus_fast.aio import MessageBus
    except ImportError:
        return {"unavailable": "no D-Bus library in this Home Assistant"}
    if not os.path.exists("/run/dbus/system_bus_socket") and \
            not os.environ.get("DBUS_SYSTEM_BUS_ADDRESS"):
        return {"unavailable": "no system D-Bus in this install"}

    bus = None
    try:
        bus = await asyncio.wait_for(MessageBus(bus_type=BusType.SYSTEM).connect(),
                                     DBUS_TIMEOUT_S)

        async def call(path: str, interface: str, member: str, signature: str = "",
                       body: list | None = None):
            reply = await asyncio.wait_for(bus.call(Message(
                destination=UDISKS, path=path, interface=interface, member=member,
                signature=signature, body=body or [])), DBUS_TIMEOUT_S)
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError(f"{member}: {reply.error_name}")
            return _plain(reply.body)

        objects = (await call(UDISKS_ROOT, "org.freedesktop.DBus.ObjectManager",
                              "GetManagedObjects"))[0]
        path, interfaces = find_drive(objects, drive_id, serial)
        if not path:
            return {"unavailable": "UDisks2 does not list the data disk"}
        if NVME_IFACE in interfaces:
            attrs = None
            try:
                attrs = (await call(path, NVME_IFACE, "SmartGetAttributes", "a{sv}", [{}]))[0]
            except Exception as err:  # noqa: BLE001 — the properties still help
                _LOGGER.debug("NVMe SmartGetAttributes failed: %s", err)
            return health_from_nvme(interfaces[NVME_IFACE], attrs)
        if ATA_IFACE in interfaces:
            attributes = None
            try:
                attributes = (await call(path, ATA_IFACE, "SmartGetAttributes", "a{sv}", [{}]))[0]
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("ATA SmartGetAttributes failed: %s", err)
            return health_from_ata(interfaces[ATA_IFACE], attributes)
        return {"unavailable": "the host's UDisks2 has no SMART interface for this drive "
                               "(SD, eMMC, USB, or built without SMART support)"}
    except Exception as err:  # noqa: BLE001 — never lose a snapshot over it
        return {"unavailable": f"UDisks2 not readable: {type(err).__name__}"}
    finally:
        if bus is not None:
            try:
                bus.disconnect()
            except Exception:  # noqa: BLE001
                pass


# --- putting it together ------------------------------------------------------

def sysfs_health(device: str | None, kind: str, root: str = "") -> dict:
    """BLOCKING. What sysfs alone can say about the disk."""
    out: dict = {}
    if device and kind == "emmc":
        out.update(emmc_life(device, root))
    temp = disk_temp_c(device, root)
    if temp is not None:
        out["temperature_c"] = temp
    return out


def merge_health(storage: dict | None, udisks: dict, sysfs: dict, now: float) -> dict:
    """One `disk_health` section. UDisks answers win; sysfs fills gaps; the
    sources list says where each came from, so "no data" is never confused
    with "a healthy zero"."""
    storage = storage or {}
    health: dict = {"device": storage.get("device"), "serial": storage.get("serial"),
                    "type": storage.get("type"), "sources": [],
                    "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}
    real = {k: v for k, v in (udisks or {}).items() if k != "unavailable"}
    if real:
        health.update({k: v for k, v in real.items() if k != "type"})
        health["sources"].append("udisks2")
    for key, value in (sysfs or {}).items():
        if key not in health or health[key] is None:
            health[key] = value
    if sysfs:
        health["sources"].append("sysfs")
    if not health["sources"]:
        health["unavailable"] = ("SD cards report no health data" if storage.get("type") == "sd"
                                 else (udisks or {}).get("unavailable")
                                 or "no health data reachable for this disk")
    return health
