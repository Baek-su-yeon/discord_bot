"""디스코드에서 이번 달 게시물·댓글 수집 (활성 + 보관)."""

from datetime import date, datetime

import discord

from aggregate import RawData, TypeData
from config import TAG_CHECK_IN, TAG_CHECK_OUT, TAG_EXERCISE, TAG_VACATION, TIMEZONE

TAG_TO_FIELD = {
    TAG_CHECK_IN: "checkin",
    TAG_CHECK_OUT: "checkout",
    TAG_VACATION: "vacation",
    TAG_EXERCISE: "exercise",
}


async def _iter_all_threads(channel: discord.ForumChannel):
    """활성 + 보관 스레드를 모두 순회."""
    for thread in channel.threads:
        yield thread
    async for thread in channel.archived_threads(limit=None):
        yield thread


def _thread_type(channel: discord.ForumChannel, thread: discord.Thread) -> str | None:
    """스레드에 적용된 태그로 게시물 타입(checkin/checkout/vacation/exercise)을 판별. 없으면 None."""
    tag_names = {tag.name for tag in thread.applied_tags}
    for tag_name, field_name in TAG_TO_FIELD.items():
        if tag_name in tag_names:
            return field_name
    return None


async def _collect_first_comments(thread: discord.Thread) -> dict[int, tuple[str, datetime]]:
    """스레드 내 댓글(시작 메시지 제외)을 순회해 유저별 최초 댓글 정보를 수집."""
    entries: dict[int, tuple[str, datetime]] = {}
    async for msg in thread.history(limit=None, oldest_first=True):
        if msg.id == thread.id:
            continue  # 시작 메시지(본문)는 댓글에서 제외
        uid = msg.author.id
        if uid in entries:
            continue  # 최초 댓글만 사용
        entries[uid] = (msg.author.display_name, msg.created_at.astimezone(TIMEZONE))
    return entries


async def fetch_month_data(channel: discord.ForumChannel, year: int, month: int) -> RawData:
    """이번 달(year, month) 출근/퇴근/휴가/운동 게시물의 원자료를 수집한다."""
    raw = RawData()

    async for thread in _iter_all_threads(channel):
        field_name = _thread_type(channel, thread)
        if field_name is None:
            continue

        created_kst = thread.created_at.astimezone(TIMEZONE)
        post_date = created_kst.date()
        if post_date.year != year or post_date.month != month:
            continue

        entries = await _collect_first_comments(thread)

        type_data: TypeData = getattr(raw, field_name)
        type_data[post_date] = entries

    return raw
