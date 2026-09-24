"""Windows filesystem engine (Part 2).

Like ``browser.py``, this module is LiveKit-free: it performs the real
filesystem work and enforces Part 1 security policy (``windows_security``)
on EVERY public operation - validate first, execute second, verify last.

Layering contract:

* Callers supply ABSOLUTE paths. ``windows_tools`` resolves aliases
  ("Desktop", "my documents"), relative names, and contextual references
  ("this file", "that folder") before calling in.
* Policy violations raise ``SecurityPolicyError`` (from ``windows_security``);
  protected system locations are refused BEFORE any existence check, so a
  forbidden path is never even stat-ed first.
* Operational problems raise ``WindowsFSError`` with a stable ``code``:
  NOT_FOUND, ALREADY_EXISTS, CONFLICT, INVALID_NAME, ACCESS_DENIED,
  NEEDS_RECURSIVE, VERIFY_FAILED, BLOCKED, UNKNOWN_APP, APP_MISSING,
  NO_MATCH, TOO_LARGE, UNSUPPORTED, OS_ERROR - so tools can return
  structured, user-friendly errors.
* Every modification is verified after the fact: a write that silently
  failed raises VERIFY_FAILED instead of reporting success.
* There is no shell access anywhere in this module. Opening files uses
  Windows file associations (``os.startfile``), applications launch only
  through the fixed allowlist in ``launch_application`` (executable names
  resolved inside ``%WINDIR%\\System32`` - the model never supplies a path
  or arguments), deletion goes to the Recycle Bin via ``send2trash``, and
  direct permanent deletion requires an explicit recursive flag.
"""

from __future__ import annotations

import contextlib
import fnmatch
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from send2trash import send2trash

from windows_security import (
    MAX_BULK_ITEMS,
    is_protected_path,
    validate_destination,
    validate_path,
    validate_source,
)

# Listing/searching is for conversation, not data export: cap results so a
# huge folder cannot flood the model's context.
MAX_LIST_ENTRIES = 200
MAX_SEARCH_RESULTS = 50
# Reading: files over MAX_READ_BYTES are refused (TOO_LARGE) and returned
# text is capped at MAX_READ_CHARS. Trees: bounded depth and entry budgets
# keep huge folders - and junction loops - from ever running away.
MAX_READ_BYTES = 2_000_000
MAX_READ_CHARS = 200_000
MAX_TREE_DEPTH = 5
MAX_TREE_ENTRIES = 400

# Opening these would execute programs, so open_path refuses them. Launching
# applications is only possible through launch_application's fixed allowlist.
_BLOCKED_OPEN_EXTENSIONS = frozenset(
    {
        ".exe",
        ".com",
        ".bat",
        ".cmd",
        ".ps1",
        ".psm1",
        ".vbs",
        ".vbe",
        ".js",
        ".jse",
        ".wsf",
        ".wsh",
        ".msi",
        ".msp",
        ".scr",
        ".pif",
        ".reg",
        ".hta",
        ".cpl",
        ".lnk",
    }
)

# Controlled application allowlist: friendly name -> executable file name.
# Executables are always resolved inside %WINDIR%\System32, so arbitrary
# program execution (paths, arguments, downloaded binaries) is impossible.
ALLOWED_APPLICATIONS: dict[str, str] = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "mspaint": "mspaint.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
}

# Allowlisted applications that are NOT in System32: fixed, per-machine
# candidate templates (environment variables only - never model input).
# Part 1 sanctions growing the allowlist this way.
_APPLICATION_FIXED_PATHS: dict[str, tuple[str, ...]] = {
    "vs code": (
        r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
        r"%ProgramFiles%\Microsoft VS Code\Code.exe",
        r"%ProgramFiles(x86)%\Microsoft VS Code\Code.exe",
    ),
    # Microsoft Office (Part 7): fixed Program Files locations only - the
    # model never supplies a path or arguments, only one of these names.
    "word": (
        r"%ProgramFiles%\Microsoft Office\root\Office16\WINWORD.EXE",
        r"%ProgramFiles%\Microsoft Office\Office16\WINWORD.EXE",
        r"%ProgramFiles(x86)%\Microsoft Office\root\Office16\WINWORD.EXE",
        r"%ProgramFiles(x86)%\Microsoft Office\Office16\WINWORD.EXE",
    ),
    "powerpoint": (
        r"%ProgramFiles%\Microsoft Office\root\Office16\POWERPNT.EXE",
        r"%ProgramFiles%\Microsoft Office\Office16\POWERPNT.EXE",
        r"%ProgramFiles(x86)%\Microsoft Office\root\Office16\POWERPNT.EXE",
        r"%ProgramFiles(x86)%\Microsoft Office\Office16\POWERPNT.EXE",
    ),
    "excel": (
        r"%ProgramFiles%\Microsoft Office\root\Office16\EXCEL.EXE",
        r"%ProgramFiles%\Microsoft Office\Office16\EXCEL.EXE",
        r"%ProgramFiles(x86)%\Microsoft Office\root\Office16\EXCEL.EXE",
        r"%ProgramFiles(x86)%\Microsoft Office\Office16\EXCEL.EXE",
    ),
}

_LAUNCH_ALIASES: dict[str, str] = {
    "code": "vs code",
    "vscode": "vs code",
    "visual studio code": "vs code",
    "microsoft word": "word",
    "ms word": "word",
    "winword": "word",
    "microsoft powerpoint": "powerpoint",
    "ms powerpoint": "powerpoint",
    "powerpnt": "powerpoint",
    "microsoft excel": "excel",
    "ms excel": "excel",
}


