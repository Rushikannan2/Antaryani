"""TDD tests for the Windows tool layer (Part 2).

`WindowsTools` mirrors `BrowserTools`: `@function_tool` methods with model
facing docstrings, `ToolError` error mapping, and Part 1's
`ConfirmationManager` for staged, user-confirmed risky operations.

All filesystem activity happens in pytest temporary directories.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from livekit.agents.llm import ToolError

from windows_tools import WindowsTools

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows tool layer")

EXPECTED_TOOL_NAMES = {
    "list_directory",
    "search_files",
    "file_exists",
    "folder_exists",
    "get_file_info",
    "create_file",
    "create_folder",
    "rename_path",
    "move_path",
    "copy_path",
    "recycle_path",
    "delete_path",
    "open_path",
    "launch_application",
    "confirm_windows_action",
    "cancel_windows_action",
}


def make_context() -> SimpleNamespace:
    return SimpleNamespace(session=SimpleNamespace(current_agent=lambda: None))


async def _call(tool_name: str, **kwargs):
    tools = WindowsTools()
    context = make_context()
    method = getattr(tools, tool_name)
    return await method(context, **kwargs)


# ----------------------------------------------------------------------
# wiring
# ----------------------------------------------------------------------
def test_tools_expose_expected_names() -> None:
    tools = WindowsTools()
    assert {tool.id for tool in tools.tools} == EXPECTED_TOOL_NAMES


def test_agent_source_registers_windows_tools() -> None:
    import inspect

    import agent

    source = inspect.getsource(agent)
    assert "WindowsTools" in source
    assert "*self.windows_tools.tools" in source


def test_tool_docstrings_are_present() -> None:
    tools = WindowsTools()
    for tool in tools.tools:
        assert tool.info.description, tool.id


# ----------------------------------------------------------------------
# error mapping: WindowsFSError / SecurityPolicyError -> ToolError
# ----------------------------------------------------------------------
async def test_missing_path_maps_to_tool_error() -> None:
    with pytest.raises(ToolError) as excinfo:
        await _call("list_directory", path="C:\\this-folder-does-not-exist-xyz")
    assert "NOT_FOUND" in str(excinfo.value)


async def test_protected_path_maps_to_tool_error() -> None:
    with pytest.raises(ToolError) as excinfo:
        await _call("delete_path", path="C:\\Windows\\System32")
    assert "protected" in str(excinfo.value).casefold()


async def test_invalid_reference_maps_to_tool_error() -> None:
    with pytest.raises(ToolError) as excinfo:
        await _call("list_directory", path="   ")
    assert "INVALID_NAME" in str(excinfo.value)


# ----------------------------------------------------------------------
# read-only tools
# ----------------------------------------------------------------------
async def test_list_directory_returns_structured_entries(tmp_path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    result = await _call("list_directory", path=str(tmp_path))
    assert result["count"] == 2
    assert result["path"] == str(tmp_path.resolve())


async def test_file_and_folder_exists_tools(tmp_path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    assert (await _call("file_exists", path=str(target)))["exists"] is True
    assert (await _call("folder_exists", path=str(tmp_path)))["exists"] is True
    assert (await _call("file_exists", path=str(tmp_path / "ghost")))["exists"] is False


async def test_search_files_tool(tmp_path) -> None:
    (tmp_path / "My Report.pdf").write_text("x", encoding="utf-8")
    result = await _call("search_files", root=str(tmp_path), pattern="report")
    assert result["count"] == 1


async def test_get_file_info_tool(tmp_path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("abc", encoding="utf-8")
    info = await _call("get_file_info", path=str(target))
    assert info["size_bytes"] == 3
    assert info["extension"] == ".txt"


async def test_alias_reference_lists_real_desktop() -> None:
    result = await _call("list_directory", path="desktop")
    # real Desktop must exist on Windows; entries are whatever is there
    assert result["path"].casefold().endswith("desktop")
    assert "count" in result


# ----------------------------------------------------------------------
# create / rename / move / copy through the tools
# ----------------------------------------------------------------------
async def test_create_file_and_context_tracking(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    target = tmp_path / "project" / "todo.txt"
    await tools.create_file(context, path=str(target), content="buy milk")
    assert target.read_text(encoding="utf-8") == "buy milk"
    # context now points at the file's parent for relative follow-ups
    follow_up = await tools.get_file_info(context, path="todo.txt")
    assert follow_up["name"] == "todo.txt"


async def test_create_file_conflict_surfaces_as_tool_error(tmp_path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("keep", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        await _call("create_file", path=str(target), content="clobber")
    assert "ALREADY_EXISTS" in str(excinfo.value)
    assert target.read_text(encoding="utf-8") == "keep"


async def test_create_file_overwrite_requires_confirmation(tmp_path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        await _call("create_file", path=str(target), content="new", overwrite=True)
    message = str(excinfo.value)
    assert "token" in message
    assert target.read_text(encoding="utf-8") == "old"  # nothing changed


async def test_rename_with_this_file_context(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    source = tmp_path / "draft-name.txt"
    await tools.create_file(  # creates the file and seeds "last file"
        context, path=str(source), content="x"
    )
    result = await tools.rename_path(
        context, source="this file", new_name="final-name.txt"
    )
    assert (tmp_path / "final-name.txt").exists()
    assert not source.exists()
    assert result["to"].endswith("final-name.txt")


async def test_rename_rejects_traversal_in_new_name(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("x", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        await _call(
            "rename_path",
            source=str(source),
            new_name="..\\..\\Windows\\evil.txt",
        )
    assert "INVALID_NAME" in str(excinfo.value)


async def test_move_tool(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    source = tmp_path / "a.txt"
    source.write_text("x", encoding="utf-8")
    destination = tmp_path / "inbox" / "a.txt"

    # moves are MODERATE risk: the first attempt stages, it does not execute
    with pytest.raises(ToolError) as excinfo:
        await tools.move_path(context, source=str(source), destination=str(destination))
    token = str(excinfo.value).split("'")[1]
    code = tools._manager.pending()[-1].code
    assert not destination.exists()

    await tools.confirm_windows_action(
        context,
        token=token,
        operation="move_path",
        code=code,
        source=str(source),
        destination=str(destination),
    )
    result = await tools.move_path(
        context, source=str(source), destination=str(destination)
    )
    assert destination.exists()
    assert not source.exists()
    assert result["to"].endswith("a.txt")


async def test_move_conflict_surfaces_as_tool_error(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    target = tmp_path / "b.txt"
    target.write_text("b", encoding="utf-8")
    # conflict preflight runs BEFORE staging, so the user is never asked to
    # confirm a move that could not succeed anyway
    with pytest.raises(ToolError) as excinfo:
        await _call("move_path", source=str(source), destination=str(target))
    assert "CONFLICT" in str(excinfo.value)
    assert "token" not in str(excinfo.value)


async def test_copy_tool_does_not_consume(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("data", encoding="utf-8")
    destination = tmp_path / "b.txt"
    await _call("copy_path", source=str(source), destination=str(destination))
    assert source.exists()
    assert destination.read_text(encoding="utf-8") == "data"


# ----------------------------------------------------------------------
# confirmation flow: stage -> user confirms -> retry executes
# ----------------------------------------------------------------------
async def test_delete_stages_instead_of_executing(tmp_path) -> None:
    target = tmp_path / "victim.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        await _call("delete_path", path=str(target))
    message = str(excinfo.value)
    assert "token" in message
    assert target.exists()  # nothing was deleted


async def test_confirm_then_delete_executes(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    target = tmp_path / "victim.txt"
    target.write_text("x", encoding="utf-8")

    # 1) first attempt stages a pending confirmation
    with pytest.raises(ToolError) as excinfo:
        await tools.delete_path(context, path=str(target))
    message = str(excinfo.value)
    # pull the single-use token out of the tool error message
    token = message.split("'")[1]
    code = tools._manager.pending()[-1].code

    # 2) the confirm tool records the user's approval
    confirmation = await tools.confirm_windows_action(
        context,
        token=token,
        operation="delete_path",
        code=code,
        source=str(target),
        destination="",
    )
    assert "confirmed" in confirmation.casefold()

    # 3) the retried delete now executes
    result = await tools.delete_path(context, path=str(target))
    assert result["deleted"] is True
    assert not target.exists()


async def test_confirm_requires_matching_payload(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    target = tmp_path / "victim.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(ToolError) as excinfo:
        await tools.delete_path(context, path=str(target))
    token = str(excinfo.value).split("'")[1]
    code = tools._manager.pending()[-1].code

    with pytest.raises(ToolError) as excinfo:
        await tools.confirm_windows_action(
            context,
            token=token,
            operation="delete_path",
            code=code,
            source=str(tmp_path / "different.txt"),
            destination="",
        )
    assert target.exists()
    # a single-use token cannot be replayed with the right payload either
    await tools.confirm_windows_action(
        context,
        token=token,
        operation="delete_path",
        code=code,
        source=str(target),
        destination="",
    )
    with pytest.raises(ToolError):
        await tools.confirm_windows_action(
            context,
            token=token,
            operation="delete_path",
            code=code,
            source=str(target),
            destination="",
        )


async def test_delete_protection_is_not_confirmable(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    with pytest.raises(ToolError) as excinfo:
        await tools.delete_path(context, path="C:\\Windows\\System32")
    assert "protected" in str(excinfo.value).casefold()
    assert "token" not in str(excinfo.value)  # no stage is ever offered


async def test_recycle_requires_confirmation_then_works(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    target = tmp_path / "old.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(ToolError) as excinfo:
        await tools.recycle_path(context, path=str(target))
    token = str(excinfo.value).split("'")[1]
    code = tools._manager.pending()[-1].code
    assert target.exists()

    await tools.confirm_windows_action(
        context,
        token=token,
        operation="recycle_path",
        code=code,
        source=str(target),
        destination="",
    )
    result = await tools.recycle_path(context, path=str(target))
    assert result["recycled"] is True
    assert not target.exists()


# ----------------------------------------------------------------------
# bulk escalation (>= BULK_CONFIRM_THRESHOLD items forces confirmation)
# ----------------------------------------------------------------------
async def test_bulk_folder_copy_requires_confirmation(tmp_path) -> None:
    from windows_security import BULK_ESCALATION_THRESHOLD

    source = tmp_path / "big-tree"
    source.mkdir()
    for index in range(BULK_ESCALATION_THRESHOLD + 1):
        (source / f"file{index:03d}.txt").write_text("", encoding="utf-8")
    destination = tmp_path / "big-tree-copy"

    with pytest.raises(ToolError) as excinfo:
        await _call("copy_path", source=str(source), destination=str(destination))
    message = str(excinfo.value)
    assert "token" in message
    assert not destination.exists()  # nothing copied yet

    # a small copy (below threshold) needs no confirmation
    small = tmp_path / "small"
    small.mkdir()
    (small / "one.txt").write_text("x", encoding="utf-8")
    await _call(
        "copy_path",
        source=str(small),
        destination=str(tmp_path / "small-copy"),
    )
    assert (tmp_path / "small-copy" / "one.txt").exists()


async def test_bulk_over_maximum_is_rejected(tmp_path, monkeypatch) -> None:
    import windows_security

    # shrink the cap so the test stays fast; boundary math is unit-tested
    # in Part 1
    monkeypatch.setattr(windows_security, "MAX_BULK_ITEMS", 5)
    source = tmp_path / "huge"
    source.mkdir()
    for index in range(8):
        (source / f"f{index}.txt").write_text("", encoding="utf-8")

    with pytest.raises(ToolError) as excinfo:
        await _call(
            "copy_path",
            source=str(source),
            destination=str(tmp_path / "huge-copy"),
        )
    message = str(excinfo.value).casefold()
    assert "refusing" in message or "maximum" in message
    assert not (tmp_path / "huge-copy").exists()


# ----------------------------------------------------------------------
# open_path / launch_application
# ----------------------------------------------------------------------
async def test_open_path_uses_associations(tmp_path, monkeypatch) -> None:
    import windows_fs

    started: list[str] = []
    monkeypatch.setattr(
        windows_fs, "_startfile", lambda path: started.append(str(path))
    )
    target = tmp_path / "notes.txt"
    target.write_text("x", encoding="utf-8")
    result = await _call("open_path", path=str(target))
    assert result["opened"] is True
    assert len(started) == 1


async def test_open_path_blocks_executables(tmp_path) -> None:
    target = tmp_path / "sneaky.exe"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        await _call("open_path", path=str(target))
    assert "BLOCKED" in str(excinfo.value)
    assert "launch_application" in str(excinfo.value)


async def test_launch_application_allowlist(monkeypatch) -> None:
    import windows_fs

    started: list[str] = []
    monkeypatch.setattr(
        windows_fs, "_startfile", lambda path: started.append(str(path))
    )
    result = await _call("launch_application", application="calculator")
    assert result["launched"] is True
    assert result["resolved_path"].casefold().endswith("calc.exe")

    with pytest.raises(ToolError) as excinfo:
        await _call("launch_application", application="sneaky-tool")
    assert "UNKNOWN_APP" in str(excinfo.value)

    with pytest.raises(ToolError) as excinfo:
        await _call(
            "launch_application",
            application="C:\\Users\\a\\evil.exe",
        )
    assert "UNKNOWN_APP" in str(excinfo.value)


# ----------------------------------------------------------------------
# Part 4: junction writes are refused end to end (tool layer)
# ----------------------------------------------------------------------
async def test_create_file_through_junction_is_refused(tmp_path) -> None:
    import _winapi
    import os

    link = tmp_path / "innocent"
    _winapi.CreateJunction("C:\\Windows", str(link))
    tools = WindowsTools()
    context = make_context()
    with pytest.raises(ToolError) as excinfo:
        await tools.create_file(context, path=str(link / "part4.txt"), content="x")
    assert "protected" in str(excinfo.value).casefold()
    assert not os.path.exists(r"C:\Windows\part4.txt")


# ----------------------------------------------------------------------
# Part 3: short-term context, ambiguity, cancellation
# ----------------------------------------------------------------------
async def test_it_reference_after_creating_a_file(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    target = tmp_path / "fresh.txt"
    await tools.create_file(context, path=str(target), content="x")
    info = await tools.get_file_info(context, path="it")
    assert info["name"] == "fresh.txt"


async def test_previous_file_reference_renames(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    target = tmp_path / "draft.txt"
    await tools.create_file(context, path=str(target), content="x")
    result = await tools.rename_path(
        context, source="previous file", new_name="final.txt"
    )
    assert (tmp_path / "final.txt").exists()
    assert result["to"].endswith("final.txt")


async def test_single_search_hit_seeds_it_context(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    target = tmp_path / "invoice-7788.pdf"
    target.write_text("x", encoding="utf-8")
    found = await tools.search_files(
        context, root=str(tmp_path), pattern="invoice-7788"
    )
    assert found["count"] == 1
    info = await tools.get_file_info(context, path="it")
    assert info["name"] == "invoice-7788.pdf"


async def test_pronoun_without_context_asks_instead_of_guessing() -> None:
    tools = WindowsTools()
    context = make_context()
    with pytest.raises(ToolError) as excinfo:
        await tools.get_file_info(context, path="it")
    message = str(excinfo.value)
    assert "INVALID_NAME" in message
    assert "current item" in message  # the model turns this into one question


async def test_cancel_withdraws_a_staged_action(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    target = tmp_path / "victim.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(ToolError) as excinfo:
        await tools.delete_path(context, path=str(target))
    token = str(excinfo.value).split("'")[1]
    code = tools._manager.pending()[-1].code

    outcome = await tools.cancel_windows_action(context, token=token)
    assert "cancel" in outcome.casefold()

    # the cancelled token can no longer confirm anything
    with pytest.raises(ToolError) as excinfo:
        await tools.confirm_windows_action(
            context,
            token=token,
            operation="delete_path",
            code=code,
            source=str(target),
            destination="",
        )
    assert "token" in str(excinfo.value).casefold()
    assert target.exists()

    # a fresh attempt stages a NEW confirmation (nothing auto-executes)
    with pytest.raises(ToolError) as excinfo:
        await tools.delete_path(context, path=str(target))
    new_token = str(excinfo.value).split("'")[1]
    assert new_token != token
    assert target.exists()


async def test_cancel_clears_an_approval_before_it_runs(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    target = tmp_path / "victim.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(ToolError) as excinfo:
        await tools.delete_path(context, path=str(target))
    token = str(excinfo.value).split("'")[1]
    code = tools._manager.pending()[-1].code
    await tools.confirm_windows_action(
        context,
        token=token,
        operation="delete_path",
        code=code,
        source=str(target),
        destination="",
    )
    # user changes their mind between confirmation and execution
    await tools.cancel_windows_action(context, token="")
    # approval was withdrawn: the retry stages again instead of executing
    with pytest.raises(ToolError) as excinfo:
        await tools.delete_path(context, path=str(target))
    assert "token" in str(excinfo.value)
    assert target.exists()


async def test_cancel_with_nothing_staged_is_friendly() -> None:
    tools = WindowsTools()
    context = make_context()
    outcome = await tools.cancel_windows_action(context, token="")
    assert "nothing" in outcome.casefold()


# ----------------------------------------------------------------------
# Part 6: the code never reaches the model (out-of-band only)
# ----------------------------------------------------------------------
async def test_staged_message_hides_the_code(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    target = tmp_path / "victim.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        await tools.delete_path(context, path=str(target))
    message = str(excinfo.value)
    token = message.split("'")[1]
    code = tools._manager.pending()[-1].code

    assert message.startswith("Staged for user confirmation: token '")
    assert len(token) == 22, f"first quoted span must be the token: {token!r}"
    assert "confirm_windows_action" in message
    assert "six-digit confirmation code" in message
    assert "read back" in message
    assert code not in message  # the model never sees the code


async def test_confirmation_code_is_published_out_of_band(tmp_path) -> None:
    sent: list[dict] = []
    tools = WindowsTools(confirmation_publisher=sent.append)
    context = make_context()
    target = tmp_path / "victim.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        await tools.delete_path(context, path=str(target))
    message = str(excinfo.value)
    token = message.split("'")[1]
    code = tools._manager.pending()[-1].code

    assert len(sent) == 1
    payload = sent[0]
    assert payload["type"] == "confirmation"
    assert payload["token"] == token
    assert payload["code"] == code
    assert payload["operation"] == "delete_path"
    assert payload["description"]
    assert code not in message


async def test_wrong_code_is_refused_and_staging_survives(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    target = tmp_path / "victim.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        await tools.delete_path(context, path=str(target))
    token = str(excinfo.value).split("'")[1]
    code = tools._manager.pending()[-1].code
    wrong = "999999" if code != "999999" else "999998"

    with pytest.raises(ToolError, match="confirmation code"):
        await tools.confirm_windows_action(
            context,
            token=token,
            operation="delete_path",
            code=wrong,
            source=str(target),
            destination="",
        )
    assert target.exists()  # the real user can still confirm

    await tools.confirm_windows_action(
        context,
        token=token,
        operation="delete_path",
        code=code,
        source=str(target),
        destination="",
    )
    result = await tools.delete_path(context, path=str(target))
    assert result["deleted"] is True


async def test_three_wrong_codes_withdraw_the_staging(tmp_path) -> None:
    from confirmation import MAX_CODE_ATTEMPTS

    tools = WindowsTools()
    context = make_context()
    target = tmp_path / "victim.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        await tools.delete_path(context, path=str(target))
    token = str(excinfo.value).split("'")[1]
    code = tools._manager.pending()[-1].code
    wrong = "999999" if code != "999999" else "999998"

    for attempt in range(MAX_CODE_ATTEMPTS):
        expected = "withdrawn" if attempt == MAX_CODE_ATTEMPTS - 1 else "confirmation"
        with pytest.raises(ToolError, match=expected):
            await tools.confirm_windows_action(
                context,
                token=token,
                operation="delete_path",
                code=wrong,
                source=str(target),
                destination="",
            )
    assert target.exists()
    # even the real code is refused now: the staging is gone
    with pytest.raises(ToolError, match="token"):
        await tools.confirm_windows_action(
            context,
            token=token,
            operation="delete_path",
            code=code,
            source=str(target),
            destination="",
        )
    assert target.exists()


async def test_confirm_windows_action_requires_a_code(tmp_path) -> None:
    tools = WindowsTools()
    context = make_context()
    target = tmp_path / "victim.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        await tools.delete_path(context, path=str(target))
    token = str(excinfo.value).split("'")[1]

    with pytest.raises(TypeError):
        await tools.confirm_windows_action(
            context,
            token=token,
            operation="delete_path",
            source=str(target),
            destination="",
        )
    assert target.exists()
