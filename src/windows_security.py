"""Central security policy for Srilatha's Windows filesystem capability.

ARCHITECTURE (Part 1 - policy only, no tools yet)
=================================================
Future Windows tools live outside this module (e.g. ``src/windows_tools.py``)
and must route EVERY path argument through this policy before touching disk:

    validate_source() / validate_destination() / validate_path()
    -> classify_operation()
    -> requires_confirmation()
    -> ConfirmationManager.stage() ... user confirms ... .confirm()

Design rules this module enforces (the prompt is NOT the security boundary):

* Least privilege, default deny - unknown operation names classify as
  CRITICAL, and CRITICAL operations are never permitted.
* No unrestricted shell capability may ever be added to the tool surface;
  only explicit, individually policy-checked tools may exist.
* Protected system locations (Windows, Program Files, ProgramData, ...)
  cannot be consumed (delete/move/rename) or written to, regardless of what
  the model asks. Reading from them is allowed (observation is safe).
* Every path is normalized, structurally validated, resolved (collapsing
  ``..``, junctions and short names), classified, then checked against the
  operation policy. Always operate on the RETURNED resolved path - never on
  the raw model-supplied string.
* Destructive work enters a staged PENDING_CONFIRMATION bound to an exact
  payload (operation + source + destination), a single-use random token,
  and a short TTL. A confirmation for A can never execute B; the model
  cannot fabricate one because confirming requires the staged token, and
  staging itself re-validates every path.
* Untrusted content (webpages, filenames, documents, screenshots, anything
  seen on screen) is DATA, never instructions: nothing in this layer accepts
  "instructions" as input, and future tools must take only concrete values
  (paths, names, queries) so foreign text can never become policy input.
* Screen vision is observational; only real user voice interaction may
  stage or confirm an operation (wiring contract for the confirm tool).
* Application launching must resolve through a controlled allowlist in a
  future phase - arbitrary executables are never model-callable.

This module intentionally defines no model-facing tools: the policy layer
is not reachable by the model except through future tool wrappers that
call it first.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from confirmation import (
    CONFIRMATION_TTL_SECONDS,
    MAX_CODE_ATTEMPTS,
    code_matches,
    new_confirmation_code,
)

BULK_ESCALATION_THRESHOLD = 50
MAX_BULK_ITEMS = 1000

# Folder names that are protected at the root of ANY drive (drive-agnostic),
# together with everything below them. "system32" is covered by "windows".
PROTECTED_FOLDER_NAMES = frozenset(
    {
        "windows",
        "windows.old",
        "program files",
        "program files (x86)",
        "programdata",
        "recovery",
        "system volume information",
    }
)

_RESERVED_DEVICE_NAMES = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{index}" for index in range(1, 10)]
    + [f"lpt{index}" for index in range(1, 10)]
)
RESERVED_DEVICE_NAMES = _RESERVED_DEVICE_NAMES


class SecurityPolicyError(Exception):
    """Raised whenever a request violates the security policy."""


class OperationRisk(Enum):
    """Risk classification for a single filesystem operation."""

    READ = "read"
    LOW_RISK = "low_risk"
    MODERATE = "moderate"
    DESTRUCTIVE = "destructive"
    CRITICAL = "critical"


@dataclass(frozen=True)
class OperationPolicy:
    """How an operation may interact with sources and destinations."""

    risk: OperationRisk
    consumes_source: bool = False
    writes_destination: bool = False


# Default-deny: names that are not listed here classify as CRITICAL.
_UNKNOWN_POLICY = OperationPolicy(OperationRisk.CRITICAL)

_OPERATION_POLICIES: dict[str, OperationPolicy] = {
    # READ
    "list_directory": OperationPolicy(OperationRisk.READ),
    "search_files": OperationPolicy(OperationRisk.READ),
    "file_exists": OperationPolicy(OperationRisk.READ),
    "folder_exists": OperationPolicy(OperationRisk.READ),
    "get_file_info": OperationPolicy(OperationRisk.READ),
    "read_file": OperationPolicy(OperationRisk.READ),
    "inspect_tree": OperationPolicy(OperationRisk.READ),
    # LOW-RISK MODIFICATION
    "create_file": OperationPolicy(OperationRisk.LOW_RISK, writes_destination=True),
    "create_folder": OperationPolicy(OperationRisk.LOW_RISK, writes_destination=True),
    "copy_path": OperationPolicy(OperationRisk.LOW_RISK, writes_destination=True),
    "rename_path": OperationPolicy(OperationRisk.LOW_RISK, consumes_source=True),
    "open_path": OperationPolicy(OperationRisk.LOW_RISK),
    "launch_application": OperationPolicy(OperationRisk.LOW_RISK),
    # MODERATE
    "edit_file": OperationPolicy(OperationRisk.MODERATE, consumes_source=True),
    "move_path": OperationPolicy(
        OperationRisk.MODERATE,
        consumes_source=True,
        writes_destination=True,
    ),
    "move_folder": OperationPolicy(
        OperationRisk.MODERATE,
        consumes_source=True,
        writes_destination=True,
    ),
    "bulk_copy": OperationPolicy(OperationRisk.MODERATE, writes_destination=True),
    "bulk_rename": OperationPolicy(OperationRisk.MODERATE, consumes_source=True),
    "bulk_move": OperationPolicy(
        OperationRisk.MODERATE,
        consumes_source=True,
        writes_destination=True,
    ),
    # DESTRUCTIVE
    "delete_path": OperationPolicy(OperationRisk.DESTRUCTIVE, consumes_source=True),
    "recycle_path": OperationPolicy(OperationRisk.DESTRUCTIVE, consumes_source=True),
    "recursive_delete": OperationPolicy(
        OperationRisk.DESTRUCTIVE, consumes_source=True
    ),
    "bulk_delete": OperationPolicy(OperationRisk.DESTRUCTIVE, consumes_source=True),
    "permanent_delete": OperationPolicy(
        OperationRisk.DESTRUCTIVE, consumes_source=True
    ),
    # CRITICAL - recognized so they fail loudly; never permitted.
    "format_drive": OperationPolicy(OperationRisk.CRITICAL),
    "modify_system_configuration": OperationPolicy(OperationRisk.CRITICAL),
    "modify_security_settings": OperationPolicy(OperationRisk.CRITICAL),
    "modify_boot_configuration": OperationPolicy(OperationRisk.CRITICAL),
    "modify_firewall": OperationPolicy(OperationRisk.CRITICAL),
    "modify_antivirus": OperationPolicy(OperationRisk.CRITICAL),
}

# One escalation step when an operation covers many items at once.
_BULK_ESCALATION: dict[OperationRisk, OperationRisk] = {
    OperationRisk.LOW_RISK: OperationRisk.MODERATE,
    OperationRisk.MODERATE: OperationRisk.DESTRUCTIVE,
}


def _policy_for(operation: str) -> OperationPolicy:
    """Look up an operation's policy; unknown names default to CRITICAL."""
    if not isinstance(operation, str) or not operation.strip():
        raise SecurityPolicyError("The operation name must be a non-empty string.")
    return _OPERATION_POLICIES.get(operation.strip().casefold(), _UNKNOWN_POLICY)