def _application_candidates(name: str) -> list[Path]:
    """Fixed candidate paths for an allowlisted application (no user input)."""
    key = name.strip().casefold()
    key = _LAUNCH_ALIASES.get(key, key)
    executable = ALLOWED_APPLICATIONS.get(key)
    if executable is not None:
        system_root = Path(os.environ.get("WINDIR", "C:\\Windows"))
        return [system_root / "System32" / executable]
    return [
        Path(os.path.expandvars(template))
        for template in _APPLICATION_FIXED_PATHS.get(key, ())
    ]


class WindowsFSError(Exception):
    """Filesystem failure with a stable ``code`` and a human message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ----------------------------------------------------------------------
# known-folder resolution (usernames are never hard-coded)
# ----------------------------------------------------------------------
# Per-user "User Shell Folders" registry value names. Windows stores OneDrive
# KFM redirection here, which is why the registry is the primary source.
_SHELL_FOLDER_VALUES: dict[str, tuple[str, ...]] = {
    "desktop": ("{Desktop}",),
    "documents": ("Personal",),
    "downloads": (
        "{374DE290-123F-4565-9164-39C4925E467B}",
        "{F42EE2D3-909F-4907-8871-4C22FC0BF756}",
    ),
    "pictures": ("My Pictures",),
    "videos": ("My Video", "My Videos"),
    "music": ("My Music",),
}
_FOLDER_DISPLAY_NAMES: dict[str, str] = {
    "desktop": "Desktop",
    "documents": "Documents",
    "downloads": "Downloads",
    "pictures": "Pictures",
    "videos": "Videos",
    "music": "Music",
}
KNOWN_FOLDER_NAMES = frozenset(
    {
        "desktop",
        "documents",
        "downloads",
        "pictures",
        "videos",
        "music",
        "home",
        "onedrive",
    }
)

_FOLDER_SYNONYMS = {"photos": "pictures", "one drive": "onedrive", "docs": "documents"}
_LEADING_ARTICLES = ("the ", "my ", "our ")
# Short-term context references. Bare pronouns resolve to the most recent
# item (file or folder); the rest are type-specific.
_CONTEXT_ITEM_PHRASES = frozenset({"this", "that", "it", "this one", "that one"})
_CONTEXT_FILE_PHRASES = frozenset(
    {
        "this file",
        "that file",
        "previous file",
        "the previous file",
        "last file",
        "the last file",
        "file we created",
        "the file we created",
    }
)
_CONTEXT_FOLDER_PHRASES = frozenset(
    {
        "this folder",
        "that folder",
        "this directory",
        "that directory",
        "previous folder",
        "the previous folder",
        "last folder",
        "the last folder",
        "folder we created",
        "the folder we created",
    }
)


def _read_shell_folder(value_names: tuple[str, ...]) -> Path | None:
    """Read a per-user shell folder value from the registry (no shell)."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows platforms
        return None
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            for value_name in value_names:
                try:
                    raw, _kind = winreg.QueryValueEx(key, value_name)
                except FileNotFoundError:
                    continue
                expanded = os.path.expandvars(str(raw))
                if not expanded or "%" in expanded:
                    continue  # unresolved variable - fall through to backup
                candidate = Path(expanded)
                if candidate.is_absolute():
                    return candidate.resolve()
    except OSError:
        return None
    return None


def resolve_known_folder(name: str) -> Path:
    """Resolve a standard user folder from the profile itself.

    Reads the per-user ``User Shell Folders`` registry (where Windows records
    OneDrive redirection) and falls back to environment variables and the
    user profile - usernames and paths are never hard-coded.
    """
    if not isinstance(name, str) or not name.strip():
        raise WindowsFSError("INVALID_NAME", "Say which standard folder you mean.")
    key = name.strip().casefold()
    if key not in KNOWN_FOLDER_NAMES:
        raise WindowsFSError("INVALID_NAME", f"Unknown standard location: {name!r}.")
    if key == "home":
        return Path.home().resolve()
    if key == "onedrive":
        for variable in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            value = os.environ.get(variable)
            if value:
                return Path(value).resolve()
        return (Path.home() / "OneDrive").resolve()
    shell_path = _read_shell_folder(_SHELL_FOLDER_VALUES[key])
    if shell_path is not None:
        return shell_path
    return (Path.home() / _FOLDER_DISPLAY_NAMES[key]).resolve()


