"""Reading a disk's health and the machine's identity without new privileges.

The failure worth guarding against is the quiet one: a missing reading turned
into a healthy-looking zero. An SD card that reports nothing must say so, and
a drive the host has never read must not look like a drive with no errors.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"
                       / "dartec_ha_manager"))

import disk_health  # noqa: E402
from disk_health import (base_device, default_route_mac, disk_temp_c, emmc_life,  # noqa: E402
                         find_drive, health_from_ata, health_from_nvme, merge_health,
                         pick_data_drive, primary_mac, soc_temp_c, storage_type,
                         sysfs_storage)


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestDevices:
    def test_partitions_resolve_to_their_disk(self):
        assert base_device("/dev/nvme0n1p8") == "nvme0n1"
        assert base_device("/dev/mmcblk0p8") == "mmcblk0"
        assert base_device("/dev/sda8") == "sda"
        assert base_device("mmcblk1") == "mmcblk1"
        assert base_device(None) is None

    def test_an_sd_card_and_an_emmc_are_told_apart(self, tmp_path):
        write(tmp_path, "/sys/block/mmcblk0/device/type", "SD\n")
        write(tmp_path, "/sys/block/mmcblk1/device/type", "MMC\n")
        assert storage_type("mmcblk0", root=str(tmp_path)) == "sd"
        assert storage_type("mmcblk1", root=str(tmp_path)) == "emmc"

    def test_without_a_type_file_removable_means_sd(self, tmp_path):
        assert storage_type("mmcblk0", root=str(tmp_path), removable=True) == "sd"
        assert storage_type("mmcblk0", root=str(tmp_path), removable=False) == "emmc"

    def test_a_usb_disk_is_a_usb_disk_whatever_is_inside(self, tmp_path):
        assert storage_type("sda", root=str(tmp_path), bus="usb") == "usb"

    def test_spinning_and_flash_sata(self, tmp_path):
        write(tmp_path, "/sys/block/sda/queue/rotational", "1\n")
        write(tmp_path, "/sys/block/sdb/queue/rotational", "0\n")
        assert storage_type("sda", root=str(tmp_path)) == "hdd"
        assert storage_type("sdb", root=str(tmp_path)) == "ssd"
        assert storage_type("nvme0n1", root=str(tmp_path)) == "nvme"


class TestEmmc:
    def test_life_time_is_the_worse_cell_type_as_a_lower_bound(self, tmp_path):
        write(tmp_path, "/sys/block/mmcblk0/device/life_time", "0x02 0x09\n")
        write(tmp_path, "/sys/block/mmcblk0/device/pre_eol_info", "0x01\n")
        assert emmc_life("mmcblk0", str(tmp_path)) == {"wear_pct": 80, "pre_eol": "normal"}

    def test_exceeded_reads_as_worn_out(self, tmp_path):
        write(tmp_path, "/sys/block/mmcblk0/device/life_time", "0x0B 0x01\n")
        write(tmp_path, "/sys/block/mmcblk0/device/pre_eol_info", "0x03\n")
        assert emmc_life("mmcblk0", str(tmp_path)) == {"wear_pct": 100, "pre_eol": "urgent"}

    def test_a_card_without_the_files_reports_nothing(self, tmp_path):
        assert emmc_life("mmcblk0", str(tmp_path)) == {}


class TestTemperatures:
    def test_an_nvme_drives_own_sensor(self, tmp_path):
        write(tmp_path, "/sys/block/nvme0n1/device/hwmon2/temp1_input", "41850\n")
        assert disk_temp_c("nvme0n1", str(tmp_path)) == 41.85

    def test_the_nvme_controller_path_when_the_namespace_has_none(self, tmp_path):
        write(tmp_path, "/sys/class/nvme/nvme0/hwmon3/temp1_input", "39000\n")
        assert disk_temp_c("nvme0n1", str(tmp_path)) == 39.0

    def test_a_sata_drive_through_drivetemp(self, tmp_path):
        write(tmp_path, "/sys/block/sda/device/hwmon/hwmon4/temp1_input", "36000\n")
        assert disk_temp_c("sda", str(tmp_path)) == 36.0

    def test_no_sensor_is_none_not_zero(self, tmp_path):
        write(tmp_path, "/sys/block/mmcblk0/device/hwmon0/temp1_input", "0\n")
        assert disk_temp_c("mmcblk0", str(tmp_path)) is None
        assert disk_temp_c(None, str(tmp_path)) is None

    def test_the_processor_is_found_by_its_chip_name(self, tmp_path):
        write(tmp_path, "/sys/class/hwmon/hwmon0/name", "nvme\n")
        write(tmp_path, "/sys/class/hwmon/hwmon0/temp1_input", "40000\n")
        write(tmp_path, "/sys/class/hwmon/hwmon1/name", "cpu_thermal\n")
        write(tmp_path, "/sys/class/hwmon/hwmon1/temp1_input", "52300\n")
        assert soc_temp_c(str(tmp_path)) == 52.3

    def test_an_arm_board_without_a_named_chip_uses_thermal_zone_0(self, tmp_path):
        write(tmp_path, "/sys/class/thermal/thermal_zone0/temp", "48000\n")
        assert soc_temp_c(str(tmp_path)) == 48.0


class TestSupervisorIdentity:
    def test_the_primary_interface_carries_the_mac(self):
        network = {"interfaces": [
            {"interface": "wlan0", "type": "wireless", "primary": False, "mac": "AA:AA:AA:AA:AA:AA"},
            {"interface": "end0", "type": "ethernet", "primary": True, "mac": "2C:CF:67:12:34:56"}]}
        assert primary_mac(network) == "2C:CF:67:12:34:56"

    def test_the_data_disk_is_found_by_its_supervisor_id(self):
        info = {"drives": [
            {"id": "Generic-USB-1", "filesystems": [{"device": "/dev/sda1", "name": "backup"}]},
            {"id": "Samsung-SSD-980-S64D", "model": "Samsung SSD 980", "serial": "S64D",
             "size": 500107862016, "connection_bus": "",
             "filesystems": [{"device": "/dev/nvme0n1p8", "name": "hassos-data"}]}]}
        drive = pick_data_drive(info, "Samsung-SSD-980-S64D")
        assert drive["serial"] == "S64D"

    def test_without_an_id_the_hassos_data_partition_decides(self):
        info = {"drives": [
            {"id": "a", "filesystems": [{"device": "/dev/sda1", "name": "backup"}]},
            {"id": "b", "filesystems": [{"device": "/dev/mmcblk0p8", "name": "hassos-data"}]}]}
        assert pick_data_drive(info, None)["id"] == "b"

    def test_an_older_supervisors_device_path_works_too(self):
        info = {"drives": [{"id": "x", "filesystems": [{"device": "/dev/mmcblk0p8"}]}]}
        assert pick_data_drive(info, "/dev/mmcblk0")["id"] == "x"

    def test_two_unidentified_drives_are_not_guessed_between(self):
        info = {"drives": [{"id": "a", "filesystems": []}, {"id": "b", "filesystems": []}]}
        assert pick_data_drive(info, None) is None


class TestUdisks:
    DRIVE = {"Id": "Samsung-SSD-980-S64D", "Serial": "S64D", "Model": "Samsung SSD 980"}

    def test_the_drive_is_found_by_id_or_serial(self):
        objects = {"/org/freedesktop/UDisks2/drives/other": {disk_health.DRIVE_IFACE: {"Id": "x"}},
                   "/org/freedesktop/UDisks2/drives/s": {disk_health.DRIVE_IFACE: self.DRIVE}}
        assert find_drive(objects, "Samsung-SSD-980-S64D", None)[0].endswith("/s")
        assert find_drive(objects, None, "S64D")[0].endswith("/s")
        assert find_drive(objects, "nope", "nope") == (None, {})

    def test_an_nvme_health_log(self):
        props = {"SmartCriticalWarning": [], "SmartPowerOnHours": 2211,
                 "SmartTemperature": 320, "SmartUpdated": 1789700000}
        attrs = {"percent_used": 3, "avail_spare": 100, "spare_thresh": 10, "media_errors": 0,
                 "num_err_log_entries": 4, "unsafe_shutdowns": 7, "power_cycles": 60,
                 "total_data_written": 1_840_000_000_000, "wctemp": 355, "cctemp": 358}
        health = health_from_nvme(props, attrs)
        assert health["wear_pct"] == 3
        assert health["critical_warning"] == 0
        assert health["temperature_c"] == 46.9
        assert health["temperature_warn_c"] == 81.9
        assert health["media_errors"] == 0
        assert health["data_written_tb"] == 1.84
        assert health["power_on_hours"] == 2211

    def test_nvme_warnings_become_the_specs_bits(self):
        health = health_from_nvme({"SmartCriticalWarning": ["spare", "degraded"]}, None)
        assert health["critical_warning"] == 0x05

    def test_an_ata_drive_never_read_is_not_a_healthy_drive(self):
        """SmartUpdated 0: udisksd has not read it, so every counter is a
        default, not a measurement."""
        health = health_from_ata({"SmartSupported": True, "SmartEnabled": True,
                                  "SmartUpdated": 0, "SmartNumBadSectors": 0}, None)
        assert "reallocated_sectors" not in health
        assert "unavailable" in health

    def test_an_ata_drives_attributes(self):
        props = {"SmartSupported": True, "SmartEnabled": True, "SmartUpdated": 1789700000,
                 "SmartFailing": False, "SmartPowerOnSeconds": 3600 * 900,
                 "SmartTemperature": 311.15, "SmartNumBadSectors": 0}
        rows = [(5, "reallocated-sector-count", 0, 100, 100, 10, 2, 3, {}),
                (197, "current-pending-sector", 0, 100, 100, 0, 1, 3, {}),
                (231, "ssd-life-left", 0, 93, 93, 0, 93, 1, {})]
        health = health_from_ata(props, rows)
        assert health["reallocated_sectors"] == 2
        assert health["pending_sectors"] == 1
        assert health["wear_pct"] == 7
        assert health["power_on_hours"] == 900
        assert health["temperature_c"] == 38.0
        assert health["smart_failing"] is False


class TestMerge:
    def test_an_sd_card_says_it_reports_nothing(self):
        health = merge_health({"type": "sd", "device": "mmcblk0"},
                              {"unavailable": "no SMART interface"}, {}, 0)
        assert health["unavailable"] == "SD cards report no health data"
        assert health["sources"] == []
        assert "wear_pct" not in health

    def test_udisks_wins_and_sysfs_fills_the_gaps(self):
        health = merge_health({"type": "nvme", "device": "nvme0n1", "serial": "S"},
                              {"wear_pct": 3, "temperature_c": 44.0},
                              {"temperature_c": 47.0}, 0)
        assert health["temperature_c"] == 44.0
        assert health["wear_pct"] == 3
        assert health["sources"] == ["udisks2", "sysfs"]
        assert "unavailable" not in health

    def test_emmc_from_sysfs_alone(self):
        health = merge_health({"type": "emmc", "device": "mmcblk0"},
                              {"unavailable": "no SMART interface"},
                              {"wear_pct": 20, "pre_eol": "normal"}, 0)
        assert health["wear_pct"] == 20
        assert health["sources"] == ["sysfs"]


class TestContainerInstalls:
    def test_storage_from_sysfs(self, tmp_path):
        write(tmp_path, "/sys/block/nvme0n1/size", "976773168\n")
        write(tmp_path, "/sys/block/nvme0n1/removable", "0\n")
        write(tmp_path, "/sys/block/nvme0n1/device/model", "WD Blue SN570 1TB\n")
        write(tmp_path, "/sys/block/nvme0n1/device/serial", "WD123\n")
        storage = sysfs_storage("nvme0n1", str(tmp_path))
        assert storage["model"] == "WD Blue SN570 1TB"
        assert storage["size_bytes"] == 976773168 * 512
        assert storage["type"] == "nvme"

    @pytest.mark.skipif(not hasattr(os, "makedev"), reason="device numbers are POSIX")
    def test_a_virtual_filesystem_is_no_disk(self, tmp_path):
        """The live CI job: /config on a container's overlay has device 0:49,
        which is not a block device and must not be reported as one."""
        class Stat:
            st_dev = os.makedev(0, 49)

        assert disk_health.config_device("/config", str(tmp_path), stat=lambda _p: Stat()) is None
        assert sysfs_storage(None, str(tmp_path)) is None

    @pytest.mark.skipif(not hasattr(os, "makedev"), reason="device numbers are POSIX")
    def test_a_block_device_without_a_sysfs_entry_is_no_disk(self, tmp_path):
        class Stat:
            st_dev = os.makedev(259, 3)

        assert disk_health.config_device("/config", str(tmp_path), stat=lambda _p: Stat()) is None

    def test_the_default_routes_interface_gives_the_mac(self, tmp_path):
        write(tmp_path, "/proc/net/route",
              "Iface\tDestination\tGateway\n"
              "docker0\t0000FEA9\t00000000\n"
              "eth0\t00000000\t0101A8C0\n")
        write(tmp_path, "/sys/class/net/eth0/address", "a8:a1:59:00:00:02\n")
        assert default_route_mac(str(tmp_path)) == "a8:a1:59:00:00:02"
