"""Desktop UI subsystem.

The public surface — everything a caller outside this package should
need:

    from app.ui import UIController, UIState
    from app.ui.app import run_ui

``app.ui.app`` is *not* imported here, so this package can be imported on
a Python build without tkinter. The UI shell is loaded only when the
caller explicitly asks for it.
"""

from app.ui.bridge import StubResponder, UIController
from app.ui.reducer import UIReducer
from app.ui.state import (
    ConversationEntry,
    LogLevel,
    Speaker,
    StatusSnapshot,
    TaskSummary,
    UIState,
)

__all__ = [
    "ConversationEntry",
    "LogLevel",
    "Speaker",
    "StatusSnapshot",
    "StubResponder",
    "TaskSummary",
    "UIController",
    "UIReducer",
    "UIState",
]