def _match_folder_alias(lowered: str) -> str | None:
    """Map a natural location phrase to a known-folder name, if any."""
    text = lowered.strip()
    for article in _LEADING_ARTICLES:
        if text.startswith(article):
            text = text[len(article) :]
            break
    for suffix in (" folder", " directory"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    text = _FOLDER_SYNONYMS.get(text, text)
    return text if text in KNOWN_FOLDER_NAMES else None


def resolve_user_path(
    raw: str,
    *,
    context_dir: str | None = None,
    context_file: str | None = None,
    context_folder: str | None = None,
    context_item: str | None = None,
) -> str:
    """Turn a natural reference into an absolute candidate path.

    Accepts standard-location names ("Desktop", "my documents", "photos"),
    contextual references ("this file", "that folder", bare "it",
    "previous file", "the folder we created"), quoted or plain absolute
    paths, and relative names - one starting with a standard folder name
    (``Downloads\\Rushi``) always means that folder; any other resolves
    against the last-used directory, falling back to the user's home.
    The result is only an absolute CANDIDATE: the security policy still
    validates it afterwards.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise WindowsFSError("INVALID_NAME", "Say a location or file name.")
    value = raw.strip().strip('"').strip("'").strip()
    if not value:
        raise WindowsFSError("INVALID_NAME", "Say a location or file name.")

    lowered = value.casefold()
    if lowered in _CONTEXT_ITEM_PHRASES:
        for recent in (context_item, context_file, context_folder):
            if recent:
                return recent
        raise WindowsFSError(
            "INVALID_NAME",
            "There is no current item yet; say which file or folder you mean.",
        )
    if lowered in _CONTEXT_FILE_PHRASES:
        if context_file:
            return context_file
        raise WindowsFSError(
            "INVALID_NAME",
            "There is no current file yet; give a full path or file name.",
        )
    if lowered in _CONTEXT_FOLDER_PHRASES:
        if context_folder:
            return context_folder
        raise WindowsFSError(
            "INVALID_NAME",
            "There is no current folder yet; give a full path or folder name.",
        )

    alias = _match_folder_alias(lowered)
    if alias is not None:
        return str(resolve_known_folder(alias))

    candidate = Path(value)
    if candidate.is_absolute():
        return value
    # "Downloads\Rushi": the first segment names a standard folder, so it
    # means THAT real folder - joining it onto the last-used directory used
    # to double the segment (...\Downloads\Rushi\Downloads\Rushi) and made
    # every later reference to the item fail as NOT_FOUND.
    segments = [part for part in value.replace("/", "\\").split("\\") if part]
    if segments:
        prefix = _match_folder_alias(segments[0].casefold())
        if prefix is not None:
            base = resolve_known_folder(prefix)
            if len(segments) > 1:
                return str(base.joinpath(*segments[1:]))
            return str(base)
    base = Path(context_dir) if context_dir else Path.home()
    return str(base / value)


# ----------------------------------------------------------------------
# shared validation helpers
# ----------------------------------------------------------------------
def require_source(path: str | Path, *, operation: str) -> Path:
    """Validate an operation's source: policy before existence.

    Protected locations and unknown operations are judged WITHOUT stat-ing
    first (Part 1 ordering); a missing non-protected path becomes ``NOT_FOUND``
    so tools report a stable operational code instead of a policy error.
    """
    resolved = validate_path(path)
    if is_protected_path(resolved):
        return validate_source(resolved, operation=operation)
    if not resolved.exists():
        raise WindowsFSError("NOT_FOUND", f"No such file or folder: {resolved}")
    return validate_source(resolved, operation=operation)


def _require_directory(path: str | Path) -> Path:
    """Validate a folder argument for read operations."""
    resolved = validate_path(path)
    if not resolved.exists():
        raise WindowsFSError("NOT_FOUND", f"No such folder: {resolved}")
    if not resolved.is_dir():
        raise WindowsFSError("NOT_FOUND", f"{resolved} is not a folder.")
    return resolved


# Thin wrappers over the mutating primitives so tests can prove the
# post-operation verification actually catches silent failures.
def _write_text(path: Path, data: str) -> None:
    # newline="" writes byte-for-byte: no CRLF translation, so editing a
    # CRLF file changes only the edited region.
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(data)


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _os_rename(src: Path, dst: Path) -> None:
    os.rename(src, dst)


def _unlink(path: Path) -> None:
    path.unlink()


def _rmtree(path: Path) -> None:
    shutil.rmtree(path)


def _shutil_move(src: Path, dst: Path) -> None:
    shutil.move(str(src), str(dst))


def _shutil_copy2(src: Path, dst: Path) -> None:
    shutil.copy2(str(src), str(dst))


def _shutil_copytree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst)


def _iter_directory(path: Path) -> list[Path]:
    return list(path.iterdir())


def _is_reparse_point(path: Path) -> bool:
    """True for junctions/symlinks: they are listed but never followed."""
    try:
        attributes = path.lstat().st_file_attributes
    except OSError:
        return False
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _walk_entries(root: Path):
    """Yield files AND folders under ``root`` without following junctions.

    Reparse points are yielded as entries so their names can match a
    search, but they are pruned from ``dirs`` in place so a junction loop
    can never hang the walk.
    """
    for folder, dirs, files in os.walk(root, onerror=lambda _err: None):
        base = Path(folder)
        all_dirs = list(dirs)
        dirs[:] = [name for name in all_dirs if not _is_reparse_point(base / name)]
        for name in all_dirs:
            yield base / name
        for name in files:
            yield base / name


def _recycle(path: Path) -> None:
    send2trash(str(path))


def _startfile(path: Path) -> None:
    """Hand a path to its registered Windows file-association handler."""
    os.startfile(str(path))


def count_items(path: str | Path, *, cap: int = MAX_BULK_ITEMS + 1) -> int:
    """Count the items an operation would touch, stopping at ``cap``.

    Files count as 1; folders count every descendant. Used for bulk risk
    escalation: the walk ignores unreadable subfolders instead of failing.
    """
    candidate = Path(path)
    if candidate.is_file():
        return 1
    total = 0
    for _folder, dirs, files in os.walk(candidate, onerror=lambda _err: None):
        total += len(dirs) + len(files)
        if total >= cap:
            return cap
    return total


# ----------------------------------------------------------------------
# listing and searching
# ----------------------------------------------------------------------
def list_directory(path: str | Path) -> dict[str, object]:
    """List a folder's entries (name, type, size, modified), capped."""
    resolved = _require_directory(path)
    try:
        raw_entries = _iter_directory(resolved)
    except PermissionError as exc:
        raise WindowsFSError("ACCESS_DENIED", f"Access denied: {resolved}") from exc
    except OSError as exc:
        raise WindowsFSError("OS_ERROR", f"Could not list {resolved}: {exc}") from exc

    # Folders first, then files (each alphabetical): navigation targets
    # must never be silently pushed out by the entry cap - a folder sorted
    # past 200 files was invisible, and the agent then reported the folder
    # as missing.
    folders: list[Path] = []
    plain_files: list[Path] = []
    for entry in raw_entries:
        (folders if entry.is_dir() else plain_files).append(entry)
    folders.sort(key=lambda item: item.name.casefold())
    plain_files.sort(key=lambda item: item.name.casefold())

    entries: list[dict[str, object]] = []
    for entry in [*folders, *plain_files]:
        if len(entries) >= MAX_LIST_ENTRIES:
            break
        try:
            stat = entry.stat()
            is_dir = entry.is_dir()
        except OSError:
            continue  # vanished between listing and stat-ing
        entries.append(
            {
                "name": entry.name,
                "type": "folder" if is_dir else "file",
                "size_bytes": None if is_dir else stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(
                    timespec="seconds"
                ),
            }
        )
    return {
        "path": str(resolved),
        "entries": entries,
        "count": len(entries),
        "truncated": len(raw_entries) > len(entries),
    }


def search_files(
    root: str | Path,
    pattern: str,
    *,
    recursive: bool = True,
    max_results: int = MAX_SEARCH_RESULTS,
) -> dict[str, object]:
    """Find files or folders by name: plain text anywhere, ``*.ext`` wildcards."""
    resolved_root = _require_directory(root)
    if not isinstance(pattern, str) or not pattern.strip():
        raise WindowsFSError("INVALID_NAME", "Say what file name to look for.")
    query = pattern.strip()
    if "\\" in query or "/" in query:
        raise WindowsFSError(
            "INVALID_NAME", "The search text must be a name, not a path."
        )
    wildcard = any(ch in query for ch in "*?[")
    needle = query.casefold()

    def matches(name: str) -> bool:
        lowered = name.casefold()
        if wildcard:
            return fnmatch.fnmatchcase(lowered, needle)
        return needle in lowered

    if recursive:
        candidates = _walk_entries(resolved_root)
    else:
        candidates = _iter_directory(resolved_root)

    results: list[str] = []
    exact: list[str] = []
    truncated = False
    try:
        for file_path in candidates:
            if not matches(file_path.name):
                continue
            if not wildcard and file_path.name.casefold() == needle:
                # An item literally named like the query is what was asked
                # for: keep it even when substring matches would fill the
                # cap before the walk ever reaches it.
                if len(exact) < max_results:
                    exact.append(str(file_path))
                else:
                    truncated = True
                continue
            if len(results) >= max_results:
                truncated = True
                continue  # keep walking: an exact match may still appear
            results.append(str(file_path))
    except PermissionError as exc:
        raise WindowsFSError(
            "ACCESS_DENIED", f"Access denied while searching {resolved_root}."
        ) from exc
    exact.sort(key=str.casefold)
    results.sort(key=str.casefold)
    combined = [*exact, *results]
    if len(combined) > max_results:
        truncated = True
        combined = combined[:max_results]
    results = combined
    return {
        "root": str(resolved_root),
        "pattern": query,
        "results": results,
        "count": len(results),
        "truncated": truncated,
    }


def file_exists(path: str | Path) -> dict[str, object]:
    """Check whether a path is an existing file."""
    resolved = validate_path(path)
    return {"path": str(resolved), "exists": resolved.is_file()}


def folder_exists(path: str | Path) -> dict[str, object]:
    """Check whether a path is an existing folder."""
    resolved = validate_path(path)
    return {"path": str(resolved), "exists": resolved.is_dir()}


def get_file_info(path: str | Path) -> dict[str, object]:
    """Describe one file or folder: size, dates, extension, flags."""
    resolved = validate_path(path)
    if not resolved.exists():
        raise WindowsFSError("NOT_FOUND", f"No such file or folder: {resolved}")
    try:
        stat = resolved.stat()
        is_dir = resolved.is_dir()
    except PermissionError as exc:
        raise WindowsFSError("ACCESS_DENIED", f"Access denied: {resolved}") from exc
    except OSError as exc:
        raise WindowsFSError(
            "OS_ERROR", f"Could not inspect {resolved}: {exc}"
        ) from exc
    attributes = getattr(stat, "st_file_attributes", 0)
    return {
        "name": resolved.name,
        "path": str(resolved),
        "type": "folder" if is_dir else "file",
        "size_bytes": stat.st_size,
        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(timespec="seconds"),
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "extension": resolved.suffix.casefold(),
        "read_only": bool(attributes & 0x1),
        "hidden": bool(attributes & 0x2) or resolved.name.startswith("."),
    }


# ----------------------------------------------------------------------
# reading files, documents, and folder trees (Part 7)
# ----------------------------------------------------------------------
def _read_bytes(path: Path) -> bytes:
    """Read a file's raw bytes, mapping failures to stable codes."""
    try:
        return path.read_bytes()
    except PermissionError as exc:
        raise WindowsFSError("ACCESS_DENIED", f"Access denied: {path}") from exc
    except OSError as exc:
        raise WindowsFSError("OS_ERROR", f"Could not read {path}: {exc}") from exc


def _decode_text(raw: bytes, path: Path) -> str:
    """Decode UTF-8 text; refuse binary content instead of dumping it."""
    if b"\x00" in raw[:8192]:
        raise WindowsFSError("UNSUPPORTED", f"{path} is a binary file, not text.")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise WindowsFSError("UNSUPPORTED", f"{path} is not UTF-8 text.") from exc


def _extract_docx(path: Path) -> str:
    """Paragraph text of a .docx, in document order."""
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise WindowsFSError(
            "UNSUPPORTED", f"{path} is not a readable Word document."
        ) from exc
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise WindowsFSError(
            "UNSUPPORTED", f"{path} is not a readable Word document."
        ) from exc
    paragraphs: list[str] = []
    for element in root.iter():
        if element.tag.endswith("}p"):
            texts = [
                child.text or "" for child in element.iter() if child.tag.endswith("}t")
            ]
            paragraphs.append("".join(texts))
    return "\n".join(paragraphs)


def _extract_pptx(path: Path) -> str:
    """Per-slide text of a .pptx, slides in numeric order."""
    try:
        with zipfile.ZipFile(path) as archive:
            slide_names = [
                name
                for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            ]
            slide_names.sort(
                key=lambda name: int(
                    "".join(ch for ch in Path(name).stem if ch.isdigit()) or 0
                )
            )
            parts: list[str] = []
            for position, name in enumerate(slide_names, start=1):
                try:
                    root = ElementTree.fromstring(archive.read(name))
                except ElementTree.ParseError as exc:
                    raise WindowsFSError(
                        "UNSUPPORTED", f"{path} is not a readable slideshow."
                    ) from exc
                texts = [
                    element.text or ""
                    for element in root.iter()
                    if element.tag.endswith("}t")
                ]
                parts.append(f"--- slide {position} ---\n" + "\n".join(texts))
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise WindowsFSError(
            "UNSUPPORTED", f"{path} is not a readable slideshow."
        ) from exc
    return "\n\n".join(parts)


def _extract_pdf(path: Path) -> str:
    """Text of a PDF, or an honest note when there is no text layer."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise WindowsFSError(
            "UNSUPPORTED", "PDF reading needs the pypdf package."
        ) from exc
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise WindowsFSError(
                "UNSUPPORTED", f"{path} is password-protected; not readable."
            )
        text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except WindowsFSError:
        raise
    except Exception as exc:
        raise WindowsFSError(
            "UNSUPPORTED", f"Could not extract the text of {path}."
        ) from exc
    return text or "(this PDF has no extractable text)"


# Documents are read by extraction and NEVER edited as text here;
# spreadsheets and legacy binaries are refused outright.
_UNREADABLE_EXTENSIONS = frozenset({".doc", ".ppt", ".xlsx", ".xls"})
_DOCUMENT_EXTENSIONS = frozenset(
    {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".pdf"}
)


def read_file(path: str | Path) -> dict[str, object]:
    """Read a text file's contents, extracting text from documents too."""
    resolved = require_source(path, operation="read_file")
    if not resolved.is_file():
        raise WindowsFSError("NOT_FOUND", f"Not a file: {resolved}")
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise WindowsFSError(
            "OS_ERROR", f"Could not inspect {resolved}: {exc}"
        ) from exc
    if size > MAX_READ_BYTES:
        raise WindowsFSError(
            "TOO_LARGE",
            f"{resolved} is {size} bytes; refusing to read files over "
            f"{MAX_READ_BYTES} bytes.",
        )
    extension = resolved.suffix.casefold()
    kind = "text"
    if extension == ".docx":
        kind = "docx"
        text = _extract_docx(resolved)
    elif extension == ".pptx":
        kind = "pptx"
        text = _extract_pptx(resolved)
    elif extension == ".pdf":
        kind = "pdf"
        text = _extract_pdf(resolved)
    elif extension in _UNREADABLE_EXTENSIONS:
        raise WindowsFSError(
            "UNSUPPORTED",
            f"Cannot read {extension} files as text; open it with open_path "
            "or launch the matching application instead.",
        )
    else:
        text = _decode_text(_read_bytes(resolved), resolved)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    truncated = False
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS]
        truncated = True
    return {
        "path": str(resolved),
        "name": resolved.name,
        "extension": extension,
        "kind": kind,
        "size_bytes": size,
        "content": text,
        "lines": len(text.splitlines()),
        "truncated": truncated,
    }


