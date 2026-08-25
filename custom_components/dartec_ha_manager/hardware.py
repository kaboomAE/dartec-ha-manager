"""Hardware identification — what box is this home actually running on?

HA OS/Supervised: the Supervisor knows its board ("green", "yellow", "rpi4-64",
"generic-x86-64") and OS/kernel versions. Everything else — and the CPU model
itself — comes from /proc and /sys, which are host-scoped even inside the HA
container, so Container/Core installs get real hardware detail too.

THREADING: every function here touches the filesystem and must run in an
executor, never the event loop. Callers use `async_collect_hardware`, which
handles that and caches the static half — CPU model and board do not change
while the process runs, so they are read once rather than every 60 s.
"""
from __future__ import annotations

import logging
import os
import re

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Populated on first use inside an executor; static for the process lifetime.
_STATIC_CACHE: dict | None = None

# Supervisor board slug -> friendly product name
BOARD_NAMES = {
    "green": "Home Assistant Green",
    "yellow": "Home Assistant Yellow",
    "yellow-nvme": "Home Assistant Yellow (NVMe)",
    "odroid-n2": "ODROID-N2 / HA Blue",
    "odroid-m1": "ODROID-M1",
    "odroid-c2": "ODROID-C2",
    "odroid-c4": "ODROID-C4",
    "odroid-xu4": "ODROID-XU4",
    "rpi3": "Raspberry Pi 3",
    "rpi3-64": "Raspberry Pi 3 (64-bit)",
    "rpi4": "Raspberry Pi 4",
    "rpi4-64": "Raspberry Pi 4 (64-bit)",
    "rpi5-64": "Raspberry Pi 5 (64-bit)",
    "generic-x86-64": "Generic x86-64",
    "tinker": "ASUS Tinker Board",
    "khadas-vim3": "Khadas VIM3",
    "yellow-cm5": "Home Assistant Yellow (CM5)",
}


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip("\x00 \n\r\t") or None
    except OSError:
        return None


# ARM cores publish numeric implementer/part codes instead of a model string.
# Verified need: a real Home Assistant Green (Rockchip RK3566) reported no
# "model name"/"Hardware" line at all, so CPU identification came back empty.
ARM_IMPLEMENTERS = {
    "0x41": "ARM", "0x42": "Broadcom", "0x43": "Cavium", "0x4e": "NVIDIA",
    "0x51": "Qualcomm", "0x53": "Samsung", "0x61": "Apple",
}
ARM_PARTS = {
    "0xc07": "Cortex-A7", "0xc08": "Cortex-A8", "0xc09": "Cortex-A9",
    "0xc0d": "Cortex-A12", "0xc0e": "Cortex-A17", "0xc0f": "Cortex-A15",
    "0xd01": "Cortex-A32", "0xd03": "Cortex-A53", "0xd04": "Cortex-A35",
    "0xd05": "Cortex-A55", "0xd06": "Cortex-A65", "0xd07": "Cortex-A57",
    "0xd08": "Cortex-A72", "0xd09": "Cortex-A73", "0xd0a": "Cortex-A75",
    "0xd0b": "Cortex-A76", "0xd0c": "Neoverse-N1", "0xd0d": "Cortex-A77",
    "0xd41": "Cortex-A78", "0xd42": "Cortex-A78AE", "0xd44": "Cortex-X1",
    "0xd46": "Cortex-A510", "0xd47": "Cortex-A710", "0xd48": "Cortex-X2",
    "0xd4d": "Cortex-A715", "0xd4e": "Cortex-X3",
}


def _soc_name() -> str | None:
    """SoC from the device tree's compatible list, e.g. 'rockchip,rk3566'."""
    raw = _read("/sys/firmware/devicetree/base/compatible") or \
        _read("/proc/device-tree/compatible")
    if not raw:
        return None
    # NUL-separated list, most specific first; the SoC is usually the last entry.
    entries = [part for part in raw.split("\x00") if part.strip()]
    return entries[-1].strip() if entries else None


