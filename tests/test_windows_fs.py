"""TDD tests for the Windows filesystem engine (Part 2).

The engine (`src/windows_fs.py`) is LiveKit-free, like `browser.py`: it
performs the real filesystem work and enforces Part 1 security policy on
every operation - validation first, execution second, verification last.
All file operations happen in pytest temporary directories; known-folder
resolution only READS the real user profile (never writes to it).
"""

from __future__ import annotations

import os
import sys

import pytest

from windows_fs import (
    MAX_LIST_ENTRIES,
    MAX_SEARCH_RESULTS,
    WindowsFSError,
    copy_path,
    count_items,
    create_file,
    create_folder,
    delete_path,
    file_exists,
    folder_exists,
    get_file_info,
    launch_application,
    list_directory,
    move_path,
    open_path,
    recycle_path,
    rename_path,
    resolve_known_folder,
    resolve_user_path,
    search_files,
)
from windows_security import SecurityPolicyError

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows filesystem engine"
)


def _code(excinfo) -> str:
    return excinfo.value.code


# ----------------------------------------------------------------------
# known-folder resolution (no hard-coded usernames)
# ----------------------------------------------------------------------
def test_resolves_real_desktop() -> None:
    desktop = resolve_known_folder("desktop")
    assert desktop.is_absolute()
    assert desktop.exists()
    assert desktop.name.casefold() == "desktop"


def test_resolves_documents_from_profile() -> None:
    documents = resolve_known_folder("documents")
    assert documents.is_absolute()
    assert documents.exists()
    # resolved twice -> stable, profile-driven (no hard-coded username)
    assert resolve_known_folder("documents") == documents


def test_resolves_home_from_environment() -> None:
    import os
    from pathlib import Path

    home = resolve_known_folder("home")
    assert home == Path(os.environ["USERPROFILE"]).resolve()


def test_resolves_onedrive_by_name() -> None:
    onedrive = resolve_known_folder("onedrive")
    assert onedrive.is_absolute()
    assert "onedrive" in onedrive.name.casefold()


def test_resolve_rejects_unknown_location() -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        resolve_known_folder("treasure_chest")
    assert _code(excinfo) == "INVALID_NAME"


# ----------------------------------------------------------------------
# natural / contextual reference resolution
# ----------------------------------------------------------------------
def test_alias_resolution_matches_known_folder() -> None:
    assert resolve_user_path("downloads") == str(resolve_known_folder("downloads"))
    assert resolve_user_path("My Documents") == str(resolve_known_folder("documents"))
    assert resolve_user_path("the Desktop") == str(resolve_known_folder("desktop"))


def test_absolute_path_passes_through(tmp_path) -> None:
    target = tmp_path / "a.txt"
    assert resolve_user_path(str(target)) == str(target)


def test_relative_path_uses_context_directory(tmp_path) -> None:
    resolved = resolve_user_path("notes.txt", context_dir=str(tmp_path))
    assert resolved == str(tmp_path / "notes.txt")


def test_relative_path_falls_back_to_home(tmp_path) -> None:
    resolved = resolve_user_path("notes.txt")
    assert resolved.startswith(str(resolve_known_folder("home")))


def test_context_phrases_use_last_file_and_folder(tmp_path) -> None:
    last_file = str(tmp_path / "report.txt")
    last_folder = str(tmp_path / "archive")
    assert resolve_user_path("this file", context_file=last_file) == last_file
    assert resolve_user_path("that file", context_file=last_file) == last_file
    assert resolve_user_path("that folder", context_folder=last_folder) == last_folder


def test_surrounding_quotes_are_stripped(tmp_path) -> None:
    target = str(tmp_path / "quoted name.txt")
    assert resolve_user_path(f'"{target}"') == target


def test_empty_reference_is_rejected() -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        resolve_user_path("   ")
    assert _code(excinfo) == "INVALID_NAME"


# ----------------------------------------------------------------------
# Part 3: extended short-term context references
# ----------------------------------------------------------------------
def test_bare_pronouns_use_the_most_recent_item(tmp_path) -> None:
    item = str(tmp_path / "thing.txt")
    for phrase in ("it", "this", "that", "this one", "that one"):
        assert resolve_user_path(phrase, context_item=item) == item