def _is_important_file(name: str) -> bool:
    """Well-known project files worth surfacing in a tree overview."""
    lowered = name.casefold()
    return lowered.startswith(("readme", "license")) or lowered in {
        "pyproject.toml",
        "package.json",
        "requirements.txt",
        "setup.py",
        "makefile",
        "dockerfile",
    }


def inspect_tree(
    path: str | Path,
    *,
    max_depth: int | None = None,
    max_entries: int | None = None,
) -> dict[str, object]:
    """Draw a folder as a bounded tree: sizes, counts, and key files.

    Depth and entry limits (``MAX_TREE_DEPTH`` / ``MAX_TREE_ENTRIES``)
    keep huge folders safe; junctions are listed but never followed, so a
    junction loop can never run away.
    """
    root = _require_directory(path)
    depth_limit = MAX_TREE_DEPTH if max_depth is None else max_depth
    entry_limit = MAX_TREE_ENTRIES if max_entries is None else max_entries
    lines: list[str] = [root.name or str(root)]
    files = 0
    folders = 0
    skipped = 0
    entries_seen = 0
    total_size = 0
    truncated = False
    extensions: dict[str, int] = {}
    important: list[str] = []

    def walk(current: Path, prefix: str, depth: int) -> None:
        nonlocal files, folders, skipped, entries_seen, total_size, truncated
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            skipped += 1  # unreadable folder: counted, never fatal
            return
        for index, child in enumerate(children):
            if entries_seen >= entry_limit:
                truncated = True
                return
            entries_seen += 1
            last = index == len(children) - 1
            branch = "\u2514\u2500\u2500 " if last else "\u251c\u2500\u2500 "
            try:
                reparse = _is_reparse_point(child)
                is_dir = False if reparse else child.is_dir()
            except OSError:
                skipped += 1
                lines.append(prefix + branch + child.name)
                continue
            lines.append(prefix + branch + child.name)
            if reparse:
                skipped += 1  # junction/symlink: listed, never followed
                continue
            if is_dir:
                folders += 1
                if depth >= depth_limit:
                    try:
                        if next(child.iterdir(), None) is not None:
                            truncated = True
                    except OSError:
                        pass
                    continue
                walk(
                    child,
                    prefix + ("    " if last else "\u2502   "),
                    depth + 1,
                )
            else:
                files += 1
                with contextlib.suppress(OSError):
                    total_size += child.stat().st_size
                suffix = child.suffix.casefold()
                if suffix:
                    extensions[suffix] = extensions.get(suffix, 0) + 1
                if len(important) < 10 and _is_important_file(child.name):
                    important.append(child.name)

    walk(root, "", 0)
    return {
        "path": str(root),
        "tree": "\n".join(lines),
        "files": files,
        "folders": folders,
        "total_size_bytes": total_size,
        "extensions": dict(sorted(extensions.items())),
        "important_files": important,
        "truncated": truncated,
        "skipped": skipped,
    }