# Rank order used when a caller stages an escalated risk for an operation.
_RISK_RANK: dict[OperationRisk, int] = {
    OperationRisk.READ: 0,
    OperationRisk.LOW_RISK: 1,
    OperationRisk.MODERATE: 2,
    OperationRisk.DESTRUCTIVE: 3,
    OperationRisk.CRITICAL: 4,
}


def _raise_risk(provided: OperationRisk, floor: OperationRisk) -> OperationRisk:
    """Combine a staged risk override with the operation's own risk.

    The override may RAISE the risk (bulk quantities, overwrites) but never
    lower it, and it can never out-rank a CRITICAL floor (default deny).
    """
    if not isinstance(provided, OperationRisk):
        raise SecurityPolicyError("The risk override must be an OperationRisk value.")
    return provided if _RISK_RANK[provided] >= _RISK_RANK[floor] else floor


# ----------------------------------------------------------------------
# path validation
# ----------------------------------------------------------------------
def _strip_extended_prefix(value: str) -> str:
    """Drop Windows extended-length prefixes after resolution."""
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[len("\\\\?\\UNC\\") :]
    if value.startswith("\\\\?\\"):
        return value[len("\\\\?\\") :]
    return value


def _normalize_path(path: object) -> Path:
    """Structurally validate and fully resolve a Windows path.

    Rejects: empty/malformed input, control characters (incl. NUL),
    wildcards, relative and drive-relative paths, UNC and device-namespace
    paths, reserved device names (CON, NUL, ...), alternate data streams,
    and components that collapse to nothing after Windows' own trailing
    dot/space stripping. Then resolves ``..``, junctions, and short names so
    classification always sees the real destination.
    """
    try:
        raw = os.fspath(path)
    except TypeError:
        raise SecurityPolicyError(
            "The path must be a string or path-like value."
        ) from None
    if isinstance(raw, bytes):
        raise SecurityPolicyError("The path must be text, not bytes.")

    value = raw.strip()
    if not value:
        raise SecurityPolicyError("The path cannot be empty.")
    if any(ord(ch) < 32 for ch in value):
        raise SecurityPolicyError(
            "The path contains control characters (including NUL)."
        )
    if value.startswith(("\\\\", "//")):
        raise SecurityPolicyError(
            "Network and device namespace paths are not permitted."
        )
    if "*" in value or "?" in value:
        raise SecurityPolicyError(
            "The path contains wildcard characters; pass one concrete path."
        )

    candidate = Path(value)
    if not candidate.is_absolute() or not candidate.drive:
        raise SecurityPolicyError(
            "The path must be an absolute Windows path with a drive letter."
        )

    parts = list(candidate.parts)
    normalized_parts = [parts[0]]
    for part in parts[1:]:
        if part in (".", ".."):
            # Resolution collapses these; they are not file names.
            normalized_parts.append(part)
            continue
        normalized = part.rstrip(" .")
        if not normalized:
            raise SecurityPolicyError(
                "The path contains an invalid empty component after normalization."
            )
        if ":" in normalized:
            raise SecurityPolicyError(
                "Alternate data streams are not permitted in paths."
            )
        base = normalized.split(".")[0].casefold()
        if base in _RESERVED_DEVICE_NAMES:
            raise SecurityPolicyError(
                f"The path uses the reserved device name {base!r}."
            )
        normalized_parts.append(normalized)

    rebuilt = Path(normalized_parts[0])
    for part in normalized_parts[1:]:
        rebuilt = rebuilt / part
    resolved = Path(_strip_extended_prefix(str(rebuilt.resolve())))
    return resolved


