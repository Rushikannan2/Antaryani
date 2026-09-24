"""Security-policy tests for Srilatha's future Windows capability (Part 1).

These tests define the security boundary BEFORE any Windows tool exists:

* the prompt is NOT the boundary - these rules are enforced in Python;
* protected system locations cannot be consumed or written to;
* every operation is classified by risk (default deny for unknown names);
* destructive operations need a staged, single-use, exact-match confirmation
  that only a real user interaction can satisfy;
* no shell execution may ever appear in the tool surface.

Windows-only: the policy uses native Windows path semantics.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from confirmation import MAX_CODE_ATTEMPTS
from windows_security import (
    BULK_ESCALATION_THRESHOLD,
    MAX_BULK_ITEMS,
    PROTECTED_FOLDER_NAMES,
    ConfirmationManager,
    OperationRisk,
    SecurityPolicyError,
    classify_operation,
    is_permitted,
    is_protected_path,
    requires_confirmation,
    validate_bulk_operation,
    validate_destination,
    validate_path,
    validate_source,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows filesystem policy"
)

_SRC = Path(__file__).resolve().parents[1] / "src"


# ----------------------------------------------------------------------
# structural path validation
# ----------------------------------------------------------------------
def test_validate_path_returns_resolved_absolute_path(tmp_path) -> None:
    result = validate_path(tmp_path / "sub" / ".." / "file.txt")
    assert result == (tmp_path / "file.txt").resolve()


def test_validate_path_accepts_str_input(tmp_path) -> None:
    assert validate_path(str(tmp_path)) == tmp_path.resolve()


def test_validate_path_rejects_empty() -> None:
    with pytest.raises(SecurityPolicyError, match="cannot be empty"):
        validate_path("   ")


def test_validate_path_rejects_control_characters() -> None:
    with pytest.raises(SecurityPolicyError, match="control characters"):
        validate_path("C:\\Users\\rushi\\ok\x00evil.txt")


def test_validate_path_rejects_wildcards() -> None:
    with pytest.raises(SecurityPolicyError, match="wildcard"):
        validate_path("C:\\Users\\rushi\\*.txt")


def test_validate_path_rejects_relative_path() -> None:
    with pytest.raises(SecurityPolicyError, match="absolute"):
        validate_path("relative\\path.txt")


def test_validate_path_rejects_drive_relative_path() -> None:
    with pytest.raises(SecurityPolicyError, match="absolute"):
        validate_path("C:file.txt")


def test_validate_path_rejects_unc_path() -> None:
    with pytest.raises(SecurityPolicyError, match="not permitted"):
        validate_path("\\\\server\\share\\file.txt")


def test_validate_path_rejects_device_namespace_path() -> None:
    with pytest.raises(SecurityPolicyError, match="not permitted"):
        validate_path("\\\\.\\C:\\Windows\\System32")
    with pytest.raises(SecurityPolicyError, match="not permitted"):
        validate_path("\\\\?\\C:\\Windows")


def test_validate_path_rejects_reserved_device_name() -> None:
    with pytest.raises(SecurityPolicyError, match="reserved device name"):
        validate_path("C:\\Users\\rushi\\NUL")
    with pytest.raises(SecurityPolicyError, match="reserved device name"):
        validate_path("C:\\temp\\aux.log")


def test_validate_path_rejects_alternate_data_stream() -> None:
    with pytest.raises(SecurityPolicyError, match="Alternate data streams"):
        validate_path("C:\\Users\\rushi\\notes.txt:hidden")


def test_validate_path_rejects_empty_component_after_normalization() -> None:
    with pytest.raises(SecurityPolicyError, match="component"):
        validate_path("C:\\temp\\...")


def test_validate_path_must_exist_true(tmp_path) -> None:
    with pytest.raises(SecurityPolicyError, match="does not exist"):
        validate_path(tmp_path / "missing.txt", must_exist=True)
    existing = tmp_path / "here.txt"
    existing.write_text("x", encoding="utf-8")
    assert validate_path(existing, must_exist=True) == existing.resolve()


def test_validate_path_must_exist_false(tmp_path) -> None:
    existing = tmp_path / "here.txt"
    existing.write_text("x", encoding="utf-8")
    with pytest.raises(SecurityPolicyError, match="already exists"):
        validate_path(existing, must_exist=False)
    assert validate_path(tmp_path / "new.txt", must_exist=False)


def test_validate_path_strips_trailing_dots_and_spaces(tmp_path) -> None:
    # Windows silently drops trailing dots/spaces; policy must see through it.
    assert validate_path(f"{tmp_path}\\folder.") == (tmp_path / "folder").resolve()
    assert validate_path(f"{tmp_path}\\name.txt  ") == (tmp_path / "name.txt").resolve()


# ----------------------------------------------------------------------
# protected locations
# ----------------------------------------------------------------------
def test_spec_required_protected_folders_present() -> None:
    protected = {name.casefold() for name in PROTECTED_FOLDER_NAMES}
    for required in (
        "windows",
        "system32",
        "program files",
        "program files (x86)",
        "programdata",
        "recovery",
    ):
        if required == "system32":
            # lives inside C:\Windows, so it is covered by the "windows" root
            assert "windows" in protected
        else:
            assert required in protected


def test_windows_tree_is_protected() -> None:
    assert is_protected_path("C:\\Windows")
    assert is_protected_path("C:\\Windows\\System32")
    assert is_protected_path("c:\\windows\\temp\\evil.dll")


def test_system_directories_are_protected() -> None:
    for path in (
        "C:\\Program Files",
        "C:\\Program Files (x86)\\Steam",
        "C:\\ProgramData",
        "C:\\Recovery",
    ):
        assert is_protected_path(path), path


def test_protection_is_case_insensitive() -> None:
    assert is_protected_path("C:\\PrOgRaM fIlEs\\AnyApp")


def test_protection_blocks_traversal_bypass() -> None:
    assert is_protected_path("C:\\Users\\..\\Windows\\System32")
    assert is_protected_path("C:\\Windows\\..\\Windows")


def test_protection_blocks_trailing_dot_bypass() -> None:
    assert is_protected_path("C:\\Windows.")
    assert is_protected_path("C:\\Program Files.\\Suspicious")


def test_protection_is_drive_agnostic() -> None:
    assert is_protected_path("D:\\Windows\\System32")
    assert is_protected_path("E:\\ProgramData")


def test_drive_roots_themselves_are_protected() -> None:
    assert is_protected_path("C:\\")
    assert is_protected_path("D:\\")


# ----------------------------------------------------------------------
# symlink / junction bypass (resolution must happen BEFORE classification)
# ----------------------------------------------------------------------
def _make_junction(link: Path, target: str) -> Path:
    """Create a directory junction (works without admin rights)."""
    import _winapi

    _winapi.CreateJunction(target, str(link))
    return link


def test_junction_write_into_protected_is_rejected(tmp_path) -> None:
    # A junction in a harmless folder pointing at C:\Windows must not let
    # a write "through" it look like a benign destination.
    link = _make_junction(tmp_path / "innocent", "C:\\Windows")
    with pytest.raises(SecurityPolicyError, match="protected"):
        validate_destination(str(link / "evil.txt"), operation="create_file")


def test_junction_consume_of_protected_is_rejected(tmp_path) -> None:
    link = _make_junction(tmp_path / "innocent", "C:\\Windows")
    with pytest.raises(SecurityPolicyError, match="protected"):
        validate_source(str(link), operation="delete_path")


def test_protection_follows_junction(tmp_path) -> None:
    link = _make_junction(tmp_path / "innocent", "C:\\Windows\\System32")
    assert is_protected_path(str(link / "evil.dll"))


def test_symlink_write_into_protected_is_rejected(tmp_path) -> None:
    link = tmp_path / "sneaky"
    try:
        os.symlink("C:\\Windows", str(link), target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable without developer mode: {exc}")
    with pytest.raises(SecurityPolicyError, match="protected"):
        validate_destination(str(link / "evil.txt"), operation="create_file")


def test_user_accessible_locations_are_not_protected() -> None:
    # Desktop/Documents/Downloads and other drives must stay usable.
    for path in (
        "C:\\Users",
        "C:\\Users\\rushi\\Desktop",
        "C:\\Users\\rushi\\Documents\\report.docx",
        "D:\\Projects\\website\\index.html",
        "E:\\Media\\song.mp3",
        "D:\\file.txt",
    ):
        assert not is_protected_path(path), path


# ----------------------------------------------------------------------
# operation classification
# ----------------------------------------------------------------------
def test_read_operations_classified_read() -> None:
    for operation in (
        "list_directory",
        "search_files",
        "file_exists",
        "folder_exists",
        "get_file_info",
        "inspect_tree",
        "read_file",
    ):
        assert classify_operation(operation) is OperationRisk.READ


def test_low_risk_operations_classified_low_risk() -> None:
    for operation in (
        "create_file",
        "create_folder",
        "copy_path",
        "rename_path",
        "open_path",
        "launch_application",
    ):
        assert classify_operation(operation) is OperationRisk.LOW_RISK


def test_moderate_operations_classified_moderate() -> None:
    for operation in (
        "edit_file",
        "move_path",
        "move_folder",
        "bulk_copy",
        "bulk_rename",
        "bulk_move",
    ):
        assert classify_operation(operation) is OperationRisk.MODERATE


def test_destructive_operations_classified_destructive() -> None:
    for operation in (
        "delete_path",
        "recycle_path",
        "recursive_delete",
        "bulk_delete",
        "permanent_delete",
    ):
        assert classify_operation(operation) is OperationRisk.DESTRUCTIVE


def test_critical_operations_are_never_permitted() -> None:
    for operation in (
        "format_drive",
        "modify_firewall",
        "modify_boot_configuration",
        "modify_security_settings",
    ):
        assert classify_operation(operation) is OperationRisk.CRITICAL
        assert not is_permitted(OperationRisk.CRITICAL)


def test_unknown_operation_defaults_to_critical() -> None:
    # Default deny: a name the policy does not know is treated as critical.
    assert classify_operation("frobnicate_everything") is OperationRisk.CRITICAL


def test_classification_is_case_and_whitespace_insensitive() -> None:
    assert classify_operation("  Delete_Path ") is OperationRisk.DESTRUCTIVE


def test_empty_operation_name_is_rejected() -> None:
    with pytest.raises(SecurityPolicyError, match="non-empty"):
        classify_operation("   ")


def test_requires_confirmation_matrix() -> None:
    assert requires_confirmation("list_directory") is False
    assert requires_confirmation("read_file") is False
    assert requires_confirmation("inspect_tree") is False
    assert requires_confirmation("create_file") is False
    assert requires_confirmation("edit_file") is True
    assert requires_confirmation("move_path") is True
    assert requires_confirmation("delete_path") is True
    assert requires_confirmation("format_drive") is True


def test_is_permitted_allows_everything_except_critical() -> None:
    for risk in OperationRisk:
        assert is_permitted(risk) is (risk is not OperationRisk.CRITICAL)


# ----------------------------------------------------------------------
# source validation
# ----------------------------------------------------------------------
def test_source_must_exist(tmp_path) -> None:
    with pytest.raises(SecurityPolicyError, match="does not exist"):
        validate_source(tmp_path / "ghost.txt", operation="list_directory")


def test_reading_from_protected_location_is_allowed() -> None:
    # Observation is safe: listing C:\Windows must not be blocked...
    result = validate_source("C:\\Windows", operation="list_directory")
    assert is_protected_path(result)


def test_copy_source_out_of_system_directory_is_allowed() -> None:
    # A copy does not consume its source, so reading from a protected tree
    # into a user location stays possible (the destination is checked instead).
    result = validate_source("C:\\Windows\\notepad.exe", operation="copy_path")
    assert result.parts[1].casefold() == "windows"


def test_deleting_protected_source_is_rejected() -> None:
    with pytest.raises(SecurityPolicyError, match="protected"):
        validate_source("C:\\Windows", operation="delete_path")


def test_moving_protected_source_is_rejected() -> None:
    with pytest.raises(SecurityPolicyError, match="protected"):
        validate_source("C:\\Program Files\\SomeApp", operation="move_path")


def test_source_for_unknown_operation_is_rejected(tmp_path) -> None:
    existing = tmp_path / "a.txt"
    existing.write_text("x", encoding="utf-8")
    with pytest.raises(SecurityPolicyError, match="critical"):
        validate_source(existing, operation="frobnicate")


# ----------------------------------------------------------------------
# destination validation
# ----------------------------------------------------------------------
def test_destination_inside_protected_location_is_rejected() -> None:
    with pytest.raises(SecurityPolicyError, match="protected"):
        validate_destination("C:\\Windows\\evil.txt", operation="create_file")
    with pytest.raises(SecurityPolicyError, match="protected"):
        validate_destination("C:\\Program Files\\NewApp", operation="create_folder")


def test_destination_cannot_be_a_drive_root() -> None:
    with pytest.raises(SecurityPolicyError, match="protected"):
        validate_destination("D:\\", operation="create_folder")


def test_destination_traversal_into_protected_is_rejected() -> None:
    with pytest.raises(SecurityPolicyError, match="protected"):
        validate_destination("C:\\Users\\..\\Windows\\evil.dll", operation="copy_path")


def test_new_destination_may_not_exist(tmp_path) -> None:
    result = validate_destination(
        tmp_path / "nested" / "new.txt", operation="create_file"
    )
    assert result == (tmp_path / "nested" / "new.txt").resolve()


def test_identical_source_and_destination_is_rejected(tmp_path) -> None:
    existing = tmp_path / "a.txt"
    existing.write_text("x", encoding="utf-8")
    with pytest.raises(SecurityPolicyError, match="identical"):
        validate_destination(existing, operation="move_path", source=existing)


def test_file_onto_file_conflict_is_rejected(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    destination = tmp_path / "b.txt"
    destination.write_text("b", encoding="utf-8")
    with pytest.raises(SecurityPolicyError, match="already exists"):
        validate_destination(destination, operation="copy_path", source=source)


def test_moving_into_existing_directory_is_allowed(tmp_path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    result = validate_destination(tmp_path, operation="move_path", source=source)
    assert result == tmp_path.resolve()


# ----------------------------------------------------------------------
# bulk operations
# ----------------------------------------------------------------------
def test_bulk_below_threshold_is_not_escalated() -> None:
    assert BULK_ESCALATION_THRESHOLD > 2
    assert (
        validate_bulk_operation(BULK_ESCALATION_THRESHOLD - 1, "copy_path")
        is OperationRisk.LOW_RISK
    )


def test_bulk_at_threshold_escalates_risk() -> None:
    assert (
        validate_bulk_operation(BULK_ESCALATION_THRESHOLD, "copy_path")
        is OperationRisk.MODERATE
    )
    assert (
        validate_bulk_operation(BULK_ESCALATION_THRESHOLD, "create_file")
        is OperationRisk.MODERATE
    )
    assert (
        validate_bulk_operation(BULK_ESCALATION_THRESHOLD, "move_path")
        is OperationRisk.DESTRUCTIVE
    )


def test_bulk_destructive_stays_destructive() -> None:
    assert (
        validate_bulk_operation(BULK_ESCALATION_THRESHOLD, "delete_path")
        is OperationRisk.DESTRUCTIVE
    )


def test_bulk_reads_are_not_escalated() -> None:
    assert (
        validate_bulk_operation(BULK_ESCALATION_THRESHOLD, "list_directory")
        is OperationRisk.READ
    )


def test_bulk_over_maximum_is_rejected() -> None:
    with pytest.raises(SecurityPolicyError, match="maximum"):
        validate_bulk_operation(MAX_BULK_ITEMS + 1, "copy_path")


def test_bulk_non_positive_count_is_rejected() -> None:
    with pytest.raises(SecurityPolicyError, match="at least 1"):
        validate_bulk_operation(0, "delete_path")
    with pytest.raises(SecurityPolicyError, match="at least 1"):
        validate_bulk_operation(-5, "delete_path")


def test_bulk_unknown_operation_is_rejected() -> None:
    with pytest.raises(SecurityPolicyError, match="critical"):
        validate_bulk_operation(10, "frobnicate")


# ----------------------------------------------------------------------
# confirmation architecture
# ----------------------------------------------------------------------
def test_stage_rejects_operations_that_never_need_confirmation() -> None:
    manager = ConfirmationManager()
    with pytest.raises(SecurityPolicyError, match="does not require"):
        manager.stage(operation="copy_path", description="copy a file")


def test_stage_rejects_critical_operation() -> None:
    manager = ConfirmationManager()
    with pytest.raises(SecurityPolicyError, match="critical"):
        manager.stage(operation="format_drive", description="format D:")
    with pytest.raises(SecurityPolicyError, match="critical"):
        manager.stage(operation="frobnicate", description="???")


def test_stage_rejects_protected_source(tmp_path) -> None:
    # Even a confirmed delete of a system location must be impossible.
    manager = ConfirmationManager()
    with pytest.raises(SecurityPolicyError, match="protected"):
        manager.stage(
            operation="delete_path",
            description="delete the Windows folder",
            source="C:\\Windows",
        )


def test_stage_rejects_protected_destination(tmp_path) -> None:
    manager = ConfirmationManager()
    source = tmp_path / "a.txt"
    source.write_text("x", encoding="utf-8")
    with pytest.raises(SecurityPolicyError, match="protected"):
        manager.stage(
            operation="move_path",
            description="move into Windows",
            source=source,
            destination="C:\\Windows\\a.txt",
        )


def test_stage_requires_a_description(tmp_path) -> None:
    manager = ConfirmationManager()
    source = tmp_path / "a.txt"
    source.write_text("x", encoding="utf-8")
    with pytest.raises(SecurityPolicyError, match="description"):
        manager.stage(operation="delete_path", description="   ", source=source)


def test_stage_returns_pending_record(tmp_path) -> None:
    manager = ConfirmationManager()
    source = tmp_path / "a.txt"
    source.write_text("x", encoding="utf-8")

    pending = manager.stage(
        operation="delete_path",
        description="delete the file a.txt",
        source=source,
    )

    assert isinstance(pending.token, str) and len(pending.token) >= 16
    assert pending.operation == "delete_path"
    assert pending.risk is OperationRisk.DESTRUCTIVE
    assert pending.source == str(source.resolve())
    assert pending.expires_at > pending.created_at
    assert manager.pending() == (pending,)


def test_stage_tokens_are_unique(tmp_path) -> None:
    manager = ConfirmationManager()
    first = manager.stage(operation="delete_path", description="d", source=tmp_path)
    second = manager.stage(operation="delete_path", description="d", source=tmp_path)
    assert first.token != second.token


def test_confirm_requires_a_known_token(tmp_path) -> None:
    manager = ConfirmationManager()
    with pytest.raises(SecurityPolicyError, match="pending confirmation"):
        manager.confirm(
            "not-a-real-token",
            operation="delete_path",
            code="000000",
            source=tmp_path,
        )


def test_confirm_rejects_changed_operation(tmp_path) -> None:
    manager = ConfirmationManager()
    pending = manager.stage(
        operation="delete_path", description="delete it", source=tmp_path
    )
    with pytest.raises(SecurityPolicyError, match="does not match"):
        manager.confirm(
            pending.token,
            operation="move_path",
            code=pending.code,
            source=tmp_path,
        )
    assert manager.pending() == (pending,)  # staging survives the failure


def test_confirm_rejects_changed_source(tmp_path) -> None:
    manager = ConfirmationManager()
    approved = tmp_path / "approved.txt"
    approved.write_text("x", encoding="utf-8")
    sneaky = tmp_path / "sneaky.txt"
    sneaky.write_text("x", encoding="utf-8")

    pending = manager.stage(
        operation="delete_path",
        description="delete approved.txt",
        source=approved,
    )
    with pytest.raises(SecurityPolicyError, match="does not match"):
        manager.confirm(
            pending.token,
            operation="delete_path",
            code=pending.code,
            source=sneaky,
        )
    assert manager.pending() == (pending,)


def test_confirm_rejects_changed_destination(tmp_path) -> None:
    manager = ConfirmationManager()
    source = tmp_path / "a.txt"
    source.write_text("x", encoding="utf-8")
    pending = manager.stage(
        operation="move_path",
        description="move a.txt",
        source=source,
        destination=tmp_path / "one.txt",
    )
    with pytest.raises(SecurityPolicyError, match="does not match"):
        manager.confirm(
            pending.token,
            operation="move_path",
            code=pending.code,
            source=source,
            destination=tmp_path / "two.txt",
        )


def test_confirm_succeeds_and_is_single_use(tmp_path) -> None:
    manager = ConfirmationManager()
    source = tmp_path / "a.txt"
    source.write_text("x", encoding="utf-8")

    pending = manager.stage(
        operation="delete_path", description="delete a.txt", source=source
    )
    confirmed = manager.confirm(
        pending.token,
        operation="delete_path",
        code=pending.code,
        source=source,
    )

    assert confirmed.token == pending.token
    assert manager.pending() == ()  # consumed

    with pytest.raises(SecurityPolicyError, match="pending confirmation"):
        manager.confirm(
            pending.token,
            operation="delete_path",
            code=pending.code,
            source=source,
        )


def test_confirmation_expires(tmp_path) -> None:
    manager = ConfirmationManager(ttl_seconds=0.05)
    pending = manager.stage(
        operation="delete_path", description="delete it", source=tmp_path
    )
    time.sleep(0.15)
    with pytest.raises(SecurityPolicyError, match="expired"):
        manager.confirm(
            pending.token,
            operation="delete_path",
            code=pending.code,
            source=tmp_path,
        )
    assert manager.pending() == ()


# ----------------------------------------------------------------------
# Part 6: the model cannot self-confirm (out-of-band code)
# ----------------------------------------------------------------------
def test_stage_issues_a_six_digit_code(tmp_path) -> None:
    manager = ConfirmationManager()
    pending = manager.stage(
        operation="delete_path", description="delete it", source=tmp_path
    )
    assert len(pending.code) == 6 and pending.code.isdigit()


def test_wrong_code_is_refused_and_staging_survives(tmp_path) -> None:
    manager = ConfirmationManager()
    pending = manager.stage(
        operation="delete_path", description="delete it", source=tmp_path
    )
    wrong = "999999" if pending.code != "999999" else "999998"
    with pytest.raises(SecurityPolicyError, match="confirmation code"):
        manager.confirm(
            pending.token,
            operation="delete_path",
            code=wrong,
            source=tmp_path,
        )
    assert manager.pending() == (pending,)  # the real user can still confirm


def test_correct_code_still_requires_the_exact_payload(tmp_path) -> None:
    manager = ConfirmationManager()
    pending = manager.stage(
        operation="delete_path", description="delete it", source=tmp_path
    )
    # a model that overheard the code still cannot redirect the action
    with pytest.raises(SecurityPolicyError, match="does not match"):
        manager.confirm(
            pending.token,
            operation="move_path",
            code=pending.code,
            source=tmp_path,
        )
    assert manager.pending() == (pending,)


def test_failed_payload_probe_does_not_burn_code_attempts(tmp_path) -> None:
    manager = ConfirmationManager()
    pending = manager.stage(
        operation="delete_path", description="delete it", source=tmp_path
    )
    for _ in range(3):
        with pytest.raises(SecurityPolicyError, match="does not match"):
            manager.confirm(
                pending.token,
                operation="move_path",
                code=pending.code,
                source=tmp_path,
            )
    # only wrong CODES count toward the attempt limit
    confirmed = manager.confirm(
        pending.token,
        operation="delete_path",
        code=pending.code,
        source=tmp_path,
    )
    assert confirmed.token == pending.token


def test_three_wrong_codes_withdraw_the_staging(tmp_path) -> None:
    manager = ConfirmationManager()
    pending = manager.stage(
        operation="delete_path", description="delete it", source=tmp_path
    )
    wrong = "999999" if pending.code != "999999" else "999998"
    for attempt in range(MAX_CODE_ATTEMPTS):
        expected = "withdrawn" if attempt == MAX_CODE_ATTEMPTS - 1 else "confirmation"
        with pytest.raises(SecurityPolicyError, match=expected):
            manager.confirm(
                pending.token,
                operation="delete_path",
                code=wrong,
                source=tmp_path,
            )
    # withdrawn: even the CORRECT code is now useless - nothing auto-runs
    assert manager.pending() == ()
    with pytest.raises(SecurityPolicyError, match="pending confirmation"):
        manager.confirm(
            pending.token,
            operation="delete_path",
            code=pending.code,
            source=tmp_path,
        )


def test_cancel_also_clears_attempt_history(tmp_path) -> None:
    manager = ConfirmationManager()
    first = manager.stage(
        operation="delete_path", description="delete it", source=tmp_path
    )
    wrong = "999999" if first.code != "999999" else "999998"
    with pytest.raises(SecurityPolicyError, match="confirmation code"):
        manager.confirm(
            first.token,
            operation="delete_path",
            code=wrong,
            source=tmp_path,
        )
    assert manager.cancel(first.token) is True
    # a fresh staging gets a clean slate: attempts are tracked per token
    second = manager.stage(
        operation="delete_path", description="delete it again", source=tmp_path
    )
    confirmed = manager.confirm(
        second.token,
        operation="delete_path",
        code=second.code,
        source=tmp_path,
    )
    assert confirmed.token == second.token


def test_cancel_removes_pending_confirmation(tmp_path) -> None:
    manager = ConfirmationManager()
    pending = manager.stage(
        operation="delete_path", description="delete it", source=tmp_path
    )
    assert manager.cancel(pending.token) is True
    assert manager.pending() == ()
    assert manager.cancel(pending.token) is False
    assert manager.cancel("never-issued") is False


def test_manager_requires_positive_ttl() -> None:
    with pytest.raises(SecurityPolicyError, match="positive"):
        ConfirmationManager(ttl_seconds=0)


# ----------------------------------------------------------------------
# no-shell regression guard (Part 1 rules 5 and 14)
# ----------------------------------------------------------------------
# Usage-shaped tokens; prose about these APIs is allowed, use is not.
FORBIDDEN_SHELL_TOKENS = (
    "os.system",
    "os.popen",
    "import subprocess",
    "subprocess.",
    "create_subprocess",
    "shell=true",
    "eval(",
    "exec(",
    "ctypes.windll",
    "invoke-expression",
    "start-process",
)


def test_no_shell_execution_in_tool_surface() -> None:
    # The model must never gain unrestricted shell access (rule 5 / rule 14).
    for filename in (
        "tools.py",
        "windows_security.py",
        "windows_fs.py",
        "windows_tools.py",
    ):
        source = (_SRC / filename).read_text(encoding="utf-8").casefold()
        for token in FORBIDDEN_SHELL_TOKENS:
            assert token not in source, f"{filename} contains {token!r}"


def test_windows_security_exposes_no_model_tools() -> None:
    # The policy layer must not be callable by the model directly.
    source = (_SRC / "windows_security.py").read_text(encoding="utf-8")
    assert "function_tool" not in source


# ----------------------------------------------------------------------
# Part 7: editing is confirmed and can never touch protected sources
# ----------------------------------------------------------------------
def test_editing_from_protected_location_is_refused() -> None:
    with pytest.raises(SecurityPolicyError, match="protected"):
        validate_source("C:\\Windows\\System32\\kernel32.dll", operation="edit_file")


def test_stage_rejects_edit_of_protected_source() -> None:
    manager = ConfirmationManager()
    with pytest.raises(SecurityPolicyError, match="protected"):
        manager.stage(
            operation="edit_file",
            description="change a system file",
            source="C:\\Windows\\System32\\kernel32.dll",
        )


def test_edit_file_has_consumes_source_policy() -> None:
    # an edit rewrites its source in place: the policy must say so, so that
    # validate_source and stage both refuse protected locations.
    from windows_security import _OPERATION_POLICIES

    policy = _OPERATION_POLICIES["edit_file"]
    assert policy.consumes_source is True
    assert policy.writes_destination is False