# ----------------------------------------------------------------------
# editing text files (Part 7)
# ----------------------------------------------------------------------
def plan_edit(
    path: str | Path,
    *,
    mode: str = "replace",
    find: str = "",
    replace: str = "",
    append: str = "",
) -> dict[str, object]:
    """Preflight an edit: policy, mode, decodability, size, matches.

    Runs BEFORE any confirmation is staged so the user is never asked to
    approve an edit that could not succeed. Nothing is ever written here.
    """
    src = require_source(path, operation="edit_file")
    if not src.is_file():
        raise WindowsFSError("NOT_FOUND", f"Not a file: {src}")
    if not isinstance(mode, str) or mode.strip().casefold() not in (
        "replace",
        "append",
    ):
        raise WindowsFSError("INVALID_NAME", "Edit mode must be 'replace' or 'append'.")
    normalized = mode.strip().casefold()
    if src.suffix.casefold() in _DOCUMENT_EXTENSIONS:
        raise WindowsFSError(
            "UNSUPPORTED",
            f"{src.suffix} documents are never edited as text here; open the "
            "application instead.",
        )
    try:
        size = src.stat().st_size
    except OSError as exc:
        raise WindowsFSError("OS_ERROR", f"Could not inspect {src}: {exc}") from exc
    if size > MAX_READ_BYTES:
        raise WindowsFSError(
            "TOO_LARGE",
            f"{src} is {size} bytes; refusing to edit files over "
            f"{MAX_READ_BYTES} bytes.",
        )
    text = _decode_text(_read_bytes(src), src)
    matches = 0
    if normalized == "replace":
        if not isinstance(find, str) or not find:
            raise WindowsFSError("INVALID_NAME", "Say the exact text to replace.")
        if not isinstance(replace, str):
            raise WindowsFSError("INVALID_NAME", "The replacement must be text.")
        matches = text.count(find)
        if matches == 0:
            raise WindowsFSError(
                "NO_MATCH",
                f"{find!r} was not found in {src}; nothing was changed.",
            )
    elif not isinstance(append, str) or not append:
        raise WindowsFSError("INVALID_NAME", "Say the text to append.")
    return {"path": str(src), "mode": normalized, "matches": matches}


