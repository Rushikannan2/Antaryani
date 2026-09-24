"""Real screenshot capture via Pillow ImageGrab (no shell, no ffmpeg).

Screenshots land in ``~/Pictures/Srilatha`` with timestamped, never
reused names. Every file is verified after writing: a missing or
zero-byte file is an error, never a silent success.
"""

from __future__ import annotations

import contextlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageGrab

from safe_files import unique_path

__all__ = [
    "ScreenshotError",
    "capture_screenshot",
    "grab_desktop",
    "screenshots_dir",
    "unique_path",
    "verify_screenshot",
]


class ScreenshotError(Exception):
    """Raised when a screenshot cannot be captured, saved, or verified."""


def screenshots_dir() -> Path:
    override = os.environ.get("SRILATHA_SCREENSHOTS_DIR")
    return (
        Path(override).expanduser()
        if override
        else Path.home() / "Pictures" / "Srilatha"
    )


def verify_screenshot(path: Path | str) -> dict:
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise ScreenshotError(f"Screenshot file is missing: {file_path}")
    try:
        size = file_path.stat().st_size
    except OSError as exc:
        raise ScreenshotError(f"Could not verify screenshot file: {file_path}") from exc
    if size <= 0:
        raise ScreenshotError(f"Screenshot file is empty (zero bytes): {file_path}")
    return {"path": str(file_path), "size_bytes": size, "verified": True}


def _enumerate_displays(image: Image.Image) -> list[dict]:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        monitors: list[dict] = []
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.RECT),
            ctypes.c_void_p,
        )

        def _callback(handle: Any, _hdc: Any, _rect: Any, _lparam: Any) -> bool:
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(MonitorInfo)
            if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                rect = info.rcMonitor
                monitors.append(
                    {
                        "index": len(monitors),
                        "primary": bool(info.dwFlags & 1),
                        "bounds": [
                            int(rect.left),
                            int(rect.top),
                            int(rect.right),
                            int(rect.bottom),
                        ],
                    }
                )
            return True

        user32.EnumDisplayMonitors(None, None, callback_type(_callback), 0)
        if monitors:
            return monitors
    except Exception:
        pass
    return [
        {
            "index": 0,
            "primary": True,
            "bounds": [0, 0, image.width, image.height],
        }
    ]


def grab_desktop() -> tuple[Image.Image, list[dict]]:
    """Capture the full virtual desktop and enumerate the displays."""
    image = ImageGrab.grab(all_screens=True)
    if not isinstance(image, Image.Image):
        raise RuntimeError("the Windows screen API returned an invalid image")
    return image, _enumerate_displays(image)


def capture_screenshot(
    directory: Path | str | None = None,
    grabber: Any = None,
    note: str = "",
) -> dict:
    """Capture, save, and verify a real screenshot of the desktop."""
    target_dir = Path(directory) if directory is not None else screenshots_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ScreenshotError(f"Cannot create the screenshot directory: {exc}") from exc

    source = grabber if grabber is not None else grab_desktop
    try:
        captured = source()
        if isinstance(captured, Image.Image):
            image = captured
            displays = [
                {
                    "index": 0,
                    "primary": True,
                    "bounds": [0, 0, image.width, image.height],
                }
            ]
        else:
            image, displays = captured
            displays = list(displays) if displays else []
    except Exception as exc:
        raise ScreenshotError(f"Screen capture failed: {exc}") from exc

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = unique_path(target_dir, f"screenshot_{stamp}", ".png")
    created = False
    try:
        with open(path, "xb") as handle:
            created = True
            image.save(handle, format="PNG")
    except Exception as exc:
        if created:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
        raise ScreenshotError(f"Could not save the screenshot: {exc}") from exc

    try:
        info = verify_screenshot(path)
    except ScreenshotError:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        raise
    return {
        "ok": True,
        "path": info["path"],
        "filename": path.name,
        "size_bytes": info["size_bytes"],
        "displays": list(displays) if displays else [],
        "capture": "full_virtual_desktop",
        "note": note,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
