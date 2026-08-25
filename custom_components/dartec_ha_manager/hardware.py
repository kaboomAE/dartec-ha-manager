"""Hardware identification — what box is this home actually running on?

HA OS/Supervised: the Supervisor knows its board ("green", "yellow", "rpi4-64",
"generic-x86-64") and OS/kernel versions. Everything else — and the CPU model
itself — comes from /proc and /sys, which are host-scoped even inside the HA
container, so Container/Core installs get real hardware detail too.
"""
from __future__ import annotations

import logging
import os
import re

_LOGGER = logging.getLogger(__name__)

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


def _cpu_model() -> tuple[str | None, int | None]:
    """(model name, core count) from /proc/cpuinfo — works on x86 and ARM."""
    text = _read("/proc/cpuinfo")
    if not text:
        return None, None
    model = None
    for key in ("model name", "Model", "Processor", "cpu model"):
        match = re.search(rf"^{key}\s*:\s*(.+)$", text, re.MULTILINE)
        if match:
            model = match.group(1).strip()
            break
    if model is None:
        # ARM boards often only expose implementer/part codes; fall back to Hardware
        match = re.search(r"^Hardware\s*:\s*(.+)$", text, re.MULTILINE)
        model = match.group(1).strip() if match else None
    cores = len(re.findall(r"^processor\s*:", text, re.MULTILINE)) or None
    return model, cores


def _board_model() -> str | None:
    """Physical board/product name: device tree (ARM/SBC) or DMI (x86)."""
    for path in ("/sys/firmware/devicetree/base/model",
                 "/proc/device-tree/model",
                 "/sys/class/dmi/id/product_name"):
        value = _read(path)
        if value and value.lower() not in ("to be filled by o.e.m.", "system product name", "default string"):
            return value
    return None


def collect_hardware(supervisor: dict | None = None) -> dict:
    """supervisor: parsed os/host info when available (HA OS/Supervised)."""
    info: dict = {}
    try:
        import platform

        cpu_model, cores = _cpu_model()
        info["cpu_model"] = cpu_model
        info["cpu_cores"] = cores
        info["architecture"] = platform.machine() or None
        info["board_model"] = _board_model()
        info["kernel"] = platform.release() or None

        try:
            import psutil

            info["memory_total_mb"] = round(psutil.virtual_memory().total / 1024 / 1024)
            freq = psutil.cpu_freq()
            info["cpu_mhz"] = round(freq.max or freq.current) if freq else None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("psutil hardware detail failed: %s", err)

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
            info["os_name"] = _os_release_name()

        info["docker"] = os.path.exists("/.dockerenv")
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("hardware collect failed: %s", err)
    return info


def _os_release_name() -> str | None:
    text = _read("/etc/os-release")
    if not text:
        return None
    match = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', text, re.MULTILINE)
    return match.group(1) if match else None
