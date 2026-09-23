import contextlib
import logging
import sys
from collections.abc import Iterable
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

from browser import BrowserManager
from prompts import AGENT_INSTRUCTIONS
from tools import BrowserTools


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
    def __init__(self, browser: BrowserManager | None = None) -> None:
        self.browser = browser or BrowserManager(headless=True)
        self.browser_tools = BrowserTools(self.browser)
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
            ],
        )


server = AgentServer()


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

    browser = BrowserManager(headless=False)
    ctx.add_shutdown_callback(browser.close)

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

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=Assistant(browser),
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


if __name__ == "__main__":
    cli.run_app(server)