def validate_path(path: str | Path, *, must_exist: bool | None = None) -> Path:
    """Validate a path structurally and return its resolved form.

    ``must_exist=True`` requires the path to exist, ``must_exist=False``
    requires it not to exist, ``None`` (default) imposes no constraint.
    Raises ``SecurityPolicyError`` for anything malformed or unsafe.
    """
    resolved = _normalize_path(path)
    if must_exist is True and not resolved.exists():
        raise SecurityPolicyError(f"Path does not exist: {resolved}")
    if must_exist is False and resolved.exists():
        raise SecurityPolicyError(f"Path already exists: {resolved}")
    return resolved


def is_protected_path(path: str | Path) -> bool:
    """True for drive roots and protected system locations (and children).

    Case-insensitive, traversal-proof (classification happens after
    resolution), and drive-agnostic: ``D:\\Windows`` is as protected as
    ``C:\\Windows``. User locations (Desktop, Documents, other drives'
    project folders) are never protected.
    """
    resolved = _normalize_path(path)
    parts = resolved.parts
    if len(parts) == 1:  # a drive root itself, e.g. "C:\\"
        return True
    return len(parts) >= 2 and parts[1].casefold() in PROTECTED_FOLDER_NAMES


# ----------------------------------------------------------------------
# operation policy
# ----------------------------------------------------------------------
def classify_operation(operation: str) -> OperationRisk:
    """Classify an operation name; unknown names are CRITICAL (deny)."""
    return _policy_for(operation).risk


