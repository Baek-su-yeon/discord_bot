"""main: 봇 기동, 스케줄, catch-up, 모듈 연결."""

import datetime as dt
import logging

import discord
from discord.ext import tasks

from aggregate import aggregate
from config import (
    ATTENDANCE_CHANNEL_ID,
    DISCORD_TOKEN,
    RUN_HOUR,
    RUN_MINUTE,
    SUMMARY_POST_TAG,
    SUMMARY_POST_TITLE,
    TIMEZONE,
)
from fetch import fetch_month_data
from render import build_embed
from state import load_state, save_state

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("attendance_bot")

# 포럼 스레드 최대 자동 보관 시간(분) = 7일. 집계 게시물이 자동 보관되지 않도록 최대값 사용.
SUMMARY_POST_AUTO_ARCHIVE = 10080

intents = discord.Intents.default()
client = discord.Client(intents=intents)

_startup_done = False


def _resolve_summary_tags(channel: discord.ForumChannel) -> list[discord.ForumTag]:
    """SUMMARY_POST_TAG가 설정돼 있으면 channel.available_tags에서 일치하는 태그를 찾아 반환."""
    if not SUMMARY_POST_TAG:
        return []
    for tag in channel.available_tags:
        if tag.name == SUMMARY_POST_TAG:
            return [tag]
    logger.warning("SUMMARY_POST_TAG='%s' 태그를 채널에서 찾지 못했습니다. 태그 없이 생성합니다.", SUMMARY_POST_TAG)
    return []


async def get_or_create_summary_message(channel: discord.ForumChannel) -> discord.Message:
    """state.json을 참고해 집계 게시물의 시작 메시지를 가져오거나, 없으면 새로 만든다."""
    state = load_state()
    thread_id = state.get("thread_id")
    message_id = state.get("message_id")

    if thread_id and message_id:
        try:
            thread = channel.guild.get_channel_or_thread(thread_id) or await channel.guild.fetch_channel(thread_id)
            message = await thread.fetch_message(message_id)
            return message
        except (discord.NotFound, discord.Forbidden):
            logger.warning("저장된 집계 게시물에 접근할 수 없습니다. 새로 생성합니다.")

    result = await channel.create_thread(
        name=SUMMARY_POST_TITLE,
        content="집계를 준비하고 있습니다...",
        auto_archive_duration=SUMMARY_POST_AUTO_ARCHIVE,
        applied_tags=_resolve_summary_tags(channel),
    )
    save_state({"thread_id": result.thread.id, "message_id": result.message.id})
    return result.message


async def run_aggregation() -> None:
    """5절 전체 재계산 + 집계 게시물 수정."""
    channel = client.get_channel(ATTENDANCE_CHANNEL_ID)
    if channel is None:
        channel = await client.fetch_channel(ATTENDANCE_CHANNEL_ID)

    today = dt.datetime.now(TIMEZONE).date()

    logger.info("집계 시작: %s년 %s월 (오늘: %s)", today.year, today.month, today)

    raw = await fetch_month_data(channel, today.year, today.month)
    result = aggregate(raw, today)

    embed = build_embed(result, today)

    message = await get_or_create_summary_message(channel)

    thread = message.channel
    if getattr(thread, "archived", False):
        await thread.edit(archived=False)

    await message.edit(content="", embed=embed, attachments=[])

    logger.info("집계 완료")


@tasks.loop(time=dt.time(hour=RUN_HOUR, minute=RUN_MINUTE, tzinfo=TIMEZONE))
async def daily_aggregation() -> None:
    try:
        await run_aggregation()
    except Exception:
        logger.exception("일일 집계 중 오류가 발생했습니다. 다음 스케줄에 재시도합니다.")


@daily_aggregation.error
async def daily_aggregation_error(error: BaseException) -> None:
    logger.exception("daily_aggregation 루프에서 처리되지 않은 오류 발생", exc_info=error)
    if not daily_aggregation.is_running():
        daily_aggregation.restart()


@client.event
async def on_ready() -> None:
    global _startup_done
    logger.info("로그인됨: %s", client.user)

    if _startup_done:
        logger.info("재접속 감지 — catch-up/스케줄 시작을 건너뜁니다.")
        return
    _startup_done = True

    try:
        # 시작 시 1회 즉시 집계(catch-up) -> 21:00을 놓친 날 보정
        await run_aggregation()
    except Exception:
        logger.exception("시작 시 catch-up 집계 중 오류가 발생했습니다.")

    if not daily_aggregation.is_running():
        daily_aggregation.start()


def main() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN이 설정되지 않았습니다. .env를 확인하세요.")
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
