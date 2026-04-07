"""System resource helpers."""
from __future__ import annotations

import platform
import shutil

import psutil


def system_info() -> dict:
    vm = psutil.virtual_memory()
    disk = shutil.disk_usage(".")
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "python": platform.python_version(),
        "cpu_cores": psutil.cpu_count(logical=True),
        "ram_total_gb": round(vm.total / 1024**3, 1),
        "ram_available_gb": round(vm.available / 1024**3, 1),
        "disk_free_gb": round(disk.free / 1024**3, 1),
    }


def concurrency_ceiling() -> int:
    """Conservative async concurrency based on available RAM."""
    available_gb = psutil.virtual_memory().available / 1024**3
    cores = psutil.cpu_count(logical=True) or 2
    # Each scraper task ~ 20 MB peak; cap at 20 or cores*2
    by_ram = max(1, int(available_gb * 1024 / 20))
    return min(by_ram, cores * 2, 20)
