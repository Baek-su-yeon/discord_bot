"""main: 봇 기동, 스케줄(자정 집계 / 09:00 게시), 음성 이벤트, 모듈 연결."""

import datetime as dt
import logging

import discord
from discord.ext import tasks

from aggregate import aggregate
from config import (
    AGGREGATE_HOUR,
    AGGREGATE_MINUTE,
    ATTENDANCE_CHANNEL_ID,
    DISCORD_TOKEN,
    POST_HOUR,
    POST_MINUTE,
    SUMMARY_POST_TAG,
    SUMMARY_POST_TITLE,
    TAG_ATTENDANCE,
    TAG_VACATION,
    TIMEZONE,
)
from fetch import fetch_month_data
from render import build_embed
from state import (
    add_session_end,
    add_session_start,
    close_open_sessions,
    load_sessions,
    load_state,
    save_sessions,
    save_state,
    sessions_to_voice,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("attendance_bot")

# 포럼 스레드 최대 자동 보관 시간(분) = 7일. 집계 게시물이 자동 보관되지 않도록 최대값 사용.
SUMMARY_POST_AUTO_ARCHIVE = 10080

# message_content: 입퇴실 댓글의 "입실"/"퇴실" 문자열 판별에 필요.
# Discord Developer Portal > Bot > Privileged Gateway Intents 에서
# "MESSAGE CONTENT INTENT"도 함께 켜야 한다(봇 100서버 미만이면 심사 없이 토글 가능).
# 음성 상태(on_voice_state_update)와 self_stream은 Intents.default()에 이미 포함된다.
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

_startup_done = False


async def _get_channel() -> discord.ForumChannel:
    """집계 대상 포럼 채널을 가져온다."""
    channel = client.get_channel(ATTENDANCE_CHANNEL_ID)
    if channel is None:
        channel = await client.fetch_channel(ATTENDANCE_CHANNEL_ID)
    return channel


def _resolve_tags(channel: discord.ForumChannel, tag_name: str | None) -> list[discord.ForumTag]:
    """tag_name과 일치하는 포럼 태그를 channel.available_tags에서 찾아 반환. 없으면 빈 리스트."""
    if not tag_name:
        return []
    for tag in channel.available_tags:
        if tag.name == tag_name:
            return [tag]
    logger.warning("태그 '%s'를 채널에서 찾지 못했습니다. 태그 없이 생성합니다.", tag_name)
    return []


def _aggregate_target_day(now: dt.datetime) -> dt.date:
    """집계 대상 날짜 = 방금 끝난 전날. (현재 날짜는 아직 진행 중이라 미완성)"""
    return now.date() - dt.timedelta(days=1)


async def create_daily_post(
    channel: discord.ForumChannel, tag_name: str, title: str, content: str
) -> None:
    """태그가 달린 일일 게시물(스레드)을 새로 생성한다."""
    await channel.create_thread(
        name=title,
        content=content,
        applied_tags=_resolve_tags(channel, tag_name),
    )
    logger.info("게시물 생성: %s", title)


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
        applied_tags=_resolve_tags(channel, SUMMARY_POST_TAG),
    )
    # 집계 게시물 ID만 보존하고, 보관해 둔 임베드(pending_embed)는 유지한다.
    state["thread_id"] = result.thread.id
    state["message_id"] = result.message.id
    save_state(state)
    return result.message


async def _compute_summary_embed(channel: discord.ForumChannel, target_day: dt.date) -> discord.Embed:
    """target_day 기준 그달 전체를 집계해 임베드를 만든다(음성 우선 + 댓글 폴백)."""
    raw = await fetch_month_data(channel, target_day.year, target_day.month)
    voice = sessions_to_voice(load_sessions(), TIMEZONE)
    result = aggregate(raw, target_day, voice)
    return build_embed(result, target_day)


async def run_midnight_aggregation() -> None:
    """자정: 전날 진행 중 세션을 자정에서 끊고(자정 컷) 집계해 결과 임베드를 보관."""
    channel = await _get_channel()
    now = dt.datetime.now(TIMEZONE)
    target_day = _aggregate_target_day(now)

    # 자정 컷: target_day의 열린(None) 세션을 target_day의 자정(=다음날 00:00) ts로 닫는다.
    cut_dt = dt.datetime.combine(target_day + dt.timedelta(days=1), dt.time(0, 0), tzinfo=TIMEZONE)
    sessions = load_sessions()
    close_open_sessions(sessions, target_day.isoformat(), int(cut_dt.timestamp()))
    save_sessions(sessions)

    logger.info("자정 집계 시작: %s (자정 컷 완료)", target_day)

    embed = await _compute_summary_embed(channel, target_day)

    state = load_state()
    state["pending_embed"] = embed.to_dict()
    save_state(state)

    logger.info("자정 집계 완료: %s (결과 보관)", target_day)