def edit_file(
    path: str | Path,
    *,
    mode: str = "replace",
    find: str = "",
    replace: str = "",
    append: str = "",
) -> dict[str, object]:
    """Replace or append text in an existing text file, then verify it.

    ``plan_edit`` validates everything first; this performs the actual
    read-before-write, writes byte-for-byte (no newline translation), and
    re-reads the file so a silently failed write raises VERIFY_FAILED
    instead of reporting success.
    """
    plan = plan_edit(path, mode=mode, find=find, replace=replace, append=append)
    src = Path(plan["path"])
    text = _decode_text(_read_bytes(src), src)
    if plan["mode"] == "replace":
        updated = text.replace(find, replace)
        replaced = int(plan["matches"])
    else:
        updated = text + append
        replaced = 0
    try:
        _write_text(src, updated)
    except PermissionError as exc:
        raise WindowsFSError("ACCESS_DENIED", f"Access denied: {src}") from exc
    except OSError as exc:
        raise WindowsFSError("OS_ERROR", f"Could not write {src}: {exc}") from exc
    if _decode_text(_read_bytes(src), src) != updated:
        raise WindowsFSError(
            "VERIFY_FAILED", f"The edit to {src} could not be verified."
        )
    return {
        "path": str(src),
        "mode": plan["mode"],
        "replaced": replaced,
        "verified": True,
    }


# ----------------------------------------------------------------------
# creating
# ----------------------------------------------------------------------
def create_file(
    path: str | Path, content: str = "", *, overwrite: bool = False
) -> dict[str, object]:
    """Create (or explicitly overwrite) a UTF-8 text file, then verify it."""
    if not isinstance(content, str):
        raise WindowsFSError("INVALID_NAME", "The file content must be text.")
    target = validate_destination(path, operation="create_file")
    overwritten = False
    if target.exists():
        if target.is_dir():
            raise WindowsFSError("CONFLICT", f"A folder already exists at {target}.")
        if not overwrite:
            raise WindowsFSError(
                "ALREADY_EXISTS",
                f"A file already exists at {target}. Choose a new name, or "
                "ask the user before overwriting it.",
            )
        overwritten = True

    data = content.encode("utf-8")
    try:
        if not target.parent.exists():
            _mkdir(target.parent)
        _write_text(target, content)
    except PermissionError as exc:
        raise WindowsFSError(
            "ACCESS_DENIED", f"Access denied: {target.parent}"
        ) from exc
    except OSError as exc:
        raise WindowsFSError("OS_ERROR", f"Could not write {target}: {exc}") from exc

    if not target.is_file() or target.stat().st_size != len(data):
        raise WindowsFSError(
            "VERIFY_FAILED",
            f"The file at {target} could not be verified after writing.",
        )
    return {
        "path": str(target),
        "size_bytes": len(data),
        "overwritten": overwritten,
    }


