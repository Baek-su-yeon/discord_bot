"""main: 봇 기동, 스케줄(자정 집계 / 09:00 게시), 음성 이벤트, 화면공유 알림, 모듈 연결."""

import argparse
import asyncio
import datetime as dt
import logging
import sys
from logging.handlers import RotatingFileHandler

import discord
from discord.ext import tasks

from aggregate import aggregate
from config import (
    AGGREGATE_HOUR,
    AGGREGATE_MINUTE,
    ATTENDANCE_CHANNEL_ID,
    AWARD_EXCLUDED_IDS,
    AWARD_EXCLUDED_NAMES,
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

# 로깅: 콘솔 + 파일(bot.log) 동시 출력. RotatingFileHandler로 자동 로테이션.
# 백그라운드(작업 스케줄러) 실행 시 콘솔이 안 보이므로 bot.log가 유일한 관찰 창구.
LOG_FILE = "bot.log"
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5MB
LOG_BACKUP_COUNT = 5

_log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
)
logging.basicConfig(
    level=logging.INFO,
    format=_log_format,
    handlers=[logging.StreamHandler(), _file_handler],
)
logger = logging.getLogger("attendance_bot")


def _log_uncaught(exc_type, exc_value, exc_tb) -> None:
    """미처리 예외 traceback을 로그(콘솔+파일)에 남긴다. Ctrl+C는 기본 동작 유지."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    logger.critical("처리되지 않은 예외로 종료합니다.", exc_info=(exc_type, exc_value, exc_tb))


sys.excepthook = _log_uncaught

# 포럼 스레드 최대 자동 보관 시간(분) = 7일. 집계 게시물이 자동 보관되지 않도록 최대값 사용.
SUMMARY_POST_AUTO_ARCHIVE = 10080

# 화면공유 알림: 음성 입장(또는 공유 종료) 후 이 시간만큼 공유가 없으면 알림. 켤 때까지 반복.
STREAM_ALERT_INTERVAL_SECONDS = 3600  # 1시간
# 알림 발송 허용 시간대 (KST): 09:00 이상 18:00 미만. 18:00 이후엔 발송하지 않음.
ALERT_WINDOW_START_HOUR = 9
ALERT_WINDOW_END_HOUR = 18

# message_content: 입퇴실 댓글의 "입실"/"퇴실" 문자열 판별에 필요.
# Discord Developer Portal > Bot > Privileged Gateway Intents 에서
# "MESSAGE CONTENT INTENT"도 함께 켜야 한다(봇 100서버 미만이면 심사 없이 토글 가능).
# 음성 상태(on_voice_state_update)와 self_stream은 Intents.default()에 이미 포함된다.
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

_startup_done = False

# 실행 플래그 (main의 argparse에서 설정). 기본값 = 둘 다 활성(인자 없이 실행하면 현행 동작 유지).
# --no-summary: 09:00 집계 결과 "게시(공개 갱신)"만 생략. 입퇴실/휴가 게시물 생성과
#   집계 계산·보관(state.json의 pending_embed)은 그대로 유지된다.
# --no-stream-alert: 화면공유 1시간 미사용 알림만 끈다. 세션 기록은 그대로 유지된다.
_summary_publish_enabled = True
_stream_alert_enabled = True

# 화면공유 알림 타이머 (메모리 전용, 영구 저장 안 함). user_id -> asyncio.Task
_stream_alert_tasks: dict[int, asyncio.Task] = {}


def _within_alert_window(now: dt.datetime) -> bool:
    """알림 발송 허용 시간대(09:00~18:00) 여부."""
    return ALERT_WINDOW_START_HOUR <= now.hour < ALERT_WINDOW_END_HOUR


def _cancel_stream_alert(user_id: int) -> None:
    """해당 유저의 화면공유 알림 타이머를 해제."""
    task = _stream_alert_tasks.pop(user_id, None)
    if task is not None:
        task.cancel()


def _start_stream_alert(member: discord.Member) -> None:
    """해당 유저의 화면공유 알림 타이머를 (재)시작. --no-stream-alert면 시작하지 않는다."""
    if not _stream_alert_enabled:
        return
    _cancel_stream_alert(member.id)
    _stream_alert_tasks[member.id] = asyncio.create_task(_stream_alert_loop(member))


async def _stream_alert_loop(member: discord.Member) -> None:
    """1시간마다, 여전히 음성에 있고 공유를 안 켰으면 채널에서 @멘션. 09~18시에만 발송."""
    try:
        while True:
            await asyncio.sleep(STREAM_ALERT_INTERVAL_SECONDS)

            voice = member.voice
            if voice is None or voice.channel is None:
                return  # 음성 퇴장 -> 종료
            if voice.self_stream:
                return  # 공유 켜짐 -> 종료

            if not _within_alert_window(dt.datetime.now(TIMEZONE)):
                continue  # 시간대 밖 -> 이번 주기는 발송 생략, 다음 주기 대기

            try:
                await voice.channel.send(
                    f"{member.mention} 화면 공유가 꺼져 있어요. 확인해주세요 🙏"
                )
            except discord.HTTPException:
                logger.exception("화면공유 알림 전송 실패")
    except asyncio.CancelledError:
        pass


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


async def _ensure_daily_post(
    channel: discord.ForumChannel,
    state: dict,
    state_key: str,
    tag_name: str,
    title: str,
    content: str,
    today_iso: str,
) -> bool:
    """오늘 아직 안 만들었으면 일일 게시물을 생성하고 state에 기록. 생성했으면 True.

    봇 재시작/루프 재시작 등으로 09:00 작업이 같은 날 두 번 돌아도 중복 생성하지 않도록
    state[state_key]에 마지막 생성 날짜를 저장해 가드한다. (post 타입별 개별 가드)
    """
    if state.get(state_key) == today_iso:
        logger.info("오늘 이미 생성됨, 건너뜀: %s", title)
        return False
    await create_daily_post(channel, tag_name, title, content)
    state[state_key] = today_iso
    return True


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
    result = aggregate(raw, target_day, voice, AWARD_EXCLUDED_IDS, AWARD_EXCLUDED_NAMES)
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
    """09:00: 입퇴실 게시물 -> 휴가 게시물 -> 집계 결과 게시물 (이 순서).

    입퇴실/휴가 게시물은 오늘 이미 생성했으면 건너뛴다(중복 생성 가드).
    집계 결과는 동일 메시지를 수정하는 멱등 동작이라 항상 게시한다.
    """
    channel = await _get_channel()
    today = dt.datetime.now(TIMEZONE).date()
    today_iso = today.isoformat()

    state = load_state()
    created = False
    # 1) 입퇴실 게시물
    created |= await _ensure_daily_post(
        channel, state, "last_attendance_post", TAG_ATTENDANCE,
        f"{today.month}월 {today.day}일 입퇴실",
        "오늘의 입실/퇴실을 댓글로 남겨주세요. (예: 입실, 퇴실)", today_iso,
    )
    # 2) 휴가 게시물
    created |= await _ensure_daily_post(
        channel, state, "last_vacation_post", TAG_VACATION,
        f"{today.month}월 {today.day}일 휴가",
        "오늘 휴가라면 댓글로 남겨주세요.", today_iso,
    )
    if created:
        save_state(state)

    # 3) 집계 결과 게시물 (전날 자정에 확정한 결과)
    # --no-summary면 공개 게시만 생략. 집계 계산·보관(pending_embed)은 자정/시작 catch-up에서 계속된다.
    if _summary_publish_enabled:
        await publish_summary(channel)
    else:
        logger.info("집계 결과 게시 비활성화(--no-summary): 공개 게시 생략 (결과는 pending_embed에 보관)")


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

        # 화면공유 알림 타이머 관리 (메모리)
        stream_started = after.self_stream and not before.self_stream
        stream_stopped = before.self_stream and not after.self_stream
        joined = now_in and not was_in
        left = was_in and not now_in

        if left or (stream_started and now_in):
            # 음성 퇴장 또는 공유 시작 -> 타이머 해제
            _cancel_stream_alert(uid)
        elif (joined and not after.self_stream) or (stream_stopped and now_in):
            # 공유 없이 입장 또는 공유 종료(여전히 음성) -> 1시간 타이머 (재)시작
            _start_stream_alert(member)
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
    global _summary_publish_enabled, _stream_alert_enabled
    parser = argparse.ArgumentParser(
        description="디스코드 출퇴근 집계 봇. 인자 없이 실행하면 모든 기능이 켜진 채로 동작한다."
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="매일 09:00 집계 결과의 '공개 게시(갱신)'를 끈다. "
        "입퇴실/휴가 게시물 생성과 집계 계산·보관(state.json의 pending_embed)은 그대로 유지된다.",
    )
    parser.add_argument(
        "--no-stream-alert",
        action="store_true",
        help="화면공유(라이브) 1시간 미사용 알림을 끈다. 음성/화면공유 세션 기록은 그대로 유지된다.",
    )
    args = parser.parse_args()
    _summary_publish_enabled = not args.no_summary
    _stream_alert_enabled = not args.no_stream_alert

    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN이 설정되지 않았습니다. .env를 확인하세요.")

    logger.info(
        "기능 상태 — 집계 결과 게시: %s, 화면공유 알림: %s",
        "ON" if _summary_publish_enabled else "OFF",
        "ON" if _stream_alert_enabled else "OFF",
    )
    # log_handler=None: discord.py 자체 로깅 설정을 끄고, discord 로거가 위에서 구성한
    # 루트 핸들러(콘솔 + bot.log)로 전파되게 한다.
    client.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
