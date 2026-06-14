"""영구 상태 입출력.

- state.json: 집계 게시물 ID 전용.
- sessions.json: 날짜별·유저별 음성/화면공유 세션 로그(개인정보, .gitignore 등록).

sessions.json 구조 (시각은 Unix epoch 초, 진행 중 세션은 종료 ts = null):
{
  "2026-06-15": {
    "123": {"voice": [[1718413200, 1718420400], [1718424000, null]],
            "stream": [[1718413260, 1718416800]]}
  }
}
"""

import json
import os
from datetime import date, datetime, tzinfo

from config import SESSION_FILE, STATE_FILE


# --- 집계 게시물 ID (state.json) ---


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


# --- 세션 로그 (sessions.json) ---


def load_sessions() -> dict:
    """sessions.json을 읽어 dict로 반환. 없으면 빈 dict."""
    if not os.path.exists(SESSION_FILE):
        return {}
    with open(SESSION_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_sessions(sessions: dict) -> None:
    """sessions dict를 sessions.json에 저장."""
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


def add_session_start(sessions: dict, day_key: str, user_id: int, kind: str, ts: int) -> None:
    """세션 시작 기록: [ts, None] 추가. kind = "voice" | "stream"."""
    day = sessions.setdefault(day_key, {})
    user = day.setdefault(str(user_id), {})
    user.setdefault(kind, []).append([ts, None])


def add_session_end(sessions: dict, day_key: str, user_id: int, kind: str, ts: int) -> None:
    """가장 최근의 열린(종료 None) 세션을 ts로 닫는다. 열린 세션이 없으면 무시."""
    arr = sessions.get(day_key, {}).get(str(user_id), {}).get(kind)
    if not arr:
        return
    for sess in reversed(arr):
        if sess[1] is None:
            sess[1] = ts
            return


def close_open_sessions(sessions: dict, day_key: str, cut_ts: int) -> None:
    """자정 컷: 해당 날짜의 모든 열린(None) 세션을 cut_ts(자정 ts)로 닫는다."""
    for user in sessions.get(day_key, {}).values():
        for arr in user.values():
            for sess in arr:
                if sess[1] is None:
                    sess[1] = cut_ts


def sessions_to_voice(
    sessions: dict, tz: tzinfo
) -> dict[date, dict[int, list[tuple[datetime, datetime | None]]]]:
    """sessions.json(epoch) -> aggregate용 음성 세션(tz-aware datetime).

    화면공유(stream)는 공부시간 계산에 쓰지 않으므로 voice만 변환한다.
    """
    voice: dict[date, dict[int, list[tuple[datetime, datetime | None]]]] = {}
    for day_str, users in sessions.items():
        day = date.fromisoformat(day_str)
        for uid_str, kinds in users.items():
            arr = kinds.get("voice") or []
            if not arr:
                continue
            converted: list[tuple[datetime, datetime | None]] = []
            for start_ts, end_ts in arr:
                start = datetime.fromtimestamp(start_ts, tz)
                end = datetime.fromtimestamp(end_ts, tz) if end_ts is not None else None
                converted.append((start, end))
            voice.setdefault(day, {})[int(uid_str)] = converted
    return voice
