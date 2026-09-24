"""Real screenshot capture: created files, unique names, no overwrites.

Every test injects a fake grabber, so no real display is touched and no
real screen content is captured during testing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

import screen_capture as sc


def _image(size: tuple[int, int] = (64, 36)) -> Image.Image:
    return Image.new("RGB", size, (10, 20, 30))


def _displays() -> list[dict]:
    return [{"index": 0, "primary": True, "bounds": [0, 0, 64, 36]}]


def _grabber():
    return (_image(), _displays())


def test_screenshot_created_and_verified(tmp_path: Path) -> None:
    result = sc.capture_screenshot(directory=tmp_path, grabber=_grabber)
    assert result["ok"] is True
    path = Path(result["path"])
    assert path.exists()
    size = path.stat().st_size
    assert size > 0
    assert result["size_bytes"] == size
    assert result["displays"] == _displays()
    with Image.open(path) as img:
        assert img.size == (64, 36)


def test_unique_filename_prevents_overwrite(tmp_path: Path) -> None:
    first = sc.capture_screenshot(directory=tmp_path, grabber=_grabber)
    first_path = Path(first["path"])
    original_bytes = first_path.read_bytes()
    second = sc.capture_screenshot(directory=tmp_path, grabber=_grabber)
    second_path = Path(second["path"])
    assert second_path != first_path
    assert first_path.read_bytes() == original_bytes  # untouched


def test_unique_path_bumps_when_target_exists(tmp_path: Path) -> None:
    target = tmp_path / "shot.png"
    target.write_bytes(b"existing")
    candidate = sc.unique_path(tmp_path, "shot", ".png")
    assert candidate != target
    assert not candidate.exists()
    assert target.read_bytes() == b"existing"


def test_verify_missing_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(sc.ScreenshotError) as excinfo:
        sc.verify_screenshot(tmp_path / "missing.png")
    assert (
        "missing" in str(excinfo.value).lower() or "not" in str(excinfo.value).lower()
    )


def test_verify_zero_byte_file_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(sc.ScreenshotError):
        sc.verify_screenshot(empty)


def test_verify_good_file_returns_size(tmp_path: Path) -> None:
    good = tmp_path / "good.png"
    good.write_bytes(b"12345")
    info = sc.verify_screenshot(good)
    assert info["size_bytes"] == 5


def test_failure_reports_actual_reason(tmp_path: Path) -> None:
    def boom():
        raise RuntimeError("display is locked")

    with pytest.raises(sc.ScreenshotError) as excinfo:
        sc.capture_screenshot(directory=tmp_path, grabber=boom)
    assert "display is locked" in str(excinfo.value)
    assert list(tmp_path.iterdir()) == []  # no phantom file, no false success


def test_failure_when_directory_is_not_a_directory(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    with pytest.raises(sc.ScreenshotError):
        sc.capture_screenshot(directory=blocker, grabber=_grabber)


def test_default_directories_are_controlled(tmp_path: Path) -> None:
    assert sc.screenshots_dir().name.lower() == "srilatha"
    import screen_recording as sr

    assert sr.recordings_dir().name.lower() == "srilatha"
