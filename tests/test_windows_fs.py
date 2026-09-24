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
    MAX_READ_BYTES,
    MAX_SEARCH_RESULTS,
    MAX_TREE_DEPTH,
    MAX_TREE_ENTRIES,
    WindowsFSError,
    copy_path,
    count_items,
    create_file,
    create_folder,
    delete_path,
    edit_file,
    file_exists,
    folder_exists,
    get_file_info,
    inspect_tree,
    launch_application,
    list_directory,
    move_path,
    open_path,
    plan_edit,
    read_file,
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


def test_resolve_known_folder_prefix_beats_stale_context() -> None:
    downloads = resolve_known_folder("downloads")
    expected = str(downloads / "Rushi")
    assert (
        resolve_user_path("Downloads\\Rushi", context_dir="C:\\stale\\ctx") == expected
    )
    assert (
        resolve_user_path("Downloads/Rushi", context_dir="C:\\stale\\ctx") == expected
    )
    assert resolve_user_path("Downloads\\", context_dir="C:\\stale\\ctx") == str(
        downloads
    )


def test_resolve_known_folder_prefix_articles_and_synonyms() -> None:
    assert resolve_user_path("my documents\\a.txt", context_dir="C:\\stale") == str(
        resolve_known_folder("documents") / "a.txt"
    )
    assert resolve_user_path("photos\\cat.png", context_dir="C:\\stale") == str(
        resolve_known_folder("pictures") / "cat.png"
    )


def test_resolve_plain_relative_still_uses_context(tmp_path) -> None:
    resolved = resolve_user_path("sub\\file.txt", context_dir=str(tmp_path))
    assert resolved == str(tmp_path / "sub" / "file.txt")


def test_resolve_profile_folder_via_home_adaptively(monkeypatch, tmp_path) -> None:
    import windows_fs

    monkeypatch.setattr(windows_fs.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "Projects").mkdir()
    resolved = resolve_user_path("Projects\\doc.txt", context_dir="C:\\stale\\ctx")
    assert resolved == str(tmp_path / "Projects" / "doc.txt")


def test_resolve_adapts_to_shell_folders_discovered_live(tmp_path, monkeypatch) -> None:
    import windows_fs

    fav = tmp_path / "Faves"
    fav.mkdir()
    real = windows_fs._shell_folder_map
    monkeypatch.setattr(
        windows_fs, "_shell_folder_map", lambda: {**real(), "favorites": fav}
    )
    assert resolve_user_path(
        "Favorites\\links.txt", context_dir="C:\\stale\\ctx"
    ) == str(fav / "links.txt")
    assert resolve_user_path("Favorites", context_dir="C:\\stale\\ctx") == str(fav)


def test_resolve_tolerates_singular_folder_speech() -> None:
    resolved = resolve_user_path("download\\p.txt", context_dir="C:\\stale\\ctx")
    assert resolved == str(resolve_known_folder("downloads") / "p.txt")


def test_resolve_standard_folder_wins_over_context_twin(tmp_path) -> None:
    # A local folder that merely shares a standard name must not hijack the
    # reference: real standard folders win, then existing context folders.
    (tmp_path / "Downloads").mkdir()
    resolved = resolve_user_path("Downloads\\x", context_dir=str(tmp_path))
    assert resolved == str(resolve_known_folder("downloads") / "x")


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


def test_list_directory_shows_folders_before_capped_files(tmp_path) -> None:
    # A folder name sorting after hundreds of files must never be silently
    # cut by the cap: navigation targets are listed before files.
    for index in range(MAX_LIST_ENTRIES + 5):
        (tmp_path / f"f{index:04d}.tmp").write_text("", encoding="utf-8")
    (tmp_path / "zz-final-sub").mkdir()
    result = list_directory(str(tmp_path))
    names = [entry["name"] for entry in result["entries"]]
    assert names[0] == "zz-final-sub"
    assert "zz-final-sub" in names
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