def test_pronoun_falls_back_to_file_then_folder(tmp_path) -> None:
    last_file = str(tmp_path / "a.txt")
    last_folder = str(tmp_path / "dir")
    assert resolve_user_path("it", context_file=last_file) == last_file
    assert resolve_user_path("it", context_folder=last_folder) == last_folder


def test_pronoun_without_any_context_is_an_error() -> None:
    # Ambiguity must surface as an error the model turns into one question,
    # never as a guess.
    with pytest.raises(WindowsFSError) as excinfo:
        resolve_user_path("it")
    assert _code(excinfo) == "INVALID_NAME"
    assert "current item" in str(excinfo.value)


def test_previous_file_phrases(tmp_path) -> None:
    last_file = str(tmp_path / "report.txt")
    for phrase in (
        "previous file",
        "the previous file",
        "last file",
        "the last file",
    ):
        assert resolve_user_path(phrase, context_file=last_file) == last_file


def test_folder_we_created_phrases(tmp_path) -> None:
    last_folder = str(tmp_path / "new-folder")
    for phrase in (
        "folder we created",
        "the folder we created",
        "previous folder",
        "the previous folder",
    ):
        assert resolve_user_path(phrase, context_folder=last_folder) == last_folder


# ----------------------------------------------------------------------
# list_directory
# ----------------------------------------------------------------------
def test_list_directory_reports_entries(tmp_path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "hello.txt").write_text("hi", encoding="utf-8")

    result = list_directory(str(tmp_path))

    assert result["path"] == str(tmp_path.resolve())
    assert result["count"] == 2
    assert result["truncated"] is False
    by_name = {entry["name"]: entry for entry in result["entries"]}
    assert by_name["sub"]["type"] == "folder"
    assert by_name["hello.txt"]["type"] == "file"
    assert by_name["hello.txt"]["size_bytes"] == 2
    assert by_name["hello.txt"]["modified"]


