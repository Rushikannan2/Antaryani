"""Real Windows system metrics for the dashboard and the status tool.

Everything here reads live data (psutil, ctypes against ``nvml.dll``).
No shell, no subprocess, no model-generated commands. A malformed or
unavailable source yields ``None``/unavailable fields - never invented
numbers, never an exception escaping to the caller.
"""

from __future__ import annotations

import math
import os
import platform
import socket
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import psutil

_GB = 1024**3

_GROUPS = (
    "cpu",
    "ram",
    "storage",
    "battery",
    "network",
    "gpu",
    "system",
    "time",
)

_LABELS = {
    "cpu": "CPU",
    "ram": "RAM",
    "storage": "Storage",
    "battery": "Battery",
    "network": "Network",
    "gpu": "GPU",
    "system": "System",
    "time": "Time",
}

# Sentinel: distinguish "use the real default" from an explicit ``None``.
_DEFAULT: Any = object()


# ----------------------------------------------------------------------
# sanitizers
# ----------------------------------------------------------------------
def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _percent(value: Any) -> float | None:
    if not _is_number(value):
        return None
    number = float(value)
    if 0.0 <= number <= 100.0:
        return number
    return None


def _number(value: Any) -> float | None:
    if not _is_number(value):
        return None
    number = float(value)
    return number if number >= 0.0 else None


def _int(value: Any) -> int | None:
    number = _number(value)
    if number is None:
        return None
    return int(number)