def test_search_exact_name_match_ranked_first(tmp_path) -> None:
    # An item literally named like the query is what the user asked for; it
    # must survive the result cap even when dozens of substring matches
    # would otherwise fill it in walk order.
    for index in range(MAX_SEARCH_RESULTS + 5):
        (tmp_path / f"aaa-rushi-{index:03d}.txt").write_text("", encoding="utf-8")
    (tmp_path / "Rushi").mkdir()
    result = search_files(str(tmp_path), "rushi")
    assert result["count"] == MAX_SEARCH_RESULTS
    assert os.path.basename(result["results"][0]) == "Rushi"
    assert result["truncated"] is True


def test_search_truncated_when_exact_displaces_a_result(tmp_path) -> None:
    for index in range(MAX_SEARCH_RESULTS):
        (tmp_path / f"aaa-hit-{index:03d}.log").write_text("", encoding="utf-8")
    (tmp_path / "hit").mkdir()
    result = search_files(str(tmp_path), "hit")
    assert os.path.basename(result["results"][0]) == "hit"
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


# ----------------------------------------------------------------------
# Part 7: reading files, trees, and documents
# ----------------------------------------------------------------------
def _make_docx(path, paragraphs: list[str]):
    """Build a minimal .docx (a zip containing word/document.xml)."""
    import zipfile
    from xml.sax.saxutils import escape

    body = "".join(
        f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>" for text in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
    return path


def _make_pptx(path, slides: list[str]):
    """Build a minimal .pptx (a zip with ppt/slides/slideN.xml files)."""
    import zipfile
    from xml.sax.saxutils import escape

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        for index, text in enumerate(slides, start=1):
            slide = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<p:sld xmlns:a="http://schemas.openxmlformats.org/'
                'drawingml/2006/main" xmlns:p="http://schemas.openxmlformats'
                '.org/presentationml/2006/main">'
                "<p:cSld><p:spTree><p:sp><p:txBody><a:p>"
                f"<a:r><a:t>{escape(text)}</a:t></a:r>"
                "</a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
            )
            archive.writestr(f"ppt/slides/slide{index}.xml", slide)
    return path


def _minimal_pdf(text: str) -> bytes:
    """A one-page PDF with one line of extractable text (or none)."""
    if text:
        content = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
    else:
        content = b"BT ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length "
        + str(len(content)).encode()
        + b" >>\nstream\n"
        + content
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += b"trailer << /Size 6 /Root 1 0 R >>\n"
    out += f"startxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def test_read_file_returns_text(tmp_path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("hello\nworld", encoding="utf-8")
    result = read_file(str(target))
    assert set(result) == {
        "path",
        "name",
        "extension",
        "kind",
        "size_bytes",
        "content",
        "lines",
        "truncated",
    }
    assert result["path"] == str(target.resolve())
    assert result["name"] == "note.txt"
    assert result["extension"] == ".txt"
    assert result["kind"] == "text"
    assert result["content"] == "hello\nworld"
    assert result["lines"] == 2
    assert result["truncated"] is False
    assert result["size_bytes"] == target.stat().st_size


def test_read_file_empty_text_is_not_an_error(tmp_path) -> None:
    target = tmp_path / "empty.txt"
    target.write_text("", encoding="utf-8")
    result = read_file(str(target))
    assert result["content"] == ""
    assert result["lines"] == 0
    assert result["truncated"] is False


