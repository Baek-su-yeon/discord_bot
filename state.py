"""state.json 읽기/쓰기. 유일한 영구 상태 = 집계 게시물 ID."""

import json
import os

from config import STATE_FILE


def load_state() -> dict:
    """state.json을 읽어 dict로 반환. 없으면 빈 dict."""
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    """state dict를 state.json에 저장."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
