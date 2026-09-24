import asyncio
import contextlib
import json
import logging
import os
import sys
import threading
from collections.abc import Callable, Iterable
from typing import TextIO

from dotenv import load_dotenv
from google.genai import types as genai_types
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    TurnHandlingOptions,
    cli,
    inference,
    room_io,
)
from livekit.agents.beta.tools import EndCallTool
from livekit.plugins import ai_coustics, google

from activity import ActivityLog
from browser import BrowserManager
from confirmation import CONFIRMATION_TOPIC
from dashboard_api import DashboardServices, run_server, start_dashboard_server
from prompts import AGENT_INSTRUCTIONS
from session_history import SessionHistory, sanitize_detail
from system_tools import SystemTools
from tools import BrowserTools
from windows_tools import WindowsTools

logger = logging.getLogger(__name__)


def configure_unicode_logs(streams: Iterable[TextIO] | None = None) -> None:
    """Force UTF-8 on every stream that logging writes to.

    On Windows the standard streams default to the locale codepage (e.g.
    cp1252), which cannot encode non-Latin scripts. Logging a multilingual
    conversation item (Hindi, Tamil, Chinese, ...) then raises
    UnicodeEncodeError, the record is lost, and Python prints a
    ``--- Logging error ---`` traceback. Reconfiguring to UTF-8 with
    ``errors="replace"`` keeps every transcript loggable; unencodable lone
    surrogates degrade to ``?`` instead of crashing the handler.
    """
    default_streams = (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__)
    targets = list(streams or default_streams)

    # Also cover any handler stream logging has already been configured with
    # (root logger plus every named logger), not just the standard streams.
    for logger in [
        logging.getLogger(),
        *(
            item
            for item in logging.Logger.manager.loggerDict.values()
            if isinstance(item, logging.Logger)
        ),
    ]:
        for handler in logger.handlers:
            stream = getattr(handler, "stream", None)
            if stream is not None and stream not in targets:
                targets.append(stream)

    for stream in targets:
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        # A closed or broken stream must never prevent the agent from starting.
        with contextlib.suppress(ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")


configure_unicode_logs()

load_dotenv(".env.local")


class Assistant(Agent):
    def __init__(
        self,
        browser: BrowserManager | None = None,
        confirmation_publisher: Callable[[dict], None] | None = None,
        system_tools: SystemTools | None = None,
        history: SessionHistory | None = None,
        activity: ActivityLog | None = None,
    ) -> None:
        self.browser = browser or BrowserManager(headless=True)
        self.browser_tools = BrowserTools(
            self.browser, confirmation_publisher=confirmation_publisher
        )
        # Windows filesystem tools (Part 2): one ConfirmationManager per
        # session, held inside this instance.
        self.windows_tools = WindowsTools(
            confirmation_publisher=confirmation_publisher,
            history=history,
            activity=activity,
        )
        self.system_tools = system_tools or SystemTools(
            history=history,
            activity=activity,
        )
        self._end_call_tool = EndCallTool(
            extra_description=(
                "Only end the call after the user clearly says they are finished, "
                "says goodbye, or directly asks to end the call."
            ),
            end_instructions=(
                "Give Srilatha's brief, polite farewell to Rushi Sir in the "
                "language he has been speaking, then end the call."
            ),
        )

        super().__init__(
            # A Large Language Model (LLM) is your agent's brain, processing user input and generating a response
            # See all available models at https://docs.livekit.io/agents/models/llm/
            # llm=inference.LLM(model="google/gemma-4-31b-it"),
            llm=google.beta.realtime.RealtimeModel(
                model="gemini-3.1-flash-live-preview",
                voice="Aoede",
                # No `language=` pin: Gemini audio models auto-detect and switch
                # between all 99 supported languages (see prompts.AGENT_INSTRUCTIONS).
                tool_response_scheduling=genai_types.FunctionResponseScheduling.WHEN_IDLE,
            ),
            # To use a realtime model instead of a voice pipeline, replace the LLM
            # with a RealtimeModel and remove the STT/TTS from the AgentSession
            # (Note: This is for the OpenAI Realtime API. For other providers, see https://docs.livekit.io/agents/models/realtime/)
            # 1. Install livekit-agents[openai]
            # 2. Set OPENAI_API_KEY in .env.local
            # 3. Add `from livekit.plugins import openai` to the top of this file
            # 4. Replace the llm argument with:
            #     llm=openai.realtime.RealtimeModel(voice="marin")
            instructions=AGENT_INSTRUCTIONS,
            tools=[
                *self.browser_tools.tools,
                *self._end_call_tool.tools,
                *self.windows_tools.tools,
                *self.system_tools.tools,
            ],
        )


server = AgentServer()
_dashboard_services: DashboardServices | None = None
_dashboard_runner: object | None = None
_dashboard_autostarted = False


def _dashboard_autostart_enabled() -> bool:
    return os.environ.get("SRILATHA_DASHBOARD_AUTOSTART", "1").casefold() not in {
        "0",
        "false",
        "no",
    }


def _run_dashboard_api(app: object) -> None:
    """Serve until exit; clear the flag when the bind fails so jobs can retry."""

    global _dashboard_autostarted
    try:
        run_server(app=app)
    finally:
        _dashboard_autostarted = False


def start_dashboard_api() -> bool:
    """Boot the localhost dashboard API as soon as this worker process starts.

    ``lk agent dev``/``lk agent start`` import the entrypoint instead of
    running it as ``__main__``, and the frontend polls the API before any job
    arrives. Starting the server at import time (idempotent, non-fatal)
    removes the connection-refused window between worker boot and the first
    session; the per-job bind in ``_get_dashboard_services`` remains as a
    fallback when this boot-time bind fails.
    """

    global _dashboard_autostarted
    if _dashboard_autostarted or not _dashboard_autostart_enabled():
        return _dashboard_autostarted
    _dashboard_autostarted = True
    app = DashboardServices().app()
    threading.Thread(
        target=_run_dashboard_api,
        args=(app,),
        name="srilatha-dashboard-api",
        daemon=True,
    ).start()
    return True


async def _get_dashboard_services() -> DashboardServices:
    """Create one local API/runtime shared by this worker process.

    LiveKit may dispatch several jobs on different event loops in one worker.
    A module-level ``asyncio.Lock`` would bind itself to the first loop and
    crash later jobs, so startup is intentionally idempotent without one.
    Normally ``start_dashboard_api`` already serves from worker boot; this
    per-job bind only runs when that boot-time bind failed.
    """

    global _dashboard_runner, _dashboard_services
    if _dashboard_services is None:
        _dashboard_services = DashboardServices()
    if _dashboard_runner is None and not _dashboard_autostarted:
        _dashboard_runner = await start_dashboard_server(_dashboard_services.app())
    return _dashboard_services


@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext):
    # The job process may attach or swap log streams during bootstrap, after
    # this module was imported; re-apply so transcripts (in any language)
    # always log cleanly instead of crashing cp1252 writers.
    configure_unicode_logs()

    # Logging setup
    # Add any other context you want in all log entries here
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    services = await _get_dashboard_services()
    session_id = services.history.begin_session()
    transcript: list[tuple[str, str]] = []
    session_language: str | None = None
    history_closed = False

    def finalize_history() -> None:
        nonlocal history_closed
        if history_closed:
            return
        history_closed = True
        user_text = [text for role, text in transcript if role == "user"]
        summary = (
            "Conversation: " + " | ".join(user_text[-4:])
            if user_text
            else "Voice session"
        )
        try:
            services.history.end_session(
                session_id,
                status="completed",
                summary=sanitize_detail(summary),
                language=session_language,
            )
        except Exception:
            logger.warning("could not persist session history", exc_info=True)

    async def close_history() -> None:
        finalize_history()

    ctx.add_shutdown_callback(close_history)

    browser = BrowserManager(headless=False)
    ctx.add_shutdown_callback(browser.close)

    system_tools = SystemTools(
        history=services.history,
        activity=services.activity,
        recorder=services.recorder,
        session_id=session_id,
    )

    # Confirmation codes travel only to the user's own screen: a targeted
    # room data message on a dedicated topic, captured at session start and
    # read from this list at publish time (empty = broadcast, which is safe
    # because each room is fresh and random per token).
    identities: list[str] = []

    def publish_confirmation(payload: dict) -> None:
        """Send a confirmation code out of band; never into the model."""
        data = json.dumps(payload).encode("utf-8")

        def _on_done(task: asyncio.Task) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                logger.warning(
                    "confirmation publish failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        asyncio.create_task(
            ctx.room.local_participant.publish_data(
                data,
                reliable=True,
                destination_identities=list(identities),
                topic=CONFIRMATION_TOPIC,
            )
        ).add_done_callback(_on_done)

    # Gemini realtime handles the voice input and output for this session.
    session = AgentSession(
        # Speech-to-text (STT) is your agent's ears, turning the user's speech into text that the LLM can understand
        # See all available models at https://docs.livekit.io/agents/models/stt/
        # stt=inference.STT(model="deepgram/nova-3", language="en"),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        # tts=inference.TTS(
        #   model="fishaudio/s2.1-pro", voice="fa4c9eb3dccc4806b382b40d61c6b10a"
        # ),
        turn_handling=TurnHandlingOptions(
            # The LiveKit turn detector determines when the user is done speaking and the agent should respond.
            # TurnDetector is an end-of-turn model that listens to the user's audio directly, combining
            # semantic understanding with acoustic cues (intonation, pitch, rhythm) for state-of-the-art accuracy.
            # AgentSession supplies the required VAD automatically.
            # See https://docs.livekit.io/agents/build/turns
            turn_detection=inference.TurnDetector(),
            # Adaptive interruptions use the turn detector to tell a real interruption from a
            # backchannel like "mhm" or "right", so the agent keeps talking through the latter.
            interruption={"mode": "adaptive"},
            # allow the LLM to generate a response while waiting for the end of turn
            # See https://docs.livekit.io/agents/build/audio/#preemptive-generation
            preemptive_generation={"enabled": True},
        ),
        # Expressive mode is disabled because Gemini realtime handles the voice output.
        # expressive=True,
    )

    def record_transcription(event: object) -> None:
        nonlocal session_language
        language = getattr(event, "language", None)
        if language is not None:
            session_language = str(language)

    def record_conversation(event: object) -> None:
        item = getattr(event, "item", None)
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None)
        if not isinstance(role, str) or not isinstance(text, str) or not text.strip():
            return
        clean = sanitize_detail(text)
        transcript.append((role, clean))
        try:
            services.history.add_event(session_id, "conversation", role, clean)
        except KeyError:
            logger.debug("conversation arrived after session history closed")

    session.on("user_input_transcribed", record_transcription)
    session.on("conversation_item_added", record_conversation)
    session.on("close", lambda _event: finalize_history())

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=Assistant(
            browser,
            confirmation_publisher=publish_confirmation,
            system_tools=system_tools,
            history=services.history,
            activity=services.activity,
        ),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            video_input=True,
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_S
                ),
            ),
        ),
    )

    # Join the room and connect to the user
    await ctx.connect()
    # Session-start identities: confirmation codes go only to these devices.
    identities.extend(ctx.room.remote_participants)


# ``lk agent dev``/``lk agent start`` import this module instead of running it
# as ``__main__``, so boot the dashboard API here: it must serve before the
# first job arrives or the frontend proxy sees ECONNREFUSED.
start_dashboard_api()


if __name__ == "__main__":
    cli.run_app(server)