def test_read_file_missing(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        read_file(str(tmp_path / "ghost.txt"))
    assert _code(excinfo) == "NOT_FOUND"


def test_read_file_folder_reports_not_found(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        read_file(str(tmp_path))
    assert _code(excinfo) == "NOT_FOUND"


def test_read_file_rejects_binary_content(tmp_path) -> None:
    target = tmp_path / "blob.dat"
    target.write_bytes(b"MZ\x90\x00\x03\x00")
    with pytest.raises(WindowsFSError) as excinfo:
        read_file(str(target))
    assert _code(excinfo) == "UNSUPPORTED"


def test_read_file_rejects_non_utf8(tmp_path) -> None:
    target = tmp_path / "latin.txt"
    target.write_bytes(b"\xff\xfe caf\xe9")
    with pytest.raises(WindowsFSError) as excinfo:
        read_file(str(target))
    assert _code(excinfo) == "UNSUPPORTED"


def test_read_file_rejects_files_larger_than_the_cap(tmp_path) -> None:
    target = tmp_path / "huge.txt"
    target.write_bytes(b"a" * (MAX_READ_BYTES + 1))
    with pytest.raises(WindowsFSError) as excinfo:
        read_file(str(target))
    assert _code(excinfo) == "TOO_LARGE"


def test_read_file_truncates_long_content(tmp_path) -> None:
    target = tmp_path / "long.txt"
    target.write_text("x" * 250_000, encoding="utf-8")
    result = read_file(str(target))
    assert result["truncated"] is True
    assert len(result["content"]) == 200_000


def test_read_file_rejects_spreadsheets(tmp_path) -> None:
    target = tmp_path / "book.xlsx"
    target.write_bytes(b"PK\x03\x04 not really an xlsx")
    with pytest.raises(WindowsFSError) as excinfo:
        read_file(str(target))
    assert _code(excinfo) == "UNSUPPORTED"


def test_read_file_extracts_docx_text(tmp_path) -> None:
    target = _make_docx(tmp_path / "report.docx", ["Quarterly report", "Revenue is up"])
    result = read_file(str(target))
    assert result["kind"] == "docx"
    assert result["extension"] == ".docx"
    assert "Quarterly report" in result["content"]
    assert "Revenue is up" in result["content"]
    assert result["content"].index("Quarterly report") < result["content"].index(
        "Revenue is up"
    )
    assert result["truncated"] is False


def test_read_file_extracts_pptx_slides(tmp_path) -> None:
    target = _make_pptx(tmp_path / "deck.pptx", ["Slide one text", "Slide two text"])
    result = read_file(str(target))
    assert result["kind"] == "pptx"
    content = str(result["content"])
    assert "--- slide 1 ---" in content
    assert "--- slide 2 ---" in content
    assert "Slide one text" in content
    assert "Slide two text" in content
    assert content.index("--- slide 1 ---") < content.index("--- slide 2 ---")


def test_read_file_extracts_pdf_text(tmp_path) -> None:
    target = tmp_path / "sample.pdf"
    target.write_bytes(_minimal_pdf("PART7 PDF SAMPLE"))
    result = read_file(str(target))
    assert result["kind"] == "pdf"
    assert "PART7 PDF SAMPLE" in str(result["content"])


def test_read_file_pdf_without_text_is_honest_not_an_error(tmp_path) -> None:
    target = tmp_path / "blank.pdf"
    target.write_bytes(_minimal_pdf(""))
    result = read_file(str(target))
    assert result["kind"] == "pdf"
    assert "no extractable text" in str(result["content"]).casefold()


def test_read_file_encrypted_pdf_is_unsupported(tmp_path) -> None:
    from pypdf import PdfWriter

    target = tmp_path / "locked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret-password")
    with target.open("wb") as handle:
        writer.write(handle)
    with pytest.raises(WindowsFSError) as excinfo:
        read_file(str(target))
    assert _code(excinfo) == "UNSUPPORTED"


def test_read_file_corrupt_docx_is_unsupported(tmp_path) -> None:
    target = tmp_path / "broken.docx"
    target.write_bytes(b"PK\x03\x04 this is not a zip archive")
    with pytest.raises(WindowsFSError) as excinfo:
        read_file(str(target))
    assert _code(excinfo) == "UNSUPPORTED"


def test_read_file_pdf_without_pypdf_is_unsupported(tmp_path, monkeypatch) -> None:
    target = tmp_path / "sample.pdf"
    target.write_bytes(_minimal_pdf("PART7 PDF SAMPLE"))
    monkeypatch.setitem(sys.modules, "pypdf", None)
    with pytest.raises(WindowsFSError) as excinfo:
        read_file(str(target))
    assert _code(excinfo) == "UNSUPPORTED"


# ----------------------------------------------------------------------
# Part 7: bounded folder trees
# ----------------------------------------------------------------------
def test_tree_limits_are_bounded_constants() -> None:
    assert MAX_TREE_DEPTH == 5
    assert MAX_TREE_ENTRIES == 400


def test_inspect_tree_reports_structure(tmp_path) -> None:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print(1)", encoding="utf-8")
    (root / "README.md").write_text("# proj", encoding="utf-8")
    result = inspect_tree(str(root))
    assert result["path"] == str(root.resolve())
    assert "README.md" in result["tree"]
    assert "app.py" in result["tree"]
    assert "src" in result["tree"]
    assert "\u251c" in result["tree"] or "\u2514" in result["tree"]
    assert result["files"] == 2
    assert result["folders"] == 1
    assert result["total_size_bytes"] == len("print(1)") + len("# proj")
    assert result["extensions"] == {".md": 1, ".py": 1}
    assert result["important_files"] == ["README.md"]
    assert result["truncated"] is False
    assert result["skipped"] == 0


def test_inspect_tree_depth_is_bounded(tmp_path) -> None:
    root = tmp_path / "deep"
    nested = root / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "deep.txt").write_text("x", encoding="utf-8")
    result = inspect_tree(str(root), max_depth=2)
    assert result["truncated"] is True
    assert "deep.txt" not in result["tree"]