def test_list_directory_missing_path(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        list_directory(str(tmp_path / "ghost"))
    assert _code(excinfo) == "NOT_FOUND"


def test_list_directory_on_a_file(tmp_path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        list_directory(str(target))
    assert _code(excinfo) == "NOT_FOUND"


def test_list_directory_caps_entries(tmp_path) -> None:
    for index in range(MAX_LIST_ENTRIES + 5):
        (tmp_path / f"f{index:04d}.tmp").write_text("", encoding="utf-8")
    result = list_directory(str(tmp_path))
    assert result["count"] == MAX_LIST_ENTRIES
    assert result["truncated"] is True


def test_list_directory_access_denied(tmp_path, monkeypatch) -> None:
    import windows_fs

    def deny(_path):
        raise PermissionError(5, "denied")

    monkeypatch.setattr(windows_fs, "_iter_directory", deny)
    with pytest.raises(WindowsFSError) as excinfo:
        list_directory(str(tmp_path))
    assert _code(excinfo) == "ACCESS_DENIED"


# ----------------------------------------------------------------------
# search_files
# ----------------------------------------------------------------------
def test_search_by_substring_is_recursive(tmp_path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "Budget2026.txt").write_text("x", encoding="utf-8")
    (tmp_path / "nested" / "budget-final.TXT").write_text("y", encoding="utf-8")
    (tmp_path / "other.txt").write_text("z", encoding="utf-8")

    result = search_files(str(tmp_path), "budget")

    assert result["count"] == 2
    names = {path.casefold() for path in result["results"]}
    assert str(tmp_path / "Budget2026.txt").casefold() in names
    assert str(tmp_path / "nested" / "budget-final.TXT").casefold() in names


def test_search_by_wildcard(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("y", encoding="utf-8")
    result = search_files(str(tmp_path), "*.txt")
    assert result["count"] == 1


def test_search_no_match_returns_empty(tmp_path) -> None:
    result = search_files(str(tmp_path), "zzz-not-here")
    assert result == {
        "root": str(tmp_path.resolve()),
        "pattern": "zzz-not-here",
        "results": [],
        "count": 0,
        "truncated": False,
    }


def test_search_rejects_empty_pattern(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        search_files(str(tmp_path), "  ")
    assert _code(excinfo) == "INVALID_NAME"


def test_search_rejects_path_like_pattern(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        search_files(str(tmp_path), "..\\*")
    assert _code(excinfo) == "INVALID_NAME"


def test_search_caps_results(tmp_path) -> None:
    for index in range(MAX_SEARCH_RESULTS + 3):
        (tmp_path / f"hit{index:03d}.log").write_text("", encoding="utf-8")
    result = search_files(str(tmp_path), "hit")
    assert result["count"] == MAX_SEARCH_RESULTS
    assert result["truncated"] is True


def test_search_missing_root(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        search_files(str(tmp_path / "ghost"), "x")
    assert _code(excinfo) == "NOT_FOUND"


# ----------------------------------------------------------------------
# existence and info
# ----------------------------------------------------------------------
def test_file_exists_and_folder_exists(tmp_path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    assert file_exists(str(target)) == {
        "path": str(target.resolve()),
        "exists": True,
    }
    assert folder_exists(str(target))["exists"] is False
    assert folder_exists(str(tmp_path))["exists"] is True
    assert file_exists(str(tmp_path / "ghost"))["exists"] is False


def test_get_file_info(tmp_path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    info = get_file_info(str(target))
    assert info["name"] == "notes.txt"
    assert info["type"] == "file"
    assert info["size_bytes"] == 5
    assert info["extension"] == ".txt"
    assert info["read_only"] is False
    folder_info = get_file_info(str(tmp_path))
    assert folder_info["type"] == "folder"
    assert folder_info["extension"] == ""


def test_get_file_info_missing(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        get_file_info(str(tmp_path / "ghost"))
    assert _code(excinfo) == "NOT_FOUND"


# ----------------------------------------------------------------------
# create
# ----------------------------------------------------------------------
def test_create_file_writes_and_verifies(tmp_path) -> None:
    target = tmp_path / "new" / "deep" / "note.txt"
    result = create_file(str(target), content="Srilatha was here")
    assert result["path"] == str(target.resolve())
    assert result["overwritten"] is False
    assert target.read_text(encoding="utf-8") == "Srilatha was here"
    assert result["size_bytes"] == len("Srilatha was here")


def test_create_file_conflict_is_reported(tmp_path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("keep me", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        create_file(str(target), content="clobber")
    assert _code(excinfo) == "ALREADY_EXISTS"
    assert target.read_text(encoding="utf-8") == "keep me"  # untouched


def test_create_file_overwrite_when_allowed(tmp_path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    result = create_file(str(target), content="new", overwrite=True)
    assert result["overwritten"] is True
    assert target.read_text(encoding="utf-8") == "new"


def test_create_file_verify_failure_detected(tmp_path, monkeypatch) -> None:
    import windows_fs

    def pretend_write(_path, _data):  # write silently does nothing
        return None

    monkeypatch.setattr(windows_fs, "_write_text", pretend_write)
    with pytest.raises(WindowsFSError) as excinfo:
        create_file(str(tmp_path / "ghost.txt"), content="x")
    assert _code(excinfo) == "VERIFY_FAILED"


def test_create_file_over_a_folder(tmp_path) -> None:
    folder = tmp_path / "adir"
    folder.mkdir()
    with pytest.raises(WindowsFSError) as excinfo:
        create_file(str(folder), content="x", overwrite=True)
    assert _code(excinfo) == "CONFLICT"


def test_create_folder(tmp_path) -> None:
    target = tmp_path / "level1" / "level2"
    result = create_folder(str(target))
    assert result["path"] == str(target.resolve())
    assert target.is_dir()


def test_create_folder_conflict(tmp_path) -> None:
    target = tmp_path / "exists"
    target.mkdir()
    with pytest.raises(WindowsFSError) as excinfo:
        create_folder(str(target))
    assert _code(excinfo) == "ALREADY_EXISTS"


def test_create_folder_over_existing_file(tmp_path) -> None:
    file_path = tmp_path / "afile"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        create_folder(str(file_path))
    assert _code(excinfo) == "ALREADY_EXISTS"
    assert file_path.is_file()  # untouched


def test_create_file_through_junction_is_blocked(tmp_path) -> None:
    # Writing through a junction that points into a protected location
    # must be refused after resolution, never followed.
    import _winapi

    link = tmp_path / "innocent"
    _winapi.CreateJunction("C:\\Windows", str(link))
    with pytest.raises(SecurityPolicyError, match="protected"):
        create_file(str(link / "part4.txt"), content="x")
    assert not os.path.exists(r"C:\Windows\part4.txt")


# ----------------------------------------------------------------------
# rename
# ----------------------------------------------------------------------
def test_rename_file(tmp_path) -> None:
    source = tmp_path / "old.txt"
    source.write_text("body", encoding="utf-8")
    result = rename_path(str(source), "new.txt")
    target = tmp_path / "new.txt"
    assert result["from"] == str(source.resolve())
    assert result["to"] == str(target.resolve())
    assert target.read_text(encoding="utf-8") == "body"
    assert not source.exists()


def test_rename_conflict_never_overwrites(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    existing = tmp_path / "b.txt"
    existing.write_text("b", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        rename_path(str(source), "b.txt")
    assert _code(excinfo) == "CONFLICT"
    assert existing.read_text(encoding="utf-8") == "b"
    assert source.exists()


def test_rename_rejects_path_separators_in_new_name(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        rename_path(str(source), "..\\..\\Windows\\evil.txt")
    assert _code(excinfo) == "INVALID_NAME"
    with pytest.raises(WindowsFSError) as excinfo:
        rename_path(str(source), "sub\\b.txt")
    assert _code(excinfo) == "INVALID_NAME"
    assert source.exists()


def test_rename_same_name_reports_conflict(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        rename_path(str(source), "a.txt")
    assert _code(excinfo) == "ALREADY_EXISTS"


def test_rename_missing_source(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        rename_path(str(tmp_path / "ghost"), "new")
    assert _code(excinfo) == "NOT_FOUND"


def test_rename_protected_source_is_rejected() -> None:
    with pytest.raises(SecurityPolicyError, match="protected"):
        rename_path("C:\\Windows\\notepad.exe", "stolen.exe")


def test_rename_verify_failure_detected(tmp_path, monkeypatch) -> None:
    import windows_fs

    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    monkeypatch.setattr(windows_fs, "_os_rename", lambda *_: None)
    with pytest.raises(WindowsFSError) as excinfo:
        rename_path(str(source), "b.txt")
    assert _code(excinfo) == "VERIFY_FAILED"


# ----------------------------------------------------------------------
# move
# ----------------------------------------------------------------------
def test_move_file_to_new_location(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("payload", encoding="utf-8")
    destination = tmp_path / "elsewhere" / "b.txt"
    result = move_path(str(source), str(destination))
    assert result["to"] == str(destination.resolve())
    assert destination.read_text(encoding="utf-8") == "payload"
    assert not source.exists()


def test_move_file_into_existing_folder(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("payload", encoding="utf-8")
    folder = tmp_path / "inbox"
    folder.mkdir()
    result = move_path(str(source), str(folder))
    assert (folder / "a.txt").exists()
    assert result["to"] == str((folder / "a.txt").resolve())


def test_move_conflict_never_overwrites(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    target = tmp_path / "b.txt"
    target.write_text("b", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        move_path(str(source), str(target))
    assert _code(excinfo) == "CONFLICT"
    assert target.read_text(encoding="utf-8") == "b"


def test_move_onto_itself_is_rejected(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        move_path(str(source), str(tmp_path))  # lands on itself
    assert _code(excinfo) == "ALREADY_EXISTS"


def test_move_missing_source(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        move_path(str(tmp_path / "ghost"), str(tmp_path / "x.txt"))
    assert _code(excinfo) == "NOT_FOUND"


def test_move_rejects_protected_destination(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    with pytest.raises(SecurityPolicyError, match="protected"):
        move_path(str(source), "C:\\Windows\\evil.txt")
    assert source.exists()


def test_move_from_protected_location_is_rejected() -> None:
    with pytest.raises(SecurityPolicyError, match="protected"):
        move_path(
            "C:\\Windows\\System32\\drivers\\etc\\hosts",
            "C:\\Users\\public-stolen.txt",
        )


# ----------------------------------------------------------------------
# copy
# ----------------------------------------------------------------------
def test_copy_file(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("data", encoding="utf-8")
    destination = tmp_path / "copy.txt"
    copy_path(str(source), str(destination))
    assert destination.read_text(encoding="utf-8") == "data"
    assert source.exists()  # copy does not consume


def test_copy_folder_recursively(tmp_path) -> None:
    source = tmp_path / "tree"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "deep.txt").write_text("deep", encoding="utf-8")
    destination = tmp_path / "tree-copy"
    copy_path(str(source), str(destination))
    assert (destination / "nested" / "deep.txt").read_text(encoding="utf-8") == "deep"
    assert source.exists()


def test_copy_conflict_never_overwrites(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    target = tmp_path / "b.txt"
    target.write_text("b", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        copy_path(str(source), str(target))
    assert _code(excinfo) == "CONFLICT"
    assert target.read_text(encoding="utf-8") == "b"


def test_copy_overwrite_when_allowed(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("new content", encoding="utf-8")
    target = tmp_path / "b.txt"
    target.write_text("old", encoding="utf-8")
    result = copy_path(str(source), str(target), overwrite=True)
    assert result["copied"] is True
    assert target.read_text(encoding="utf-8") == "new content"


def test_copy_folder_conflict(tmp_path) -> None:
    source = tmp_path / "tree"
    source.mkdir()
    (source / "x.txt").write_text("x", encoding="utf-8")
    # copying INTO a folder that already holds a same-name child conflicts
    existing = tmp_path / "target"
    (existing / "tree").mkdir(parents=True)
    (existing / "tree" / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        copy_path(str(source), str(existing))
    assert _code(excinfo) == "CONFLICT"
    assert (existing / "tree" / "keep.txt").read_text(encoding="utf-8") == ("keep")


def test_copy_rejects_protected_destination() -> None:
    # reading system files is allowed (Part 1), writing them is not
    with pytest.raises(SecurityPolicyError, match="protected"):
        copy_path(
            "C:\\Windows\\System32\\kernel32.dll",
            "C:\\Windows\\System32\\kernel32-stolen.dll",
        )


# ----------------------------------------------------------------------
# recycle / delete
# ----------------------------------------------------------------------
def test_recycle_removes_file(tmp_path) -> None:
    target = tmp_path / "trash-me.txt"
    target.write_text("bye", encoding="utf-8")
    result = recycle_path(str(target))
    assert result == {"path": str(target.resolve()), "recycled": True}
    assert not target.exists()


def test_recycle_removes_folder(tmp_path) -> None:
    folder = tmp_path / "trash-me"
    folder.mkdir()
    (folder / "x.txt").write_text("x", encoding="utf-8")
    recycle_path(str(folder))
    assert not folder.exists()


def test_recycle_missing(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        recycle_path(str(tmp_path / "ghost"))
    assert _code(excinfo) == "NOT_FOUND"


def test_recycle_protected_is_rejected() -> None:
    with pytest.raises(SecurityPolicyError, match="protected"):
        recycle_path("C:\\Windows\\System32\\kernel32.dll")
    with pytest.raises(SecurityPolicyError, match="protected"):
        recycle_path("C:\\Program Files")


def test_delete_file_permanently(tmp_path) -> None:
    target = tmp_path / "bye.txt"
    target.write_text("x", encoding="utf-8")
    result = delete_path(str(target))
    assert result["method"] == "permanent"
    assert not target.exists()


def test_delete_folder_requires_recursive_flag(tmp_path) -> None:
    folder = tmp_path / "tree"
    folder.mkdir()
    with pytest.raises(WindowsFSError) as excinfo:
        delete_path(str(folder))
    assert _code(excinfo) == "NEEDS_RECURSIVE"
    delete_path(str(folder), recursive=True)
    assert not folder.exists()


def test_delete_blocked_for_protected_paths() -> None:
    with pytest.raises(SecurityPolicyError, match="protected"):
        delete_path("C:\\Windows", recursive=True)
    with pytest.raises(SecurityPolicyError, match="protected"):
        delete_path("C:\\Program Files\\AnyApp", recursive=True)


def test_delete_permission_failure(tmp_path, monkeypatch) -> None:
    import windows_fs

    target = tmp_path / "locked.txt"
    target.write_text("x", encoding="utf-8")

    def deny(_path):
        raise PermissionError(5, "denied")

    monkeypatch.setattr(windows_fs, "_unlink", deny)
    with pytest.raises(WindowsFSError) as excinfo:
        delete_path(str(target))
    assert _code(excinfo) == "ACCESS_DENIED"


def test_delete_missing(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        delete_path(str(tmp_path / "ghost"))
    assert _code(excinfo) == "NOT_FOUND"


# ----------------------------------------------------------------------
# open_path / launch_application
# ----------------------------------------------------------------------
def test_open_path_allows_documents(tmp_path, monkeypatch) -> None:
    import windows_fs

    started: list[str] = []
    monkeypatch.setattr(
        windows_fs, "_startfile", lambda path: started.append(str(path))
    )
    target = tmp_path / "notes.txt"
    target.write_text("x", encoding="utf-8")
    result = open_path(str(target))
    assert result["opened"] is True
    assert started == [str(target.resolve())]


def test_open_path_allows_folders(tmp_path, monkeypatch) -> None:
    import windows_fs

    started: list[str] = []
    monkeypatch.setattr(
        windows_fs, "_startfile", lambda path: started.append(str(path))
    )
    result = open_path(str(tmp_path))
    assert result["opened"] is True
    assert len(started) == 1


def test_open_path_blocks_executables(tmp_path, monkeypatch) -> None:
    import windows_fs

    monkeypatch.setattr(windows_fs, "_startfile", lambda path: pytest.fail())
    for name in ("tool.exe", "setup.msi", "script.ps1", "run.bat", "x.lnk"):
        target = tmp_path / name
        target.write_text("x", encoding="utf-8")
        with pytest.raises(WindowsFSError) as excinfo:
            open_path(str(target))
        assert _code(excinfo) == "BLOCKED"


def test_open_path_missing(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        open_path(str(tmp_path / "ghost.txt"))
    assert _code(excinfo) == "NOT_FOUND"


def test_launch_application_resolves_known_app(monkeypatch) -> None:
    import windows_fs

    started: list[str] = []
    monkeypatch.setattr(
        windows_fs, "_startfile", lambda path: started.append(str(path))
    )
    result = launch_application("Notepad")
    assert result["launched"] is True
    assert result["resolved_path"].casefold().endswith("notepad.exe")
    assert len(started) == 1


def test_launch_application_rejects_unknown_names() -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        launch_application("hacker-tool")
    assert _code(excinfo) == "UNKNOWN_APP"
    assert "notepad" in str(excinfo.value).casefold()


def test_launch_application_rejects_paths_and_shell_names() -> None:
    for bad in (
        "C:\\Windows\\System32\\cmd.exe",
        "powershell",
        "..\\evil.exe",
        "calc.exe C:\\data",
    ):
        with pytest.raises(WindowsFSError) as excinfo:
            launch_application(bad)
        assert _code(excinfo) == "UNKNOWN_APP"


def test_launch_vs_code_resolves_fixed_candidate(tmp_path, monkeypatch) -> None:
    # VS Code is allowlisted with fixed, environment-driven candidate paths
    # (never model-supplied); candidates are stubbed for determinism.
    import windows_fs

    fake = tmp_path / "Code.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(windows_fs, "_application_candidates", lambda _n: [fake])
    started: list[str] = []
    monkeypatch.setattr(
        windows_fs, "_startfile", lambda path: started.append(str(path))
    )
    result = launch_application("VS Code")
    assert result["launched"] is True
    assert result["resolved_path"] == str(fake)
    assert started == [str(fake)]


def test_launch_reports_missing_application(monkeypatch) -> None:
    import windows_fs

    monkeypatch.setattr(
        windows_fs,
        "_application_candidates",
        lambda _n: [windows_fs.Path(r"C:\\no-such-app\\Code.exe")],
    )
    monkeypatch.setattr(windows_fs, "_startfile", lambda path: pytest.fail())
    with pytest.raises(WindowsFSError) as excinfo:
        launch_application("vs code")
    assert _code(excinfo) == "APP_MISSING"


# ----------------------------------------------------------------------
# bulk accounting
# ----------------------------------------------------------------------
def test_count_items_single_and_nested(tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("x", encoding="utf-8")
    assert count_items(target) == 1
    tree = tmp_path / "tree"
    (tree / "a").mkdir(parents=True)
    (tree / "b.txt").write_text("x", encoding="utf-8")
    (tree / "a" / "c.txt").write_text("x", encoding="utf-8")
    assert count_items(tree) == 3


def test_count_items_caps_quickly(tmp_path) -> None:
    from windows_security import MAX_BULK_ITEMS

    tree = tmp_path / "big"
    tree.mkdir()
    for index in range(60):
        (tree / f"f{index}.tmp").write_text("", encoding="utf-8")
    # capped walk must not need the whole tree to exceed the cap
    assert count_items(tree) >= 51
    assert count_items(tree) <= MAX_BULK_ITEMS + 1
