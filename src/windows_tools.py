"""Model-facing Windows filesystem tools (Part 2).

``WindowsTools`` mirrors ``BrowserTools``: ``@function_tool`` methods with
model-facing docstrings, ``ToolError`` error mapping, and Part 1's
``ConfirmationManager`` for staged, user-confirmed risky operations.

Security chain for every tool (the prompt is NOT the boundary):

1. Natural references resolve first (location aliases, relative names,
   "this file"/"that folder") into absolute candidates.
2. The engine enforces full Part 1 policy on those candidates - protected
   system locations, traversal, reserved names, default-deny - before any
   disk access in their direction.
3. Risky work (MODERATE/DESTRUCTIVE, bulk quantities, overwrites) is staged
   with a single-use token bound to the exact operation and paths; only
   ``confirm_windows_action`` - which the model may only call after the real
   user clearly agrees - unlocks the retried call. CRITICAL/protected
   requests can never be staged, so they can never be confirmed.
4. Failures map to ``ToolError`` with stable codes (NOT_FOUND, CONFLICT,
   ACCESS_DENIED, NO_MATCH, AMBIGUOUS, ...) so the agent can explain them
   naturally by voice.

No tool exposes a shell or arbitrary program execution: opening uses
Windows file associations (never executables), launching uses the fixed
allowlist inside ``windows_fs``, and deletion defaults to the Recycle Bin.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ToolError

import windows_fs
from confirmation import announce_confirmation
from windows_fs import WindowsFSError
from windows_security import (
    CONFIRMATION_TTL_SECONDS,
    ConfirmationManager,
    OperationRisk,
    SecurityPolicyError,
    is_permitted,
    requires_confirmation,
    validate_bulk_operation,
    validate_path,
)


class WindowsTools:
    """The Windows filesystem tools plus confirm and cancel helpers."""

    def __init__(
        self, confirmation_publisher: Callable[[dict], None] | None = None
    ) -> None:
        self._confirmation_publisher = confirmation_publisher
        self._manager = ConfirmationManager()
        # The user's last approval: (operation, source, destination).
        # Consumed by the next exactly-matching gated call; expires.
        self._approved: tuple[str, str | None, str | None] | None = None
        self._approved_at: float | None = None
        # Contextual references: "this file", "that folder", relative names.
        self._context_dir: str | None = None
        self._last_file: str | None = None
        self._last_folder: str | None = None
        # The most recently used item of any type, for bare "it"/"this"/"that".
        self._last_item: str | None = None
        # Recent paths (newest first) so a bare name can be disambiguated
        # against items we have actually touched this session.
        self._recent: list[str] = []

    @property
    def tools(self) -> list:
        return [
            self.list_directory,
            self.search_files,
            self.read_file,
            self.inspect_tree,
            self.file_exists,
            self.folder_exists,
            self.get_file_info,
            self.create_file,
            self.create_folder,
            self.edit_file,
            self.rename_path,
            self.move_path,
            self.copy_path,
            self.recycle_path,
            self.delete_path,
            self.open_path,
            self.launch_application,
            self.confirm_windows_action,
            self.cancel_windows_action,
        ]

    # ------------------------------------------------------------------
    # references and context
    # ------------------------------------------------------------------
    _RECENT_LIMIT = 25

    def _resolve(self, raw: str) -> str:
        """Turn a natural reference into an absolute candidate path."""
        candidate = windows_fs.resolve_user_path(
            raw,
            context_dir=self._context_dir,
            context_file=self._last_file,
            context_folder=self._last_folder,
            context_item=self._last_item,
        )
        return self._disambiguate(raw, candidate)

    def _disambiguate(self, raw: str, candidate: str) -> str:
        """A bare name matching several recent items: ask, never guess."""
        if Path(candidate).exists():
            return candidate  # an existing primary always wins
        value = raw.strip().strip('"').strip("'")
        if not value or "\\" in value or "/" in value:
            return candidate
        if Path(value).is_absolute():
            return candidate
        wanted = Path(candidate).name.casefold()
        matches = [
            recent
            for recent in self._recent
            if Path(recent).name.casefold() == wanted and Path(recent).exists()
        ]
        if len(matches) > 1:
            listing = "; ".join(matches)
            raise WindowsFSError(
                "AMBIGUOUS",
                f"Several items are named {Path(candidate).name!r}: "
                f"{listing}. Ask which one is meant; never guess.",
            )
        if matches:
            return matches[0]
        return candidate

    def _remember(self, path: str) -> None:
        """Keep recent paths (newest first) for bare-name disambiguation."""
        entry = str(Path(path))
        folded = entry.casefold()
        self._recent = [item for item in self._recent if item.casefold() != folded]
        self._recent.insert(0, entry)
        del self._recent[self._RECENT_LIMIT :]

    def _track(self, path: str) -> None:
        """Remember the last path so later "this file" references work."""
        candidate = Path(path)
        self._remember(str(candidate))
        self._last_item = str(candidate)
        if candidate.is_dir():
            self._last_folder = str(candidate)
            self._context_dir = str(candidate)
        else:
            self._last_file = str(candidate)
            self._context_dir = str(candidate.parent)

    # ------------------------------------------------------------------
    # confirmation gate
    # ------------------------------------------------------------------
    def _matches_approved(
        self, operation: str, source: str | None, destination: str | None
    ) -> bool:
        if self._approved is None or self._approved_at is None:
            return False
        if time.monotonic() - self._approved_at > CONFIRMATION_TTL_SECONDS:
            self._approved = None
            self._approved_at = None
            return False
        normalized_source = (
            None if source is None else str(validate_path(source)).casefold()
        )
        normalized_destination = (
            None if destination is None else str(validate_path(destination)).casefold()
        )
        stored_source, stored_destination = self._approved[1], self._approved[2]
        return (
            operation.strip().casefold(),
            normalized_source,
            normalized_destination,
        ) == (
            self._approved[0].casefold(),
            None if stored_source is None else stored_source.casefold(),
            None if stored_destination is None else stored_destination.casefold(),
        )

    def _ensure_confirmed(
        self,
        *,
        operation: str,
        source: str | None,
        destination: str | None,
        description: str,
        risk: OperationRisk,
    ) -> None:
        """Pass when already user-approved or trivial; else stage and stop."""
        if self._matches_approved(operation, source, destination):
            self._approved = None
            self._approved_at = None
            return
        if not is_permitted(risk):
            raise ToolError(
                f"Operation {operation!r} is classified critical "
                "and is never permitted."
            )
        if not requires_confirmation(risk):
            return
        try:
            pending = self._manager.stage(
                operation=operation,
                description=description,
                source=source,
                destination=destination,
                risk=risk,
            )
        except SecurityPolicyError as exc:
            raise ToolError(str(exc)) from exc
        announce_confirmation(
            self._confirmation_publisher,
            token=pending.token,
            code=pending.code,
            description=pending.description,
            operation=pending.operation,
            source=pending.source,
            destination=pending.destination,
            expires_in=CONFIRMATION_TTL_SECONDS,
        )
        raise ToolError(
            f"Staged for user confirmation: token '{pending.token}'. "
            f"Explain exactly what will happen ({description}) and ask the "
            "user to confirm. The six-digit confirmation code is shown to "
            "the user only, never to you; he must read back that exact code "
            "to you. Call confirm_windows_action with this token, that code, "
            "and the same operation, source, and destination. Never claim "
            "the action is confirmed until that tool reports success."
        )

    def _bulk_risk(self, source: Path, operation: str) -> OperationRisk:
        """Risk for an operation on ``source``, escalated by item count.

        ``count_items`` counts descendants, so an empty folder scores 0;
        the folder itself is still the one item being touched, so the count
        never drops below 1 (the policy's minimum).
        """
        return validate_bulk_operation(
            max(windows_fs.count_items(source), 1), operation
        )

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------
    @function_tool()
    async def list_directory(self, context: RunContext, path: str) -> dict[str, object]:
        """List one folder's contents: names, type, size, and modified date.

        Use for "what is in ..." or "show my Desktop" requests. For finding a
        specific file by name anywhere, prefer search_files instead.

        Args:
            path: A standard location name (Desktop, Documents, Downloads,
                Pictures, Videos, Music, Home, OneDrive), a full path, a
                relative name, or a contextual reference like "this folder".
        """
        try:
            resolved = self._resolve(path)
            result = await asyncio.to_thread(windows_fs.list_directory, resolved)
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        self._track(result["path"])
        return result

    @function_tool()
    async def search_files(
        self, context: RunContext, root: str, pattern: str
    ) -> dict[str, object]:
        """Search a folder's tree for files whose NAME matches a pattern.

        Plain text matches the name anywhere (case-insensitive); use a
        wildcard like *.pdf or report* for prefix/extension matching. Results
        are capped: mention if truncated and narrow the pattern if needed.

        Args:
            root: Where to search - a location name ("Documents"), a full
                path, or a contextual reference.
            pattern: File-name text or wildcard to look for, never a path.
        """
        try:
            resolved_root = self._resolve(root)
            result = await asyncio.to_thread(
                windows_fs.search_files, resolved_root, pattern
            )
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        self._track(result["root"])
        for hit in result["results"]:
            # Every hit becomes disambiguation context for bare names.
            self._remember(str(hit))
        if result["count"] == 1:
            # A single hit is the obvious referent for "it" / "this file".
            self._track(str(result["results"][0]))
        return result

    @function_tool()
    async def read_file(self, context: RunContext, path: str) -> dict[str, object]:
        """Read one file's text content (also extracts DOCX/PPTX/PDF text).

        Binary files, spreadsheets, and oversized files are refused with a
        stable code instead of dumping noise into the conversation.

        Args:
            path: What to read - full path, location name, relative name, or
                a contextual reference like "this file".
        """
        try:
            resolved = self._resolve(path)
            result = await asyncio.to_thread(windows_fs.read_file, resolved)
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        self._track(result["path"])
        return result

    @function_tool()
    async def inspect_tree(self, context: RunContext, path: str) -> dict[str, object]:
        """Show a folder as a bounded tree with sizes, counts, and key files.

        Depth and entry limits keep huge folders safe; junctions are listed
        but never followed.

        Args:
            path: Which folder - full path, location name, relative name, or
                a contextual reference like "that folder".
        """
        try:
            resolved = self._resolve(path)
            result = await asyncio.to_thread(windows_fs.inspect_tree, resolved)
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        self._track(result["path"])
        return result

    @function_tool()
    async def file_exists(self, context: RunContext, path: str) -> dict[str, object]:
        """Check whether a specific FILE exists before acting on it.

        Args:
            path: The file's location name, full path, relative name, or a
                contextual reference like "this file".
        """
        try:
            resolved = self._resolve(path)
            result = await asyncio.to_thread(windows_fs.file_exists, resolved)
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        return result

    @function_tool()
    async def folder_exists(self, context: RunContext, path: str) -> dict[str, object]:
        """Check whether a specific FOLDER exists before acting on it.

        Args:
            path: The folder's location name, full path, relative name, or a
                contextual reference like "that folder".
        """
        try:
            resolved = self._resolve(path)
            result = await asyncio.to_thread(windows_fs.folder_exists, resolved)
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        return result

    @function_tool()
    async def get_file_info(self, context: RunContext, path: str) -> dict[str, object]:
        """Get one file's or folder's details: size, dates, extension, flags.

        Args:
            path: Location name, full path, relative name, or a contextual
                reference like "this file".
        """
        try:
            resolved = self._resolve(path)
            result = await asyncio.to_thread(windows_fs.get_file_info, resolved)
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        self._track(result["path"])
        return result

    # ------------------------------------------------------------------
    # creating
    # ------------------------------------------------------------------
    @function_tool()
    async def create_file(
        self,
        context: RunContext,
        path: str,
        content: str = "",
        overwrite: bool = False,
    ) -> dict[str, object]:
        """Create a text file with the given content, then verify it wrote.

        Missing parent folders are created automatically. An existing file
        is never overwritten unless overwrite=True, which itself requires
        the user's explicit confirmation.

        Args:
            path: Where to create it - location name, full path, or a
                relative name resolved against the last-used folder.
            content: The text to write (UTF-8); use "" for an empty file.
            overwrite: Only True when the user explicitly asked to replace
                an existing file.
        """
        try:
            resolved = self._resolve(path)
            risk = OperationRisk.DESTRUCTIVE if overwrite else OperationRisk.LOW_RISK
            self._ensure_confirmed(
                operation="create_file",
                source=None,
                destination=resolved,
                description=f"overwrite the existing file at {resolved}",
                risk=risk,
            )
            result = await asyncio.to_thread(
                windows_fs.create_file, resolved, content, overwrite=overwrite
            )
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        self._track(result["path"])
        return result

    @function_tool()
    async def create_folder(self, context: RunContext, path: str) -> dict[str, object]:
        """Create a folder (parents included), then verify it exists.

        Args:
            path: Where to create it - location name, full path, or a
                relative name resolved against the last-used folder.
        """
        try:
            resolved = self._resolve(path)
            self._ensure_confirmed(
                operation="create_folder",
                source=None,
                destination=resolved,
                description=f"create the folder {resolved}",
                risk=OperationRisk.LOW_RISK,
            )
            result = await asyncio.to_thread(windows_fs.create_folder, resolved)
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        self._track(result["path"])
        return result

    @function_tool()
    async def edit_file(
        self,
        context: RunContext,
        path: str,
        mode: str = "replace",
        find: str = "",
        replace: str = "",
        append: str = "",
    ) -> dict[str, object]:
        """Change text inside an existing text file (replace or append).

        Everything is checked before anything changes: missing files,
        unsupported documents, binary files, and a find text that is not
        present all fail BEFORE a confirmation is staged. The edit itself
        requires the user's confirmation like every other MODERATE action,
        and the result is verified by re-reading the file.

        Args:
            path: Which file - full path, location name, relative name, or
                a contextual reference like "this file".
            mode: "replace" (use find/replace) or "append" (add at the end).
            find: Exact text to replace (mode "replace" only).
            replace: The replacement text (mode "replace" only).
            append: Text to add at the end (mode "append" only).
        """
        try:
            resolved = self._resolve(path)
            plan = await asyncio.to_thread(
                windows_fs.plan_edit,
                resolved,
                mode=mode,
                find=find,
                replace=replace,
                append=append,
            )
            src = Path(plan["path"])
            if plan["mode"] == "replace":
                description = (
                    f"edit {src.name}: replace {plan['matches']} occurrence(s) "
                    f"of {find!r} with {replace!r}"
                )
            else:
                description = f"edit {src.name}: append text to the end"
            self._ensure_confirmed(
                operation="edit_file",
                source=str(src),
                destination=None,
                description=description,
                risk=self._bulk_risk(src, "edit_file"),
            )
            result = await asyncio.to_thread(
                windows_fs.edit_file,
                resolved,
                mode=mode,
                find=find,
                replace=replace,
                append=append,
            )
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        self._track(result["path"])
        return result

    # ------------------------------------------------------------------
    # renaming, moving, copying
    # ------------------------------------------------------------------
    @function_tool()
    async def rename_path(
        self, context: RunContext, source: str, new_name: str
    ) -> dict[str, object]:
        """Rename a file or folder, keeping it in the same folder.

        The new name must be a plain name - no folders, no paths. Conflicts
        and collisions are reported, never overwritten.

        Args:
            source: What to rename - location name, full path, relative
                name, or a contextual reference like "this file".
            new_name: The new name only (optionally with an extension).
        """
        try:
            resolved = self._resolve(source)
            src = windows_fs.require_source(resolved, operation="rename_path")
            risk = self._bulk_risk(src, "rename_path")
            self._ensure_confirmed(
                operation="rename_path",
                source=str(src),
                destination=None,
                description=f"rename {src.name} to {new_name}",
                risk=risk,
            )
            result = await asyncio.to_thread(windows_fs.rename_path, str(src), new_name)
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        self._track(result["to"])
        return result

    @function_tool()
    async def move_path(
        self, context: RunContext, source: str, destination: str
    ) -> dict[str, object]:
        """Move a file or folder to a new location (never overwriting).

        Moving is always confirmed by the user first: conflicts are detected
        and reported before that confirmation, so nothing is ever replaced.

        Args:
            source: What to move - full path, location name, relative name,
                or a contextual reference like "that file".
            destination: Target file path, or an existing folder to move
                into (the item keeps its name inside the folder).
        """
        try:
            resolved_source = self._resolve(source)
            resolved_destination = self._resolve(destination)
            src, dest, final = windows_fs.plan_move(
                resolved_source, resolved_destination
            )
            risk = self._bulk_risk(src, "move_path")
            self._ensure_confirmed(
                operation="move_path",
                source=str(src),
                destination=str(dest),
                description=f"move {src.name} to {final}",
                risk=risk,
            )
            result = await asyncio.to_thread(windows_fs.move_path, str(src), str(dest))
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        self._track(result["to"])
        return result

    @function_tool()
    async def copy_path(
        self,
        context: RunContext,
        source: str,
        destination: str,
        overwrite: bool = False,
    ) -> dict[str, object]:
        """Copy a file or folder; the original always stays in place.

        Conflicts at the destination are reported, never overwritten, unless
        overwrite=True - which requires the user's explicit confirmation.

        Args:
            source: What to copy - full path, location name, relative name,
                or a contextual reference.
            destination: Target file path, or an existing folder to copy
                into. Large folders (many items) are confirmed first.
            overwrite: Only True when the user explicitly asked to replace
                an existing file.
        """
        try:
            resolved_source = self._resolve(source)
            resolved_destination = self._resolve(destination)
            src, dest, final = windows_fs.plan_copy(
                resolved_source, resolved_destination, overwrite=overwrite
            )
            risk = self._bulk_risk(src, "copy_path")
            if overwrite:
                risk = OperationRisk.DESTRUCTIVE
            self._ensure_confirmed(
                operation="copy_path",
                source=str(src),
                destination=str(dest),
                description=f"copy {src.name} to {final}",
                risk=risk,
            )
            result = await asyncio.to_thread(
                windows_fs.copy_path,
                str(src),
                str(dest),
                overwrite=overwrite,
            )
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        self._track(result["to"])
        return result

    # ------------------------------------------------------------------
    # recycling and deleting
    # ------------------------------------------------------------------
    @function_tool()
    async def recycle_path(self, context: RunContext, path: str) -> dict[str, object]:
        """Move an item to the Windows Recycle Bin - recoverable deletion.

        Prefer this over delete_path for anything the user might want back.
        It is confirmed by the user first, then executed and verified.

        Args:
            path: What to recycle - full path, location name, relative name,
                or a contextual reference like "this file".
        """
        try:
            resolved = self._resolve(path)
            src = windows_fs.require_source(resolved, operation="recycle_path")
            risk = self._bulk_risk(src, "recycle_path")
            self._ensure_confirmed(
                operation="recycle_path",
                source=str(src),
                destination=None,
                description=f"move {src.name} to the Recycle Bin",
                risk=risk,
            )
            result = await asyncio.to_thread(windows_fs.recycle_path, str(src))
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        return result

    @function_tool()
    async def delete_path(
        self, context: RunContext, path: str, permanent: bool = False
    ) -> dict[str, object]:
        """Delete an item; by default it goes to the Recycle Bin.

        Unless the user explicitly demands permanent deletion, items are
        recycled (recoverable). permanent=True removes the item for good;
        it always requires the user's clear confirmation, like every delete.

        Args:
            path: What to delete - full path, location name, relative name,
                or a contextual reference like "this file".
            permanent: Only True when the user explicitly asked for
                permanent, unrecoverable deletion.
        """
        try:
            resolved = self._resolve(path)
            src = windows_fs.require_source(resolved, operation="delete_path")
            risk = self._bulk_risk(src, "delete_path")
            if permanent:
                description = f"permanently delete {src.name}"
            else:
                description = f"move {src.name} to the Recycle Bin"
            self._ensure_confirmed(
                operation="delete_path",
                source=str(src),
                destination=None,
                description=description,
                risk=risk,
            )
            if permanent:
                # Folders must pass recursive=True or the engine refuses the
                # deletion AFTER confirmation (a confirmed action that then
                # fails); files keep the flag off so nothing changes there.
                result = await asyncio.to_thread(
                    windows_fs.delete_path, str(src), recursive=src.is_dir()
                )
            else:
                recycled = await asyncio.to_thread(windows_fs.recycle_path, str(src))
                result = {
                    **recycled,
                    "deleted": True,
                    "method": "recycle_bin",
                }
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        return result

    # ------------------------------------------------------------------
    # opening and launching
    # ------------------------------------------------------------------
    @function_tool()
    async def open_path(self, context: RunContext, path: str) -> dict[str, object]:
        """Open a document or folder with its normal Windows association.

        Use for "open this file", "open my report", "show me that folder".
        Executable-like files are refused: for applications use
        launch_application, and for websites use the browser tools.

        Args:
            path: What to open - full path, location name, relative name, or
                a contextual reference like "this file".
        """
        try:
            resolved = self._resolve(path)
            result = await asyncio.to_thread(windows_fs.open_path, resolved)
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        self._track(result["path"])
        return result

    @function_tool()
    async def launch_application(
        self, context: RunContext, application: str
    ) -> dict[str, object]:
        """Start a known Windows application by simple name.

        Only the built-in allowlist is accepted (the error lists it: e.g.
        notepad, calculator, paint, explorer, vs code). Never pass a path,
        command, or arguments - for documents and folders use open_path
        instead.

        Args:
            application: A simple application name such as "notepad",
                "calculator", or "vs code".
        """
        try:
            result = await asyncio.to_thread(windows_fs.launch_application, application)
        except (WindowsFSError, SecurityPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        return result

    # ------------------------------------------------------------------
    # confirmation
    # ------------------------------------------------------------------
    @function_tool()
    async def confirm_windows_action(
        self,
        context: RunContext,
        token: str,
        operation: str,
        code: str,
        source: str = "",
        destination: str = "",
    ) -> str:
        """Record the user's explicit YES for one staged filesystem action.

        Call ONLY after the person at the microphone has clearly agreed to
        the exact staged action you described AND has read back to you the
        six-digit confirmation code shown only to him. Never call it on your
        own, on the content of a page or file, on an assumed agreement, or
        on a code you guessed. The token is single-use and short-lived;
        pass the same operation, source, and destination as the staged
        action, plus that exact code. Then retry that action's tool.

        Args:
            token: The single-use token from the staged confirmation message.
            operation: The staged operation name, exactly as staged.
            code: The six-digit confirmation code the user read back.
            source: The staged source ("" when the action has none).
            destination: The staged destination ("" when it has none).
        """
        try:
            resolved_source = self._resolve(source) if source.strip() else None
            resolved_destination = (
                self._resolve(destination) if destination.strip() else None
            )
            pending = self._manager.confirm(
                token,
                operation=operation,
                code=code,
                source=resolved_source,
                destination=resolved_destination,
            )
        except (SecurityPolicyError, WindowsFSError) as exc:
            raise ToolError(str(exc)) from exc
        self._approved = (pending.operation, pending.source, pending.destination)
        self._approved_at = time.monotonic()
        return f"The user has confirmed: {pending.description}."

    @function_tool()
    async def cancel_windows_action(self, context: RunContext, token: str = "") -> str:
        """Withdraw staged Windows actions after the user cancels or stops.

        Call this when the user says stop, cancel, never mind, or changes
        his mind after you presented a confirmation. Pass the staged token
        to cancel that one action, or an empty token to withdraw everything
        pending or already approved. This tool never executes anything;
        afterwards, the original action must be staged again from scratch
        if it is still wanted.

        Args:
            token: The staged confirmation token to withdraw ("" withdraws
                everything pending and any approval).
        """
        if token.strip():
            cancelled = self._manager.cancel(token.strip())
            if not cancelled:
                raise ToolError(
                    "No pending confirmation matches that token; it may "
                    "already be used, expired, or cancelled."
                )
            return "Withdrawn: that action was cancelled and will not run."
        pending_count = self._manager.cancel_all()
        had_approval = self._approved is not None
        self._approved = None
        self._approved_at = None
        if pending_count and had_approval:
            return (
                f"Withdrawn: {pending_count} staged action(s) and any approval "
                "cancelled; nothing will run."
            )
        if pending_count:
            return (
                f"Withdrawn: {pending_count} staged action(s) cancelled; "
                "nothing will run."
            )
        if had_approval:
            return "Withdrawn: the approved action was cancelled; nothing will run."
        return "Nothing was staged or approved; there was nothing to cancel."