def create_folder(path: str | Path) -> dict[str, object]:
    """Create a folder (parents included), then verify it exists."""
    target = validate_destination(path, operation="create_folder")
    if target.exists():
        kind = "A folder" if target.is_dir() else "A file"
        raise WindowsFSError("ALREADY_EXISTS", f"{kind} already exists at {target}.")
    try:
        _mkdir(target)
    except PermissionError as exc:
        raise WindowsFSError(
            "ACCESS_DENIED", f"Access denied: {target.parent}"
        ) from exc
    except OSError as exc:
        raise WindowsFSError("OS_ERROR", f"Could not create {target}: {exc}") from exc
    if not target.is_dir():
        raise WindowsFSError(
            "VERIFY_FAILED", f"Could not verify the new folder at {target}."
        )
    return {"path": str(target), "created": True}


# ----------------------------------------------------------------------
# renaming
# ----------------------------------------------------------------------
def rename_path(source: str | Path, new_name: str) -> dict[str, object]:
    """Rename an item in place (same parent folder), then verify it moved."""
    src = require_source(source, operation="rename_path")
    if not isinstance(new_name, str) or not new_name.strip():
        raise WindowsFSError("INVALID_NAME", "The new name cannot be empty.")
    name = new_name.strip()
    if name in (".", "..") or any(ch in name for ch in '\\/:*?"<>|'):
        raise WindowsFSError(
            "INVALID_NAME",
            "The new name must be a plain name without path separators.",
        )
    target = src.parent / name
    validate_destination(target, operation="rename_path")
    if target == src:
        raise WindowsFSError(
            "ALREADY_EXISTS", f"{name!r} is already the name of this item."
        )
    if target.exists():
        raise WindowsFSError(
            "CONFLICT",
            f"Another item named {name!r} already exists in {src.parent}.",
        )
    try:
        _os_rename(src, target)
    except PermissionError as exc:
        raise WindowsFSError("ACCESS_DENIED", f"Access denied: {src}") from exc
    except OSError as exc:
        raise WindowsFSError("OS_ERROR", f"Could not rename {src}: {exc}") from exc
    if not target.exists() or src.exists():
        raise WindowsFSError(
            "VERIFY_FAILED", f"Could not verify the rename to {target}."
        )
    return {"from": str(src), "to": str(target)}


# ----------------------------------------------------------------------
# moving
# ----------------------------------------------------------------------
def plan_move(source: str | Path, destination: str | Path) -> tuple[Path, Path, Path]:
    """Policy and conflict preflight for a move: returns (src, dest, final).

    The tool layer runs this BEFORE staging a confirmation, so a conflict is
    reported to the user instead of surfacing only after they approve.
    """
    src = require_source(source, operation="move_path")
    dest = validate_destination(destination, operation="move_path")
    final = dest / src.name if dest.is_dir() else dest
    if final == src:
        raise WindowsFSError("ALREADY_EXISTS", f"{src.name} is already in {dest}.")
    if final.exists():
        raise WindowsFSError(
            "CONFLICT",
            f"An item already exists at {final}. Nothing was overwritten.",
        )
    return src, dest, final


def move_path(source: str | Path, destination: str | Path) -> dict[str, object]:
    """Move a file or folder, then verify the old path is gone."""
    src, _dest, final = plan_move(source, destination)
    try:
        if not final.parent.exists():
            _mkdir(final.parent)
        _shutil_move(src, final)
    except PermissionError as exc:
        raise WindowsFSError("ACCESS_DENIED", f"Access denied: {src}") from exc
    except OSError as exc:
        raise WindowsFSError("OS_ERROR", f"Could not move {src}: {exc}") from exc
    if not final.exists() or src.exists():
        raise WindowsFSError("VERIFY_FAILED", f"Could not verify the move to {final}.")
    return {"from": str(src), "to": str(final)}


# ----------------------------------------------------------------------
# copying
# ----------------------------------------------------------------------
def plan_copy(
    source: str | Path,
    destination: str | Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, Path, Path]:
    """Policy and conflict preflight for a copy: (src, dest, final)."""
    src = require_source(source, operation="copy_path")
    dest = validate_destination(destination, operation="copy_path")
    final = dest / src.name if dest.is_dir() else dest
    if final == src:
        raise WindowsFSError("CONFLICT", f"{src.name} cannot be copied onto itself.")
    if final.exists():
        if final.is_dir():
            raise WindowsFSError(
                "CONFLICT",
                f"A folder already exists at {final}. Nothing was replaced.",
            )
        if not overwrite:
            raise WindowsFSError(
                "CONFLICT",
                f"A file already exists at {final}. Nothing was overwritten.",
            )
    return src, dest, final


