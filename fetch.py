"""디스코드에서 이번 달 게시물·댓글 수집 (활성 + 보관)."""

from datetime import datetime

import discord

from aggregate import AttendanceLog, RawData
from config import (
    KEYWORD_CHECK_IN,
    KEYWORD_CHECK_OUT,
    TAG_ATTENDANCE,
    TAG_EXERCISE,
    TIMEZONE,
)

# 태그 이름 -> RawData 필드명. 입퇴실/운동만 댓글 수집 대상.
# 휴가는 "입실 안 한 날 = 자동 휴가"로 aggregate에서 도출하므로 댓글을 수집하지 않는다.
TAG_TO_FIELD = {
    TAG_ATTENDANCE: "attendance",
    TAG_EXERCISE: "exercise",
}


async def _iter_all_threads(channel: discord.ForumChannel):
    """활성 + 보관 스레드를 모두 순회."""
    for thread in channel.threads:
        yield thread
    async for thread in channel.archived_threads(limit=None):
        yield thread


def _thread_type(thread: discord.Thread) -> str | None:
    """스레드 태그로 게시물 타입(attendance/exercise)을 판별. 인식 못하면 None.

    옛 출근/퇴근 태그나 휴가 태그는 None -> 수집에서 제외된다.
    """
    tag_names = {tag.name for tag in thread.applied_tags}
    for tag_name, field_name in TAG_TO_FIELD.items():
        if tag_name in tag_names:
            return field_name
    return None


async def _collect_attendance(thread: discord.Thread) -> dict[int, AttendanceLog]:
    """입퇴실 게시물: 유저별 모든 댓글을 시간순으로 모아 입실/퇴실 이벤트로 분류.

    - 매칭은 부분 포함("입실" in content, "퇴실" in content). 정확 일치 아님.
    - 입실/퇴실 키워드가 없는 댓글은 무시.
    - 다중 세션 페어링은 aggregate에서 처리하므로 여기서는 시간순 이벤트만 보존.
    """
    logs: dict[int, AttendanceLog] = {}
    async for msg in thread.history(limit=None, oldest_first=True):
        if msg.id == thread.id:
            continue  # 시작 메시지(본문)는 댓글에서 제외
        content = msg.content or ""
        is_in = KEYWORD_CHECK_IN in content
        is_out = KEYWORD_CHECK_OUT in content
        if not (is_in or is_out):
            continue
        uid = msg.author.id
        t = msg.created_at.astimezone(TIMEZONE)
        log = logs.get(uid)
        if log is None:
            log = AttendanceLog(name=msg.author.display_name)
            logs[uid] = log
        if is_in:
            log.events.append((t, "in"))
        if is_out:
            log.events.append((t, "out"))

    for log in logs.values():
        log.events.sort(key=lambda e: e[0])
    return logs


async def _collect_first_comments(thread: discord.Thread) -> dict[int, tuple[str, datetime]]:
    """스레드 내 댓글(시작 메시지 제외)을 순회해 유저별 최초 댓글 정보를 수집(운동용)."""
    entries: dict[int, tuple[str, datetime]] = {}
    async for msg in thread.history(limit=None, oldest_first=True):
        if msg.id == thread.id:
            continue
        uid = msg.author.id
        if uid in entries:
            continue  # 최초 댓글만 사용
        entries[uid] = (msg.author.display_name, msg.created_at.astimezone(TIMEZONE))
    return entries


async def fetch_month_data(channel: discord.ForumChannel, year: int, month: int) -> RawData:
    """이번 달(year, month) 입퇴실/운동 게시물의 원자료를 수집한다."""
    raw = RawData()

    async for thread in _iter_all_threads(channel):
        field_name = _thread_type(thread)
        if field_name is None:
            continue

        created_kst = thread.created_at.astimezone(TIMEZONE)
        post_date = created_kst.date()
        if post_date.year != year or post_date.month != month:
            continue

        if field_name == "attendance":
            raw.attendance[post_date] = await _collect_attendance(thread)
        elif field_name == "exercise":
            raw.exercise[post_date] = await _collect_first_comments(thread)

    return raw