def is_permitted(risk: OperationRisk) -> bool:
    """Whether a risk level may ever be executed (CRITICAL never may)."""
    return risk is not OperationRisk.CRITICAL


def requires_confirmation(
    operation_or_risk: str | OperationRisk,
) -> bool:
    """Whether this operation must be staged and confirmed by the user."""
    risk = (
        operation_or_risk
        if isinstance(operation_or_risk, OperationRisk)
        else classify_operation(operation_or_risk)
    )
    return risk in (
        OperationRisk.MODERATE,
        OperationRisk.DESTRUCTIVE,
        OperationRisk.CRITICAL,
    )


def validate_source(path: str | Path, *, operation: str) -> Path:
    """Validate the read/consumed side of an operation.

    The source must exist. Operations that consume their source (delete,
    move, rename) may never target a protected system location; operations
    that only read the source (list, search, copy) may. Policy is evaluated
    before any existence check, so protected locations are refused without
    even stat-ing them.
    """
    policy = _policy_for(operation)
    if not is_permitted(policy.risk):
        raise SecurityPolicyError(
            f"Operation {operation!r} is classified critical and is never permitted."
        )
    resolved = validate_path(path)
    if policy.consumes_source and is_protected_path(resolved):
        raise SecurityPolicyError(
            f"{resolved} is protected; {operation!r} must not consume "
            "a system location."
        )
    if not resolved.exists():
        raise SecurityPolicyError(f"Path does not exist: {resolved}")
    return resolved


def validate_destination(
    path: str | Path,
    *,
    operation: str,
    source: str | Path | None = None,
) -> Path:
    """Validate the written side of an operation.

    The destination (a path or anything below it) may never be inside a
    protected system location or be a drive root. When ``source`` is
    supplied, source and destination must differ, and an existing
    non-directory destination is reported as a conflict (overwriting
    requires explicit user confirmation through the confirmation flow).
    """
    policy = _policy_for(operation)
    if not is_permitted(policy.risk):
        raise SecurityPolicyError(
            f"Operation {operation!r} is classified critical and is never permitted."
        )
    resolved = validate_path(path)
    if is_protected_path(resolved):
        raise SecurityPolicyError(
            f"{resolved} is a protected system location; refusing to write there."
        )
    if source is not None:
        resolved_source = validate_source(source, operation=operation)
        if resolved_source == resolved:
            raise SecurityPolicyError(
                "Source and destination are identical; nothing would happen."
            )
        if resolved.exists() and not resolved.is_dir():
            raise SecurityPolicyError(
                f"Destination {resolved} already exists; overwriting it "
                "requires explicit user confirmation."
            )
    return resolved


