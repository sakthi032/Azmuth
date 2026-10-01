"""Widget components for the Azmuth desktop UI.

Every class here requires tkinter. The rest of ``app.ui`` (state,
reducer, bridge) does not — that is what keeps the interesting logic
testable without a display.
"""

from app.ui.components.input_row import InputRow
from app.ui.components.status_bar import StatusBar
from app.ui.components.transcript import TranscriptView

__all__ = ["InputRow", "StatusBar", "TranscriptView"]