def _text(value: Any) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str) and value:
        return value
    return None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _strlist(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


# Per-group field schemas. Every collected group is passed through its
# schema so a provider returning garbage cannot leak nonsense upward.
_SCHEMA: dict[str, dict[str, Callable[[Any], Any]]] = {
    "cpu": {
        "percent": _percent,
        "cores": _int,
        "threads": _int,
        "frequency_ghz": _number,
    },
    "ram": {
        "total_gb": _number,
        "used_gb": _number,
        "available_gb": _number,
        "percent": _percent,
    },
    "storage": {
        "drive": _text,
        "total_gb": _number,
        "used_gb": _number,
        "free_gb": _number,
        "percent": _percent,
    },
    "battery": {
        "percent": _percent,
        "charging": _bool,
        "ac_connected": _bool,
        "status": _text,
    },
    "network": {
        "connected": _bool,
        "interfaces": _strlist,
        "upload_mbps": _number,
        "download_mbps": _number,
    },
    "gpu": {
        "name": _text,
        "percent": _percent,
        "vram_used_gb": _number,
        "vram_total_gb": _number,
        "temperature_c": _number,
    },
    "system": {
        "os": _text,
        "hostname": _text,
        "uptime_hours": _number,
    },
    "time": {
        "date": _text,
        "time": _text,
        "timezone": _text,
    },
}


def _sanitize(group: str, raw: dict) -> dict:
    """Coerce a provider payload into the group's schema."""
    if not isinstance(raw, dict):
        return {
            "available": False,
            "detail": f"{_LABELS[group]} metrics unavailable",
        }
    out = {
        field: validator(raw.get(field)) for field, validator in _SCHEMA[group].items()
    }
    if "available" in raw:
        out["available"] = _bool(raw.get("available"))
        if out["available"] is None:
            out["available"] = False
    else:
        out["available"] = True
    detail = raw.get("detail")
    if detail is not None:
        out["detail"] = _text(detail)
    return out


# ----------------------------------------------------------------------
# individual providers
# ----------------------------------------------------------------------
def cpu_metrics(interval: float | None = None) -> dict:
    percent = psutil.cpu_percent(interval=interval)
    frequency = psutil.cpu_freq()
    ghz = None
    if frequency is not None:
        mhz = frequency.max or frequency.current
        if _is_number(mhz) and mhz > 0:
            ghz = mhz / 1000.0
    return {
        "percent": _percent(percent),
        "cores": _int(psutil.cpu_count(logical=False)),
        "threads": _int(psutil.cpu_count(logical=True)),
        "frequency_ghz": _number(ghz),
    }


def ram_metrics() -> dict:
    virtual = psutil.virtual_memory()
    return {
        "total_gb": virtual.total / _GB,
        "used_gb": virtual.used / _GB,
        "available_gb": virtual.available / _GB,
        "percent": _percent(virtual.percent),
    }


def storage_metrics(*, usage: Any = None, drive: str | None = None) -> dict:
    if usage is None:
        system_drive = os.environ.get("SYSTEMDRIVE", "C:")
        root = f"{system_drive}\\"
        usage = shutil_disk_usage(root)
        if drive is None:
            drive = system_drive
    if drive is None:
        drive = "C:"
    total = getattr(usage, "total", 0)
    used = getattr(usage, "used", 0)
    free = getattr(usage, "free", 0)
    percent = None
    if _is_number(total) and total > 0 and _is_number(used):
        percent = _percent(used / total * 100.0)
    return {
        "drive": _text(drive),
        "total_gb": _number(total / _GB if _is_number(total) else None),
        "used_gb": _number(used / _GB if _is_number(used) else None),
        "free_gb": _number(free / _GB if _is_number(free) else None),
        "percent": percent,
    }


def shutil_disk_usage(path: str) -> Any:
    import shutil

    return shutil.disk_usage(path)


def battery_metrics(battery: Any = _DEFAULT) -> dict:
    if battery is _DEFAULT:
        battery = psutil.sensors_battery()
    if battery is None:
        return {"available": False, "detail": "Battery metrics unavailable"}
    percent = _percent(getattr(battery, "percent", None))
    if percent is None:
        return {"available": False, "detail": "Battery metrics unavailable"}
    plugged = getattr(battery, "power_plugged", None)
    secsleft = getattr(battery, "secsleft", None)
    ac_connected = plugged is True
    if ac_connected:
        unlimited = (
            secsleft is not None
            and isinstance(secsleft, float)
            and math.isinf(secsleft)
        )
        status = "full" if unlimited else "charging"
    else:
        status = "discharging"
    return {
        "available": True,
        "percent": percent,
        "charging": ac_connected and status == "charging",
        "ac_connected": ac_connected,
        "status": status,
    }


# Network throughput baseline: (monotonic timestamp, bytes sent, bytes recv).
_net_baseline: tuple[float, int, int] | None = None


def network_metrics(
    *, stats: Any = None, io: Any = None, now: float | None = None, reset: bool = False
) -> dict:
    global _net_baseline
    if stats is None:
        stats = psutil.net_if_stats()
    if io is None:
        io = psutil.net_io_counters()
    if now is None:
        now = time.monotonic()

    up_interfaces: list[str] = []
    connected = False
    if isinstance(stats, dict):
        for name, info in stats.items():
            is_up = getattr(info, "is_up", False)
            if is_up is True:
                connected = True
                up_interfaces.append(str(name))

    sent = getattr(io, "bytes_sent", None)
    recv = getattr(io, "bytes_recv", None)
    upload = download = None

    have_counters = _is_number(sent) and _is_number(recv) and sent >= 0 and recv >= 0
    if have_counters and not reset:
        if _net_baseline is None:
            _net_baseline = (now, int(sent), int(recv))
        else:
            prev_now, prev_sent, prev_recv = _net_baseline
            elapsed = now - prev_now
            if elapsed > 0:
                upload = (sent - prev_sent) * 8 / elapsed / 1_000_000
                download = (recv - prev_recv) * 8 / elapsed / 1_000_000
                if upload < 0:
                    upload = None
                if download < 0:
                    download = None
            _net_baseline = (now, int(sent), int(recv))
    elif have_counters and reset:
        _net_baseline = (now, int(sent), int(recv))

    return {
        "connected": connected,
        "interfaces": sorted(up_interfaces),
        "upload_mbps": _number(upload),
        "download_mbps": _number(download),
    }


def system_metrics() -> dict:
    try:
        os_name = " ".join(platform.platform().split()[:2]) or platform.system()
    except Exception:
        os_name = None
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = None
    uptime_hours = None
    try:
        uptime_hours = (time.time() - psutil.boot_time()) / 3600.0
    except Exception:
        uptime_hours = None
    return {
        "os": os_name,
        "hostname": hostname,
        "uptime_hours": _number(uptime_hours),
    }


def clock_metrics() -> dict:
    now = datetime.now().astimezone()
    tzname = None
    try:
        tzname = now.tzname()
    except Exception:
        tzname = None
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "timezone": tzname,
    }


# Public descriptive alias used by the dashboard and voice tools.
time_metrics = clock_metrics


