from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class ProcessingStates(StatesGroup):
    idle = State()
    waiting_video = State()
    analyzing = State()
    waiting_clip_count = State()
    rendering = State()
