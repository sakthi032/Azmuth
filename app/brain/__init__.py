"""Brain subsystem public API.

    from app.brain import Azmuth, build_azmuth, Brain, BrainResponse

The :class:`Azmuth` object is the top-level facade. Everything the UI
and tests need goes through it.
"""

from app.brain.azmuth import Azmuth, build_azmuth
from app.brain.pipeline import Brain, BrainResponse, ToolExecutionRecord
from app.brain.prompts import build_system_prompt
from app.brain.tool_calls import ToolCall, parse_tool_calls
from app.brain.purpose import detect_purpose
__all__ = [
    "Azmuth",
    "Brain",
    "BrainResponse",
    "ToolCall",
    "ToolExecutionRecord",
    "build_azmuth",
    "build_system_prompt",
    "parse_tool_calls",
    "detect_purpose",
]