def copy_path(
    source: str | Path,
    destination: str | Path,
    *,
    overwrite: bool = False,
) -> dict[str, object]:
    """Copy a file or folder (source is kept), then verify the copy."""
    src, _dest, final = plan_copy(source, destination, overwrite=overwrite)
    try:
        if not final.parent.exists():
            _mkdir(final.parent)
        if src.is_dir():
            _shutil_copytree(src, final)
        else:
            _shutil_copy2(src, final)
    except PermissionError as exc:
        raise WindowsFSError("ACCESS_DENIED", f"Access denied: {src}") from exc
    except OSError as exc:
        raise WindowsFSError("OS_ERROR", f"Could not copy {src}: {exc}") from exc

    if src.is_dir():
        verified = final.is_dir() and {
            item.name.casefold() for item in final.iterdir()
        } == {item.name.casefold() for item in src.iterdir()}
    else:
        verified = final.is_file() and final.stat().st_size == src.stat().st_size
    if not verified:
        raise WindowsFSError("VERIFY_FAILED", f"Could not verify the copy at {final}.")
    return {"from": str(src), "to": str(final), "copied": True}


# ----------------------------------------------------------------------
# recycling and deleting
# ----------------------------------------------------------------------
def recycle_path(path: str | Path) -> dict[str, object]:
    """Move an item to the Windows Recycle Bin (recoverable), then verify."""
    src = require_source(path, operation="recycle_path")
    try:
        _recycle(src)
    except PermissionError as exc:
        raise WindowsFSError("ACCESS_DENIED", f"Access denied: {src}") from exc
    except OSError as exc:
        raise WindowsFSError("OS_ERROR", f"Could not recycle {src}: {exc}") from exc
    if src.exists():
        raise WindowsFSError(
            "VERIFY_FAILED", f"{src} is still present after recycling."
        )
    return {"path": str(src), "recycled": True}


def delete_path(path: str | Path, *, recursive: bool = False) -> dict[str, object]:
    """Permanently delete after policy validation; folders need recursive."""
    src = require_source(path, operation="delete_path")
    is_folder = src.is_dir()
    if is_folder and not recursive:
        raise WindowsFSError(
            "NEEDS_RECURSIVE",
            f"{src} is a folder. Pass recursive=True to delete it, or use "
            "recycle_path to move it to the Recycle Bin instead.",
        )
    try:
        if is_folder:
            _rmtree(src)
        else:
            _unlink(src)
    except PermissionError as exc:
        raise WindowsFSError("ACCESS_DENIED", f"Access denied: {src}") from exc
    except OSError as exc:
        raise WindowsFSError("OS_ERROR", f"Could not delete {src}: {exc}") from exc
    if src.exists():
        raise WindowsFSError("VERIFY_FAILED", f"{src} still exists after deletion.")
    return {"path": str(src), "deleted": True, "method": "permanent"}


# ----------------------------------------------------------------------
# opening and launching
# ----------------------------------------------------------------------
def open_path(path: str | Path) -> dict[str, object]:
    """Open a document or folder with its normal Windows file association."""
    resolved = validate_path(path)
    if not resolved.exists():
        raise WindowsFSError("NOT_FOUND", f"No such file or folder: {resolved}")
    extension = resolved.suffix.casefold()
    if not resolved.is_dir() and extension in _BLOCKED_OPEN_EXTENSIONS:
        raise WindowsFSError(
            "BLOCKED",
            f"Refusing to open {extension} files because they run programs. "
            "Use launch_application for known applications or the browser "
            "for web content instead.",
        )
    try:
        _startfile(resolved)
    except PermissionError as exc:
        raise WindowsFSError("ACCESS_DENIED", f"Access denied: {resolved}") from exc
    except OSError as exc:
        raise WindowsFSError("OS_ERROR", f"Could not open {resolved}: {exc}") from exc
    return {"path": str(resolved), "opened": True, "extension": extension}


def _allowed_apps_message() -> str:
    names = sorted({*ALLOWED_APPLICATIONS, *_APPLICATION_FIXED_PATHS})
    return "Allowed applications: " + ", ".join(names) + "."


def launch_application(application: str) -> dict[str, object]:
    """Launch an allowlisted application from a fixed location.

    Only fixed allowlisted names are accepted; a path, arguments, or any
    other name is refused, so arbitrary program execution is impossible.
    """
    if not isinstance(application, str) or not application.strip():
        raise WindowsFSError("UNKNOWN_APP", _allowed_apps_message())
    cleaned = application.strip()
    name = cleaned.casefold()
    if any(ch in name for ch in '\\/:*?"<>|'):
        raise WindowsFSError(
            "UNKNOWN_APP",
            "Give a simple application name, not a path or arguments. "
            + _allowed_apps_message(),
        )
    canonical = _LAUNCH_ALIASES.get(name, name)
    if (
        canonical not in ALLOWED_APPLICATIONS
        and canonical not in _APPLICATION_FIXED_PATHS
    ):
        raise WindowsFSError(
            "UNKNOWN_APP",
            f"{cleaned!r} is not an allowed application. " + _allowed_apps_message(),
        )
    candidates = _application_candidates(canonical)
    if not candidates:
        raise WindowsFSError(
            "UNKNOWN_APP",
            f"{cleaned!r} is not an allowed application. " + _allowed_apps_message(),
        )
    target = next((item for item in candidates if item.exists()), None)
    if target is None:
        raise WindowsFSError("APP_MISSING", f"{canonical} is not available on this PC.")
    try:
        _startfile(target)
    except PermissionError as exc:
        raise WindowsFSError("ACCESS_DENIED", f"Access denied: {target}") from exc
    except OSError as exc:
        raise WindowsFSError(
            "OS_ERROR", f"Could not launch {canonical}: {exc}"
        ) from exc
    return {
        "application": cleaned,
        "resolved_path": str(target),
        "launched": True,
    }