def test_inspect_tree_entry_count_is_bounded(tmp_path) -> None:
    root = tmp_path / "many"
    root.mkdir()
    for index in range(8):
        (root / f"file{index}.txt").write_text("x", encoding="utf-8")
    result = inspect_tree(str(root), max_entries=5)
    assert result["truncated"] is True
    assert result["files"] + result["folders"] == 5


def test_inspect_tree_never_descends_into_junctions(tmp_path) -> None:
    import _winapi

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "junction-secret.txt").write_text("secret", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "real.txt").write_text("x", encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(root / "link"))
    result = inspect_tree(str(root))
    assert "link" in result["tree"]  # the junction is listed ...
    assert "junction-secret.txt" not in result["tree"]  # ... never followed
    assert result["files"] == 1
    assert result["skipped"] >= 1
    assert result["truncated"] is False


def test_inspect_tree_missing(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        inspect_tree(str(tmp_path / "ghost"))
    assert _code(excinfo) == "NOT_FOUND"


def test_inspect_tree_rejects_a_file(tmp_path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        inspect_tree(str(target))
    assert _code(excinfo) == "NOT_FOUND"


# ----------------------------------------------------------------------
# Part 7: plan_edit preflight (everything validated BEFORE confirmation)
# ----------------------------------------------------------------------
def test_plan_edit_counts_matches(tmp_path) -> None:
    target = tmp_path / "app.cfg"
    target.write_text("x=1\ny=1", encoding="utf-8")
    plan = plan_edit(str(target), mode="replace", find="1", replace="2")
    assert plan == {"path": str(target.resolve()), "mode": "replace", "matches": 2}


def test_plan_edit_append_mode_never_writes(tmp_path) -> None:
    target = tmp_path / "log.txt"
    target.write_text("line", encoding="utf-8")
    plan = plan_edit(str(target), mode="append", append="\nmore")
    assert plan["mode"] == "append"
    assert target.read_text(encoding="utf-8") == "line"


def test_plan_edit_invalid_mode(tmp_path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        plan_edit(str(target), mode="write", find="x", replace="y")
    assert _code(excinfo) == "INVALID_NAME"


def test_plan_edit_replace_requires_a_find_text(tmp_path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        plan_edit(str(target), mode="replace", find="", replace="y")
    assert _code(excinfo) == "INVALID_NAME"


def test_plan_edit_missing_file(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        plan_edit(str(tmp_path / "ghost.txt"), mode="append", append="x")
    assert _code(excinfo) == "NOT_FOUND"


def test_plan_edit_find_absent(tmp_path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        plan_edit(str(target), mode="replace", find="zzz", replace="q")
    assert _code(excinfo) == "NO_MATCH"


def test_plan_edit_rejects_office_documents(tmp_path) -> None:
    target = _make_docx(tmp_path / "report.docx", ["hello"])
    with pytest.raises(WindowsFSError) as excinfo:
        plan_edit(str(target), mode="append", append="x")
    assert _code(excinfo) == "UNSUPPORTED"


def test_plan_edit_rejects_protected_sources(tmp_path) -> None:
    with pytest.raises(SecurityPolicyError, match="protected"):
        plan_edit("C:\\Windows\\System32\\kernel32.dll", mode="append", append="x")


def test_plan_edit_rejects_binary_files(tmp_path) -> None:
    target = tmp_path / "blob.dat"
    target.write_bytes(b"MZ\x00\x01")
    with pytest.raises(WindowsFSError) as excinfo:
        plan_edit(str(target), mode="append", append="x")
    assert _code(excinfo) == "UNSUPPORTED"


def test_plan_edit_rejects_files_over_the_size_cap(tmp_path) -> None:
    target = tmp_path / "huge.txt"
    target.write_bytes(b"a" * (MAX_READ_BYTES + 1))
    with pytest.raises(WindowsFSError) as excinfo:
        plan_edit(str(target), mode="append", append="x")
    assert _code(excinfo) == "TOO_LARGE"


# ----------------------------------------------------------------------
# Part 7: edit_file (read-before-write, verified after every write)
# ----------------------------------------------------------------------
def test_edit_file_replaces_all_occurrences(tmp_path) -> None:
    target = tmp_path / "cfg.txt"
    target.write_text("a=1;b=1;c=1", encoding="utf-8")
    result = edit_file(str(target), mode="replace", find="=1", replace="=2")
    assert result["path"] == str(target.resolve())
    assert result["mode"] == "replace"
    assert result["replaced"] == 3
    assert result["verified"] is True
    assert target.read_text(encoding="utf-8") == "a=2;b=2;c=2"


def test_edit_file_appends_verbatim(tmp_path) -> None:
    target = tmp_path / "log.txt"
    target.write_text("first", encoding="utf-8")
    result = edit_file(str(target), mode="append", append="\nsecond")
    assert result["mode"] == "append"
    assert result["replaced"] == 0
    assert result["verified"] is True
    assert target.read_text(encoding="utf-8") == "first\nsecond"


def test_edit_file_missing_find_does_not_write(tmp_path) -> None:
    target = tmp_path / "cfg.txt"
    target.write_text("unchanged", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        edit_file(str(target), mode="replace", find="zzz", replace="q")
    assert _code(excinfo) == "NO_MATCH"
    assert target.read_text(encoding="utf-8") == "unchanged"


def test_edit_file_invalid_mode(tmp_path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(WindowsFSError) as excinfo:
        edit_file(str(target), mode="delete", find="x", replace="y")
    assert _code(excinfo) == "INVALID_NAME"


def test_edit_file_missing(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        edit_file(str(tmp_path / "ghost.txt"), mode="append", append="x")
    assert _code(excinfo) == "NOT_FOUND"


def test_edit_file_folder_is_not_editable(tmp_path) -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        edit_file(str(tmp_path), mode="append", append="x")
    assert _code(excinfo) == "NOT_FOUND"


def test_edit_file_rejects_office_documents(tmp_path) -> None:
    target = _make_docx(tmp_path / "report.docx", ["hello"])
    with pytest.raises(WindowsFSError) as excinfo:
        edit_file(str(target), mode="append", append="x")
    assert _code(excinfo) == "UNSUPPORTED"


def test_edit_file_rejects_binary_files(tmp_path) -> None:
    target = tmp_path / "blob.dat"
    target.write_bytes(b"MZ\x00\x01")
    with pytest.raises(WindowsFSError) as excinfo:
        edit_file(str(target), mode="append", append="x")
    assert _code(excinfo) == "UNSUPPORTED"


def test_edit_file_rejects_files_over_the_size_cap(tmp_path) -> None:
    target = tmp_path / "huge.txt"
    target.write_bytes(b"a" * (MAX_READ_BYTES + 1))
    with pytest.raises(WindowsFSError) as excinfo:
        edit_file(str(target), mode="append", append="x")
    assert _code(excinfo) == "TOO_LARGE"


def test_edit_file_refuses_protected_sources() -> None:
    with pytest.raises(SecurityPolicyError, match="protected"):
        edit_file("C:\\Windows\\System32\\kernel32.dll", mode="append", append="x")


def test_edit_file_write_permission_failure(tmp_path, monkeypatch) -> None:
    import windows_fs

    target = tmp_path / "cfg.txt"
    target.write_text("x=1", encoding="utf-8")

    def deny(_path, _data):
        raise PermissionError(5, "denied")

    monkeypatch.setattr(windows_fs, "_write_text", deny)
    with pytest.raises(WindowsFSError) as excinfo:
        edit_file(str(target), mode="replace", find="1", replace="2")
    assert _code(excinfo) == "ACCESS_DENIED"
    assert target.read_text(encoding="utf-8") == "x=1"


def test_edit_file_verification_catches_silent_write_failure(
    tmp_path, monkeypatch
) -> None:
    import windows_fs

    target = tmp_path / "cfg.txt"
    target.write_text("x=1", encoding="utf-8")
    monkeypatch.setattr(windows_fs, "_write_text", lambda _path, _data: None)
    with pytest.raises(WindowsFSError) as excinfo:
        edit_file(str(target), mode="replace", find="1", replace="2")
    assert _code(excinfo) == "VERIFY_FAILED"
    assert target.read_text(encoding="utf-8") == "x=1"


def test_edit_file_preserves_crlf_line_endings(tmp_path) -> None:
    target = tmp_path / "windows.txt"
    target.write_bytes(b"one\r\ntwo\r\nthree")
    edit_file(str(target), mode="replace", find="two", replace="TWO")
    assert target.read_bytes() == b"one\r\nTWO\r\nthree"


def test_edit_file_read_only_file_reports_access_denied(tmp_path) -> None:
    import stat

    target = tmp_path / "ro.txt"
    target.write_text("x=1", encoding="utf-8")
    os.chmod(target, stat.S_IREAD)
    try:
        with pytest.raises(WindowsFSError) as excinfo:
            edit_file(str(target), mode="replace", find="1", replace="2")
        assert _code(excinfo) == "ACCESS_DENIED"
    finally:
        os.chmod(target, stat.S_IWRITE)
    assert target.read_text(encoding="utf-8") == "x=1"


# ----------------------------------------------------------------------
# Part 7: folder search, folder rename, folder recycle/delete
# ----------------------------------------------------------------------
def test_search_includes_matching_folders(tmp_path) -> None:
    (tmp_path / "budget-2026").mkdir()
    (tmp_path / "budget.txt").write_text("x", encoding="utf-8")
    result = search_files(str(tmp_path), "budget")
    names = {os.path.basename(item) for item in result["results"]}
    assert names == {"budget-2026", "budget.txt"}
    assert result["count"] == 2


def test_search_folder_only_match(tmp_path) -> None:
    (tmp_path / "project-invoices").mkdir()
    result = search_files(str(tmp_path), "invoices")
    assert result["count"] == 1
    assert os.path.basename(result["results"][0]) == "project-invoices"


def test_search_non_recursive_includes_top_level_folders(tmp_path) -> None:
    (tmp_path / "target-folder").mkdir()
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "target-file.txt").write_text("x", encoding="utf-8")
    result = search_files(str(tmp_path), "target", recursive=False)
    names = {os.path.basename(item) for item in result["results"]}
    assert names == {"target-folder"}


def test_rename_folder(tmp_path) -> None:
    folder = tmp_path / "old-name"
    folder.mkdir()
    (folder / "keep.txt").write_text("keep", encoding="utf-8")
    rename_path(str(folder), "new-name")
    assert (tmp_path / "new-name").is_dir()
    assert (tmp_path / "new-name" / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not folder.exists()


def test_rename_folder_rejects_invalid_name(tmp_path) -> None:
    folder = tmp_path / "a"
    folder.mkdir()
    with pytest.raises(WindowsFSError) as excinfo:
        rename_path(str(folder), "..\\..\\evil")
    assert _code(excinfo) == "INVALID_NAME"


def test_recycle_empty_folder(tmp_path) -> None:
    folder = tmp_path / "empty-folder"
    folder.mkdir()
    result = recycle_path(str(folder))
    assert result["recycled"] is True
    assert not folder.exists()


def test_delete_non_empty_folder_permanently(tmp_path) -> None:
    folder = tmp_path / "stuff"
    folder.mkdir()
    (folder / "a.txt").write_text("x", encoding="utf-8")
    (folder / "sub").mkdir()
    (folder / "sub" / "b.txt").write_text("y", encoding="utf-8")
    result = delete_path(str(folder), recursive=True)
    assert result["method"] == "permanent"
    assert not folder.exists()


# ----------------------------------------------------------------------
# Part 7: document associations and the Office application allowlist
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        "report.docx",
        "deck.pptx",
        "book.xlsx",
        "paper.pdf",
        "notes.txt",
        "readme.md",
        "data.csv",
    ],
)
def test_open_path_allows_common_documents(tmp_path, monkeypatch, name) -> None:
    import windows_fs

    started: list[str] = []
    monkeypatch.setattr(
        windows_fs, "_startfile", lambda path: started.append(str(path))
    )
    target = tmp_path / name
    target.write_text("x", encoding="utf-8")
    result = open_path(str(target))
    assert result["opened"] is True
    assert started == [str(target.resolve())]


def test_office_candidates_come_from_fixed_install_paths() -> None:
    import windows_fs

    expectations = {
        "word": "WINWORD.EXE",
        "powerpoint": "POWERPNT.EXE",
        "excel": "EXCEL.EXE",
    }
    for key, executable in expectations.items():
        candidates = windows_fs._application_candidates(key)
        assert candidates, key
        assert any(candidate.name.upper() == executable for candidate in candidates), (
            key
        )


@pytest.mark.parametrize(
    ("alias", "executable"),
    [
        ("microsoft word", "WINWORD.EXE"),
        ("ms word", "WINWORD.EXE"),
        ("winword", "WINWORD.EXE"),
        ("microsoft powerpoint", "POWERPNT.EXE"),
        ("powerpnt", "POWERPNT.EXE"),
        ("ms powerpoint", "POWERPNT.EXE"),
        ("microsoft excel", "EXCEL.EXE"),
        ("ms excel", "EXCEL.EXE"),
    ],
)
def test_office_aliases_resolve_to_fixed_candidates(alias, executable) -> None:
    import windows_fs

    candidates = windows_fs._application_candidates(alias)
    assert any(candidate.name.upper() == executable for candidate in candidates), alias


def test_browser_candidates_come_from_fixed_install_paths() -> None:
    # Chrome and Edge are allowlisted the safe way - fixed, environment-driven
    # candidate templates the model never supplies (same as Office).
    import windows_fs

    expectations = {"chrome": "chrome.exe", "edge": "msedge.exe"}
    for key, executable in expectations.items():
        candidates = windows_fs._application_candidates(key)
        assert candidates, key
        assert any(
            candidate.name.casefold() == executable for candidate in candidates
        ), key


@pytest.mark.parametrize(
    ("alias", "executable"),
    [
        ("google chrome", "chrome.exe"),
        ("chrome browser", "chrome.exe"),
        ("microsoft edge", "msedge.exe"),
        ("ms edge", "msedge.exe"),
    ],
)
def test_browser_aliases_resolve_to_fixed_candidates(alias, executable) -> None:
    import windows_fs

    candidates = windows_fs._application_candidates(alias)
    assert any(candidate.name.casefold() == executable for candidate in candidates), (
        alias
    )


def test_unknown_app_message_lists_every_allowed_application() -> None:
    # The refusal must name the full allowlist so the assistant never
    # undersells what it can open ("only basic applications like Notepad").
    with pytest.raises(WindowsFSError) as excinfo:
        launch_application("hacker-tool")
    message = str(excinfo.value).casefold()
    for name in ("chrome", "edge", "vs code", "word", "powerpoint", "excel", "notepad"):
        assert name in message, name


def test_launch_google_chrome_is_allowlisted(monkeypatch) -> None:
    import windows_fs

    started: list[str] = []
    monkeypatch.setattr(
        windows_fs, "_startfile", lambda path: started.append(str(path))
    )
    try:
        result = launch_application("google chrome")
    except WindowsFSError as exc:
        # Not installed on this machine is fine - but it must be ALLOWED.
        assert exc.code == "APP_MISSING"
        assert started == []
    else:
        assert result["launched"] is True
        assert started
        assert str(result["resolved_path"]).casefold().endswith("chrome.exe")


def test_launch_microsoft_word_resolves_or_reports_missing(monkeypatch) -> None:
    import windows_fs

    started: list[str] = []
    monkeypatch.setattr(
        windows_fs, "_startfile", lambda path: started.append(str(path))
    )
    try:
        result = launch_application("Microsoft Word")
    except WindowsFSError as exc:
        assert exc.code == "APP_MISSING"
        assert started == []
    else:
        assert result["launched"] is True
        assert str(result["resolved_path"]).upper().endswith("WINWORD.EXE")
        assert len(started) == 1


def test_unknown_app_message_lists_office_apps() -> None:
    with pytest.raises(WindowsFSError) as excinfo:
        launch_application("hacker-tool")
    message = str(excinfo.value).casefold()
    assert "word" in message
    assert "powerpoint" in message
    assert "excel" in message


# ----------------------------------------------------------------------
# folder analysis: one-call summary for the open/inspect workflow
# ----------------------------------------------------------------------
def test_list_directory_summary_counts_kinds_sizes_and_recency(tmp_path) -> None:
    (tmp_path / "sub").mkdir()
    old = tmp_path / "old.docx"
    old.write_bytes(b"doc!")
    text_file = tmp_path / "a.txt"
    text_file.write_text("hi", encoding="utf-8")
    video = tmp_path / "b.mp4"
    video.write_bytes(b"\x00\x00\x00")
    newest = tmp_path / "c.pdf"
    newest.write_bytes(b"\x00" * 5)
    # explicit timestamps so the recency order is deterministic
    os.utime(old, (1_000_000_000, 1_000_000_000))
    os.utime(text_file, (2_000_000_000, 2_000_000_000))
    os.utime(video, (3_000_000_000, 3_000_000_000))
    os.utime(newest, (4_000_000_000, 4_000_000_000))

    summary = list_directory(str(tmp_path))["summary"]

    assert summary["files"] == 4
    assert summary["folders"] == 1
    assert summary["total_size_bytes"] == 4 + 2 + 3 + 5
    assert summary["by_extension"] == {".docx": 1, ".mp4": 1, ".pdf": 1, ".txt": 1}
    assert summary["newest_files"] == ["c.pdf", "b.mp4", "a.txt", "old.docx"]


def test_list_directory_summary_is_accurate_past_the_entry_cap(tmp_path) -> None:
    # The listing caps what it DISPLAYS, but the analysis must count ALL
    # files so the spoken summary stays accurate in a huge folder.
    total = MAX_LIST_ENTRIES + 5
    for index in range(total):
        (tmp_path / f"f{index:04d}.txt").write_text("", encoding="utf-8")
    result = list_directory(str(tmp_path))
    assert result["count"] == MAX_LIST_ENTRIES
    assert result["truncated"] is True
    assert result["summary"]["files"] == total


# ----------------------------------------------------------------------
# reliable deletion: read-only items and a clear recycle failure
# ----------------------------------------------------------------------
def test_delete_readonly_file_succeeds(tmp_path) -> None:
    # Windows refuses to unlink a read-only file outright (files copied
    # from discs, protected Office documents, locked screenshots);
    # deletion must clear the flag and still verify the removal.
    import stat

    target = tmp_path / "readonly.txt"
    target.write_text("x", encoding="utf-8")
    os.chmod(target, stat.S_IREAD)
    result = delete_path(str(target))
    assert result["deleted"] is True
    assert not target.exists()


def test_delete_folder_with_readonly_children_succeeds(tmp_path) -> None:
    import stat

    folder = tmp_path / "protected"
    (folder / "sub").mkdir(parents=True)
    ro_file = folder / "deck.pptx"
    ro_file.write_bytes(b"\x00")
    inner = folder / "sub" / "inner.pdf"
    inner.write_bytes(b"\x00")
    os.chmod(ro_file, stat.S_IREAD)
    os.chmod(inner, stat.S_IREAD)
    result = delete_path(str(folder), recursive=True)
    assert result["deleted"] is True
    assert not folder.exists()


def test_recycle_failure_names_the_recycle_bin(tmp_path, monkeypatch) -> None:
    # The model must see WHAT failed so it can explain it naturally (and
    # only offer permanent deletion if the user explicitly asks).
    import windows_fs

    target = tmp_path / "huge.mp4"
    target.write_bytes(b"\x00")

    def boom(_path):
        raise OSError("the file is too large for the Recycle Bin")

    monkeypatch.setattr(windows_fs, "_recycle", boom)
    with pytest.raises(WindowsFSError) as excinfo:
        recycle_path(str(target))
    assert _code(excinfo) == "OS_ERROR"
    assert "Recycle Bin" in str(excinfo.value)