async def publish_summary(channel: discord.ForumChannel) -> None:
    """보관해 둔(자정 집계) 임베드를 집계 게시물에 게시. 없으면 즉시 재계산해 게시."""
    state = load_state()
    embed_dict = state.get("pending_embed")
    if embed_dict:
        embed = discord.Embed.from_dict(embed_dict)
    else:
        target_day = _aggregate_target_day(dt.datetime.now(TIMEZONE))
        logger.warning("보관된 집계 결과가 없어 즉시 재계산합니다: %s", target_day)
        embed = await _compute_summary_embed(channel, target_day)

    message = await get_or_create_summary_message(channel)
    thread = message.channel
    if getattr(thread, "archived", False):
        await thread.edit(archived=False)
    await message.edit(content="", embed=embed, attachments=[])
    logger.info("집계 결과 게시 완료")


async def run_morning_post() -> None:
    """09:00: 입퇴실 게시물 -> 휴가 게시물 -> 집계 결과 게시물 (이 순서)."""
    channel = await _get_channel()
    today = dt.datetime.now(TIMEZONE).date()

    # 1) 입퇴실 게시물
    await create_daily_post(
        channel,
        TAG_ATTENDANCE,
        f"{today.month}월 {today.day}일 입퇴실",
        "오늘의 입실/퇴실을 댓글로 남겨주세요. (예: 입실, 퇴실)",
    )
    # 2) 휴가 게시물
    await create_daily_post(
        channel,
        TAG_VACATION,
        f"{today.month}월 {today.day}일 휴가",
        "오늘 휴가라면 댓글로 남겨주세요.",
    )
    # 3) 집계 결과 게시물 (전날 자정에 확정한 결과)
    await publish_summary(channel)


@tasks.loop(time=dt.time(hour=AGGREGATE_HOUR, minute=AGGREGATE_MINUTE, tzinfo=TIMEZONE))
async def midnight_aggregation() -> None:
    try:
        await run_midnight_aggregation()
    except Exception:
        logger.exception("자정 집계 중 오류가 발생했습니다. 다음 스케줄에 재시도합니다.")


@midnight_aggregation.error
async def midnight_aggregation_error(error: BaseException) -> None:
    logger.exception("midnight_aggregation 루프에서 처리되지 않은 오류 발생", exc_info=error)
    if not midnight_aggregation.is_running():
        midnight_aggregation.restart()


@tasks.loop(time=dt.time(hour=POST_HOUR, minute=POST_MINUTE, tzinfo=TIMEZONE))
async def morning_post() -> None:
    try:
        await run_morning_post()
    except Exception:
        logger.exception("09:00 게시 중 오류가 발생했습니다. 다음 스케줄에 재시도합니다.")


@morning_post.error
async def morning_post_error(error: BaseException) -> None:
    logger.exception("morning_post 루프에서 처리되지 않은 오류 발생", exc_info=error)
    if not morning_post.is_running():
        morning_post.restart()


@client.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    """음성 입장/퇴장 및 화면공유 변화를 sessions.json에 즉시 기록.

    - 입장(채널 None -> 채널): voice 세션 시작.
    - 퇴장(채널 -> None): voice 세션 종료(+ 공유 중이었으면 stream도 종료).
    - 채널 이동(A -> B): voice 체류는 끊기지 않으므로 기록 변화 없음.
    - 화면공유 시작/종료: stream 세션 시작/종료.
    """
    try:
        now = dt.datetime.now(TIMEZONE)
        ts = int(now.timestamp())
        day_key = now.date().isoformat()
        uid = member.id

        was_in = before.channel is not None
        now_in = after.channel is not None

        sessions = load_sessions()
        changed = False

        if now_in and not was_in:
            add_session_start(sessions, day_key, uid, "voice", ts)
            changed = True
        elif was_in and not now_in:
            add_session_end(sessions, day_key, uid, "voice", ts)
            if before.self_stream:
                add_session_end(sessions, day_key, uid, "stream", ts)
            changed = True

        # 화면공유 상태 변화 (퇴장으로 인한 종료는 위에서 이미 처리)
        if after.self_stream and not before.self_stream:
            add_session_start(sessions, day_key, uid, "stream", ts)
            changed = True
        elif before.self_stream and not after.self_stream and now_in:
            add_session_end(sessions, day_key, uid, "stream", ts)
            changed = True

        if changed:
            save_sessions(sessions)
    except Exception:
        logger.exception("음성 상태 처리 중 오류가 발생했습니다.")


@client.event
async def on_ready() -> None:
    global _startup_done
    logger.info("로그인됨: %s", client.user)

    if _startup_done:
        logger.info("재접속 감지 — catch-up/스케줄 시작을 건너뜁니다.")
        return
    _startup_done = True

    # 시작 시 1회 집계 결과 갱신(catch-up). 게시물 생성은 09:00 스케줄에만 맡겨
    # 중복 생성을 피한다. 자정 컷은 자정 스케줄에서만 수행한다.
    try:
        channel = await _get_channel()
        target_day = _aggregate_target_day(dt.datetime.now(TIMEZONE))
        embed = await _compute_summary_embed(channel, target_day)
        state = load_state()
        state["pending_embed"] = embed.to_dict()
        save_state(state)
        logger.info("시작 catch-up: 집계 결과 보관 완료 (%s)", target_day)
    except Exception:
        logger.exception("시작 시 catch-up 집계 중 오류가 발생했습니다.")

    if not midnight_aggregation.is_running():
        midnight_aggregation.start()
    if not morning_post.is_running():
        morning_post.start()


def main() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN이 설정되지 않았습니다. .env를 확인하세요.")
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