def validate_bulk_operation(count: int, operation: str) -> OperationRisk:
    """Validate a bulk operation and return its (possibly escalated) risk.

    Counts of ``BULK_ESCALATION_THRESHOLD`` or more escalate the risk one
    level (low -> moderate -> destructive). Counts above
    ``MAX_BULK_ITEMS`` are refused outright, and reads are never escalated.
    """
    policy = _policy_for(operation)
    if not is_permitted(policy.risk):
        raise SecurityPolicyError(
            f"Operation {operation!r} is classified critical and is never permitted."
        )
    if not isinstance(count, int):
        raise SecurityPolicyError("The item count must be an integer.")
    if count < 1:
        raise SecurityPolicyError("The item count must be at least 1.")
    if count > MAX_BULK_ITEMS:
        raise SecurityPolicyError(
            f"Refusing to touch {count} items: the maximum is "
            f"{MAX_BULK_ITEMS} per operation."
        )
    risk = policy.risk
    if count >= BULK_ESCALATION_THRESHOLD:
        risk = _BULK_ESCALATION.get(risk, risk)
    return risk


# ----------------------------------------------------------------------
# confirmation architecture
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class PendingConfirmation:
    """A staged operation awaiting genuine user confirmation."""

    token: str
    operation: str
    risk: OperationRisk
    source: str | None
    destination: str | None
    description: str
    code: str
    created_at: datetime
    expires_at: datetime