# ----------------------------------------------------------------------
# GPU (ctypes NVML, no pynvml package, no shell)
# ----------------------------------------------------------------------
class _NvmlBackend:
    """Minimal NVML wrapper exposing the four fields the dashboard shows."""

    def __init__(self, dll: Any, handle: Any) -> None:
        self._dll = dll
        self._handle = handle

    def name(self) -> bytes:
        import ctypes

        buffer = ctypes.create_string_buffer(96)
        rc = self._dll.nvmlDeviceGetName(self._handle, buffer, 96)
        if rc != 0:
            raise RuntimeError(f"nvmlDeviceGetName rc={rc}")
        return buffer.value

    def utilization(self) -> int:
        import ctypes

        class Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        util = Util()
        rc = self._dll.nvmlDeviceGetUtilizationRates(self._handle, ctypes.byref(util))
        if rc != 0:
            raise RuntimeError(f"nvmlDeviceGetUtilizationRates rc={rc}")
        return int(util.gpu)

    def memory(self) -> tuple[int, int]:
        import ctypes

        class Mem(ctypes.Structure):
            _fields_ = [
                ("total", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong),
                ("free", ctypes.c_ulonglong),
                ("reserved", ctypes.c_ulonglong),
                ("used_device", ctypes.c_ulonglong),
            ]

        mem = Mem()
        rc = self._dll.nvmlDeviceGetMemoryInfo(self._handle, ctypes.byref(mem))
        if rc != 0:
            raise RuntimeError(f"nvmlDeviceGetMemoryInfo rc={rc}")
        return (int(mem.used), int(mem.total))

    def temperature(self) -> int:
        import ctypes

        temp = ctypes.c_uint()
        rc = self._dll.nvmlDeviceGetTemperature(self._handle, 0, ctypes.byref(temp))
        if rc != 0:
            raise RuntimeError(f"nvmlDeviceGetTemperature rc={rc}")
        return int(temp.value)


def _load_nvml() -> Any:
    try:
        import ctypes

        dll = ctypes.windll.nvml
        if dll.nvmlInit_v2() != 0:
            return None
        count = ctypes.c_uint()
        if dll.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0 or count.value < 1:
            return None
        handle = ctypes.c_void_p()
        if dll.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
            return None
        return _NvmlBackend(dll, handle)
    except Exception:
        return None


def _safe(callable_: Callable[[], Any]) -> Any:
    try:
        return callable_()
    except Exception:
        return None


def gpu_metrics(backend: Any = _DEFAULT) -> dict:
    if backend is _DEFAULT:
        backend = _load_nvml()
    if backend is None:
        return {"available": False, "detail": "GPU metrics unavailable"}

    name = _text(_safe(backend.name))
    percent = _percent(_safe(backend.utilization))
    memory = _safe(backend.memory)
    vram_used = vram_total = None
    if isinstance(memory, (list, tuple)) and len(memory) == 2:
        used, total = memory
        if _is_number(used) and used >= 0:
            vram_used = used / _GB
        if _is_number(total) and total >= 0:
            vram_total = total / _GB
    temperature = _number(_safe(backend.temperature))
    return {
        "available": True,
        "name": name,
        "percent": percent,
        "vram_used_gb": vram_used,
        "vram_total_gb": vram_total,
        "temperature_c": temperature,
    }


# ----------------------------------------------------------------------
# aggregation
# ----------------------------------------------------------------------
def _default_providers(cpu_interval: float | None) -> dict[str, Callable[[], dict]]:
    def cpu() -> dict:
        interval = 0.1 if cpu_interval is None else cpu_interval
        return cpu_metrics(interval=interval)

    return {
        "cpu": cpu,
        "ram": ram_metrics,
        "storage": storage_metrics,
        "battery": battery_metrics,
        "network": network_metrics,
        "gpu": gpu_metrics,
        "system": system_metrics,
        "time": clock_metrics,
    }


def collect_system_metrics(
    providers: dict[str, Callable[[], dict]] | None = None,
    cpu_interval: float | None = None,
) -> dict:
    """Collect and sanitize every metric group.

    A provider that raises yields ``{"available": False, "detail": ...}``
    for its own group only; the remaining groups still collect.
    """
    merged = _default_providers(cpu_interval)
    if providers:
        merged.update(providers)

    out: dict[str, dict] = {}
    for group in _GROUPS:
        provider = merged.get(group)
        if provider is None:
            out[group] = {
                "available": False,
                "detail": f"{_LABELS[group]} metrics unavailable",
            }
            continue
        try:
            raw = provider()
        except Exception:
            out[group] = {
                "available": False,
                "detail": f"{_LABELS[group]} metrics unavailable",
            }
            continue
        out[group] = _sanitize(group, raw)
    return out
