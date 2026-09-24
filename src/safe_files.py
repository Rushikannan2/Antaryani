"""Collision-free file naming shared by screenshots and recordings.

Srilatha must never silently overwrite Rushi Sir's files: every saved
artifact gets a unique timestamped name.
"""

from __future__ import annotations

import threading
from pathlib import Path

_lock = threading.RLock()


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    """Return a non-existent ``<stem><suffix>`` path inside *directory*.

    If the target already exists, bump an incrementing suffix
    (``stem_1``, ``stem_2``, ...) until a free name is found. The
    existing file is never touched.
    """
    directory = Path(directory)
    clean_stem = (
        "".join(
            character for character in str(stem) if character not in '<>:"/\\|?*'
        ).strip()
        or "capture"
    )
    clean_suffix = str(suffix)
    if Path(clean_suffix).name != clean_suffix:
        clean_suffix = ".bin"
    with _lock:
        candidate = directory / f"{clean_stem}{clean_suffix}"
        counter = 1
        while candidate.exists():
            candidate = directory / f"{clean_stem}_{counter}{clean_suffix}"
            counter += 1
            if counter > 10_000:
                raise FileExistsError(
                    f"Could not find a free filename for {clean_stem}{clean_suffix} in {directory}"
                )
        return candidate