class ConfirmationManager:
    """Stages risky operations until a real user confirmation arrives.

    Guarantees:
      * operations that never need confirmation (and CRITICAL or unknown
        ones) cannot be staged at all;
      * staging re-runs full path policy, so protected locations can never
        become confirmable;
      * ``confirm`` needs the exact staged token, the exact operation, and
        the exact resolved source/destination - a confirmation for A never
        executes B;
      * confirmations are single-use and expire after a short TTL;
      * the manager is in-process state: nothing the model says can create
        or mutate a pending entry except going through ``stage``.
    """

    def __init__(self, ttl_seconds: float = CONFIRMATION_TTL_SECONDS) -> None:
        if not isinstance(ttl_seconds, (int, float)) or ttl_seconds <= 0:
            raise SecurityPolicyError("The confirmation lifetime must be positive.")
        self._ttl_seconds = float(ttl_seconds)
        self._pending: dict[str, PendingConfirmation] = {}
        self._code_attempts: dict[str, int] = {}

    def stage(
        self,
        *,
        operation: str,
        description: str,
        source: str | Path | None = None,
        destination: str | Path | None = None,
        risk: OperationRisk | None = None,
    ) -> PendingConfirmation:
        """Stage an operation and return its pending confirmation record.

        ``risk`` lets a caller stage an ESCALATED risk for a registered
        operation (bulk quantities, overwriting writes). It may only raise
        the risk above the operation's own classification - never lower it -
        and it can never make a CRITICAL operation permissible.
        """
        self._purge_expired()
        policy = _policy_for(operation)
        effective_risk = policy.risk if risk is None else _raise_risk(risk, policy.risk)
        if not is_permitted(effective_risk):
            raise SecurityPolicyError(
                f"Operation {operation!r} is classified critical "
                "and is never permitted."
            )
        if not requires_confirmation(effective_risk):
            raise SecurityPolicyError(
                f"Operation {operation!r} does not require confirmation."
            )
        if not isinstance(description, str) or not description.strip():
            raise SecurityPolicyError(
                "A clear description of the operation is required."
            )

        resolved_source = (
            validate_source(source, operation=operation) if source is not None else None
        )
        resolved_destination = (
            validate_destination(destination, operation=operation)
            if destination is not None
            else None
        )
        if (
            resolved_source is not None
            and resolved_destination is not None
            and resolved_source == resolved_destination
        ):
            raise SecurityPolicyError(
                "Source and destination are identical; nothing would happen."
            )

        # Reliability: re-staging the SAME still-valid action returns it
        # unchanged (same token, same six-digit code) instead of minting a
        # fresh one. Otherwise every model retry replaced the code on the
        # user's screen while the model still held the old token, so his
        # read-back could never match ("the code changes after each
        # attempt"). _purge_expired() already ran, so anything matched
        # here is genuinely still confirmable; withdrawn or expired
        # confirmations are gone and are always re-issued fresh.
        normalized_operation = operation.strip().casefold()
        source_key = None if resolved_source is None else str(resolved_source)
        destination_key = (
            None if resolved_destination is None else str(resolved_destination)
        )
        for existing in self._pending.values():
            if (
                existing.operation == normalized_operation
                and existing.source == source_key
                and existing.destination == destination_key
            ):
                return existing

        now = datetime.now(timezone.utc)
        pending = PendingConfirmation(
            token=secrets.token_urlsafe(16),
            operation=operation.strip().casefold(),
            risk=effective_risk,
            source=(None if resolved_source is None else str(resolved_source)),
            destination=(
                None if resolved_destination is None else str(resolved_destination)
            ),
            description=description.strip(),
            code=new_confirmation_code(),
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl_seconds),
        )
        self._pending[pending.token] = pending
        return pending

    def confirm(
        self,
        token: str,
        *,
        operation: str,
        code: str,
        source: str | Path | None = None,
        destination: str | Path | None = None,
    ) -> PendingConfirmation:
        """Consume a staged confirmation whose payload and code both match."""
        if not isinstance(token, str) or not token:
            raise SecurityPolicyError("No pending confirmation matches that token.")
        pending = self._pending.get(token)
        if pending is None:
            raise SecurityPolicyError("No pending confirmation matches that token.")
        if datetime.now(timezone.utc) >= pending.expires_at:
            self._code_attempts.pop(token, None)
            del self._pending[token]
            raise SecurityPolicyError(
                "The staged confirmation has expired; ask the user again."
            )

        incoming_operation = (
            operation.strip().casefold() if isinstance(operation, str) else None
        )
        if incoming_operation != pending.operation:
            raise SecurityPolicyError(
                "The confirmation does not match the staged operation."
            )
        supplied_source = validate_path(source) if source is not None else None
        supplied_destination = (
            validate_path(destination) if destination is not None else None
        )
        if not _payload_matches(supplied_source, pending.source):
            raise SecurityPolicyError(
                "The confirmation does not match the staged source."
            )
        if not _payload_matches(supplied_destination, pending.destination):
            raise SecurityPolicyError(
                "The confirmation does not match the staged destination."
            )

        # The CODE proves the user; the payload proves the action. Payload
        # checks ran first, so a mismatched payload never burns a code attempt.
        if not code_matches(pending.code, code):
            attempts = self._code_attempts.get(token, 0) + 1
            if attempts >= MAX_CODE_ATTEMPTS:
                self._code_attempts.pop(token, None)
                del self._pending[token]
                raise SecurityPolicyError(
                    "Too many wrong confirmation codes; the staged "
                    "confirmation has been withdrawn."
                )
            self._code_attempts[token] = attempts
            raise SecurityPolicyError(
                "The confirmation code does not match the code shown to the user."
            )

        self._code_attempts.pop(token, None)
        del self._pending[token]  # single use
        return pending

    def cancel(self, token: str) -> bool:
        """Withdraw a pending confirmation; True if one was removed."""
        self._code_attempts.pop(token, None)
        return self._pending.pop(token, None) is not None

    def cancel_all(self) -> int:
        """Withdraw every pending confirmation; returns how many."""
        count = len(self._pending)
        self._pending.clear()
        self._code_attempts.clear()
        return count

    def pending(self) -> tuple[PendingConfirmation, ...]:
        """Currently staged confirmations (expired ones are purged)."""
        self._purge_expired()
        return tuple(self._pending.values())

    def _purge_expired(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [
            token
            for token, pending in self._pending.items()
            if now >= pending.expires_at
        ]
        for token in expired:
            self._code_attempts.pop(token, None)
            del self._pending[token]


def _payload_matches(supplied: Path | None, staged: str | None) -> bool:
    """Compare a supplied path against the staged resolved string."""
    if supplied is None or staged is None:
        return supplied is None and staged is None
    return str(supplied).casefold() == staged.casefold()