def _cpu_model() -> tuple[str | None, int | None]:
    """(model name, core count) from /proc/cpuinfo — works on x86 and ARM."""
    text = _read("/proc/cpuinfo")
    if not text:
        return None, None
    cores = len(re.findall(r"^processor\s*:", text, re.MULTILINE)) or None

    for key in ("model name", "Model", "Processor", "cpu model"):
        match = re.search(rf"^{key}\s*:\s*(.+)$", text, re.MULTILINE)
        if match:
            return match.group(1).strip(), cores

    # Some ARM boards expose a board string here instead.
    match = re.search(r"^Hardware\s*:\s*(.+)$", text, re.MULTILINE)
    if match:
        return match.group(1).strip(), cores

    # Neither present (typical on mainline-kernel ARM64): decode the codes.
    implementer = re.search(r"^CPU implementer\s*:\s*(\S+)$", text, re.MULTILINE)
    part = re.search(r"^CPU part\s*:\s*(\S+)$", text, re.MULTILINE)
    if part:
        vendor = ARM_IMPLEMENTERS.get((implementer.group(1).lower() if implementer else ""), "")
        core_name = ARM_PARTS.get(part.group(1).lower(), f"part {part.group(1)}")
        soc = _soc_name()
        name = " ".join(filter(None, [vendor, core_name]))
        return (f"{name} ({soc})" if soc else name), cores

    return None, cores


def _board_model() -> str | None:
    """Physical board/product name: device tree (ARM/SBC) or DMI (x86)."""
    for path in ("/sys/firmware/devicetree/base/model",
                 "/proc/device-tree/model",
                 "/sys/class/dmi/id/product_name"):
        value = _read(path)
        if value and value.lower() not in ("to be filled by o.e.m.", "system product name", "default string"):
            return value
    return None


def _collect_static() -> dict:
    """BLOCKING — reads /proc, /sys and /etc. Executor only."""
    info: dict = {}
    try:
        import platform

        cpu_model, cores = _cpu_model()
        info["cpu_model"] = cpu_model
        info["cpu_cores"] = cores
        info["architecture"] = platform.machine() or None
        info["board_model"] = _board_model()
        info["kernel"] = platform.release() or None
        info["os_release_name"] = _os_release_name()
        info["docker"] = os.path.exists("/.dockerenv")

        try:
            import psutil

            info["memory_total_mb"] = round(psutil.virtual_memory().total / 1024 / 1024)
            freq = psutil.cpu_freq()
            info["cpu_mhz"] = round(freq.max or freq.current) if freq else None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("psutil hardware detail failed: %s", err)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("static hardware collect failed: %s", err)
    return info


async def async_collect_hardware(hass: HomeAssistant, supervisor: dict | None = None) -> dict:
    """supervisor: parsed os/host info when available (HA OS/Supervised)."""
    global _STATIC_CACHE

    if _STATIC_CACHE is None:
        _STATIC_CACHE = await hass.async_add_executor_job(_collect_static)
    info = dict(_STATIC_CACHE)
    os_release_name = info.pop("os_release_name", None)

    try:
        if supervisor:
            board = supervisor.get("board")
            info["board"] = board
            info["product"] = BOARD_NAMES.get(board, board)
            info["os_version"] = supervisor.get("os_version")
            info["os_name"] = supervisor.get("operating_system")
            info["supervisor_version"] = supervisor.get("supervisor_version")
            info["disk_life_time"] = supervisor.get("disk_life_time")
            if supervisor.get("kernel"):
                info["kernel"] = supervisor["kernel"]
        else:
            # Container/Core: derive a product name from the board/DMI string
            info["product"] = info.get("board_model") or (
                f"{info.get('architecture') or 'unknown'} host (container install)")
            info["os_name"] = os_release_name
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("hardware merge failed: %s", err)
    return info


def _os_release_name() -> str | None:
    text = _read("/etc/os-release")
    if not text:
        return None
    match = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', text, re.MULTILINE)
    return match.group(1) if match else None
