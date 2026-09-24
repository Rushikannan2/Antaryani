"""System metrics: CPU, RAM, storage, battery, network, GPU, system, time.

The dashboard and the voice tool report only real, sanitized values. A
malformed or unavailable source yields null/unavailable fields - never
invented numbers, never an exception.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import system_metrics as sm

FULL_FAKE = {
    "cpu": {"percent": 42.0, "cores": 8, "threads": 16, "frequency_ghz": 3.4},
    "ram": {
        "total_gb": 32.0,
        "used_gb": 12.5,
        "available_gb": 19.5,
        "percent": 39.0,
    },
    "storage": {
        "drive": "C:",
        "total_gb": 512.0,
        "used_gb": 200.0,
        "free_gb": 312.0,
        "percent": 39.1,
    },
    "battery": {
        "available": True,
        "percent": 87.0,
        "charging": False,
        "ac_connected": True,
        "status": "full",
    },
    "network": {
        "connected": True,
        "interfaces": ["Wi-Fi"],
        "upload_mbps": 1.2,
        "download_mbps": 10.5,
    },
    "gpu": {
        "available": True,
        "name": "FakeGPU",
        "percent": 13.0,
        "vram_used_gb": 2.0,
        "vram_total_gb": 8.0,
        "temperature_c": 55.0,
    },
    "system": {"os": "Windows 11", "hostname": "RUSHI-PC", "uptime_hours": 5.5},
    "time": {"date": "2026-09-24", "time": "10:42:13", "timezone": "Asia/Kolkata"},
}


def _providers(**overrides: object) -> dict:
    base: dict = {name: (lambda: {}) for name in FULL_FAKE}
    base.update(overrides)
    return base


def test_collect_returns_all_groups_with_fake_providers() -> None:
    providers = {
        name: (lambda values=values: dict(values)) for name, values in FULL_FAKE.items()
    }
    data = sm.collect_system_metrics(providers=providers)
    assert set(data) == set(FULL_FAKE)
    assert data["cpu"]["percent"] == 42.0
    assert data["ram"]["total_gb"] == 32.0
    assert data["storage"]["free_gb"] == 312.0
    assert data["battery"]["percent"] == 87.0
    assert data["network"]["connected"] is True
    assert data["gpu"]["name"] == "FakeGPU"
    assert data["system"]["hostname"] == "RUSHI-PC"
    assert data["time"]["date"] == "2026-09-24"


def test_collect_default_returns_real_machine_shape() -> None:
    data = sm.collect_system_metrics(cpu_interval=None)
    for group in FULL_FAKE:
        assert group in data
        assert isinstance(data[group], dict)
        assert "available" in data[group]
    # Real CPU/RAM on any running machine must be present and sane.
    if data["cpu"]["available"]:
        assert data["cpu"]["percent"] is None or 0.0 <= data["cpu"]["percent"] <= 100.0
    if data["ram"]["available"]:
        assert data["ram"]["total_gb"] is None or data["ram"]["total_gb"] > 0


def test_malformed_percent_becomes_none() -> None:
    for bad in (-5, 150, "abc", math.nan, math.inf, True, {"x": 1}, None):
        data = sm.collect_system_metrics(
            providers=_providers(cpu=lambda bad=bad: {"percent": bad})
        )
        assert data["cpu"]["percent"] is None, f"percent {bad!r} must sanitize to None"


def test_malformed_numbers_become_none() -> None:
    data = sm.collect_system_metrics(
        providers=_providers(
            ram=lambda: {"total_gb": -1, "used_gb": "12", "percent": 101.0}
        )
    )
    assert data["ram"]["total_gb"] is None
    assert data["ram"]["used_gb"] is None
    assert data["ram"]["percent"] is None


def test_provider_exception_yields_unavailable_group() -> None:
    def explode() -> dict:
        raise RuntimeError("psutil exploded")

    data = sm.collect_system_metrics(providers=_providers(cpu=explode))
    assert data["cpu"]["available"] is False
    assert data["cpu"]["detail"] == "CPU metrics unavailable"
    # other groups still collect normally
    assert data["ram"]["available"] is True


def test_gpu_unavailable_fallback() -> None:
    data = sm.gpu_metrics(None)
    assert data == {"available": False, "detail": "GPU metrics unavailable"}


def test_gpu_available_with_fake_backend() -> None:
    class FakeGpu:
        def name(self) -> bytes:
            return b"NVIDIA GeForce RTX 4060"

        def utilization(self) -> int:
            return 37

        def memory(self) -> tuple[int, int]:
            return (2 * 1024**3, 8 * 1024**3)

        def temperature(self) -> int:
            return 61

    data = sm.gpu_metrics(FakeGpu())
    assert data["available"] is True
    assert data["name"] == "NVIDIA GeForce RTX 4060"
    assert data["percent"] == 37.0
    assert data["vram_used_gb"] == 2.0
    assert data["vram_total_gb"] == 8.0
    assert data["temperature_c"] == 61.0


def test_gpu_malformed_fields_become_none_not_invented() -> None:
    class WeirdGpu:
        def name(self) -> str:
            return "GPU"

        def utilization(self) -> int:
            return 150  # out of range

        def memory(self) -> tuple[int, int]:
            return (-1, 0)  # nonsense

        def temperature(self) -> str:
            return "hot"  # wrong type

    data = sm.gpu_metrics(WeirdGpu())
    assert data["available"] is True
    assert data["percent"] is None
    assert data["vram_used_gb"] is None
    assert data["temperature_c"] is None


def test_battery_absent_reports_unavailable() -> None:
    data = sm.battery_metrics(None)
    assert data["available"] is False
    assert "battery" in data["detail"].lower()


def test_battery_real_values() -> None:
    fake = SimpleNamespace(percent=80.0, power_plugged=False, secsleft=7200)
    data = sm.battery_metrics(fake)
    assert data["available"] is True
    assert data["percent"] == 80.0
    assert data["ac_connected"] is False
    assert data["status"] == "discharging"


def test_battery_charging() -> None:
    fake = SimpleNamespace(percent=40.0, power_plugged=True, secsleft=1800)
    data = sm.battery_metrics(fake)
    assert data["ac_connected"] is True
    assert data["status"] == "charging"


def test_storage_zero_total_never_divides() -> None:
    usage = SimpleNamespace(total=0, used=0, free=0)
    data = sm.storage_metrics(usage=usage, drive="C:")
    assert data["percent"] is None
    assert data["total_gb"] == 0.0 or data["total_gb"] is None


def test_network_first_sample_has_no_throughput() -> None:
    io = SimpleNamespace(bytes_sent=1000, bytes_recv=2000)
    stats = {"Wi-Fi": SimpleNamespace(is_up=True)}
    data = sm.network_metrics(stats=stats, io=io, now=100.0, reset=True)
    assert data["connected"] is True
    assert data["interfaces"] == ["Wi-Fi"]
    assert data["upload_mbps"] is None
    assert data["download_mbps"] is None


def test_network_second_sample_computes_throughput() -> None:
    stats = {"Wi-Fi": SimpleNamespace(is_up=True)}
    sm.network_metrics(
        stats=stats,
        io=SimpleNamespace(bytes_sent=0, bytes_recv=0),
        now=200.0,
        reset=True,
    )
    data = sm.network_metrics(
        stats=stats,
        io=SimpleNamespace(bytes_sent=1_000_000, bytes_recv=4_000_000),
        now=202.0,
    )
    assert data["upload_mbps"] == 4.0  # 1 MB over 2 s = 4 Mbps
    assert data["download_mbps"] == 16.0


def test_network_disconnected_when_no_interface_is_up() -> None:
    stats = {"Wi-Fi": SimpleNamespace(is_up=False)}
    data = sm.network_metrics(
        stats=stats,
        io=SimpleNamespace(bytes_sent=0, bytes_recv=0),
        now=300.0,
        reset=True,
    )
    assert data["connected"] is False
