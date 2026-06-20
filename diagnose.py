"""읽기 전용 진단: 집계 게시물이 실제로 갱신되는지 디스코드 서버 상태로 확인.

봇 코드/데이터는 일절 수정하지 않는다. 로그인 -> 조회 -> 종료.
"""

import asyncio
import datetime as dt

import discord

from config import ATTENDANCE_CHANNEL_ID, DISCORD_TOKEN, TIMEZONE
from state import load_state

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


def kst(t: dt.datetime | None) -> str:
    if t is None:
        return "None"
    return t.astimezone(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def describe_embed(msg: discord.Message) -> str:
    if not msg.embeds:
        return "(임베드 없음)"
    e = msg.embeds[0]
    parts = [f"title={e.title!r}", f"desc={e.description!r}"]
    for f in e.fields:
        v = (f.value or "")[:40].replace("\n", " / ")
        parts.append(f"[{f.name}] {v}...")
    return " | ".join(parts)


async def show_message(label: str, thread, mid: int) -> None:
    try:
        msg = await thread.fetch_message(mid)
    except Exception as exc:  # noqa: BLE001
        print(f"  {label}: fetch 실패 -> {type(exc).__name__}: {exc}")
        return
    print(f"  {label}: message_id={msg.id}")
    print(f"    author={msg.author} (bot={msg.author.bot})")
    print(f"    created_at={kst(msg.created_at)}  edited_at={kst(msg.edited_at)}")
    print(f"    content={msg.content!r}")
    print(f"    embed={describe_embed(msg)}")


@client.event
async def on_ready() -> None:
    print(f"로그인됨: {client.user}\n")

    # 1) state.json 이 가리키는 대상
    state = load_state()
    tid = state.get("thread_id")
    mid = state.get("message_id")
    pe = state.get("pending_embed") or {}
    print("=== state.json ===")
    print(f"  thread_id={tid}  message_id={mid}")
    print(f"  pending_embed.description={pe.get('description')!r}\n")

    channel = client.get_channel(ATTENDANCE_CHANNEL_ID) or await client.fetch_channel(
        ATTENDANCE_CHANNEL_ID
    )
    print(f"=== 채널: {channel} (type={type(channel).__name__}) ===\n")

    # 2) 봇이 실제로 편집 대상으로 삼는 메시지 (state 기준)
    print("=== 봇 편집 대상 (state.message_id) ===")
    if tid and mid:
        try:
            thread = channel.guild.get_channel_or_thread(tid) or await channel.guild.fetch_channel(tid)
            archived = getattr(thread, "archived", None)
            locked = getattr(thread, "locked", None)
            print(f"  thread={thread.name!r} id={thread.id} archived={archived} locked={locked}")
            await show_message("starter", thread, mid)
        except Exception as exc:  # noqa: BLE001
            print(f"  thread fetch 실패 -> {type(exc).__name__}: {exc}")
    print()

    # 3) 채널의 모든 스레드(활성+보관) 중 '집계'로 보이는 것 전부 나열
    print("=== 채널 내 '집계' 관련 스레드 전수 조사 ===")
    seen = []

    async def consider(thread):
        name = thread.name or ""
        tags = {t.name for t in getattr(thread, "applied_tags", [])}
        if "집계" not in name and "📊" not in name and "집계" not in tags:
            return
        seen.append(thread.id)
        archived = getattr(thread, "archived", None)
        locked = getattr(thread, "locked", None)
        print(f"- thread id={thread.id} name={name!r}")
        print(f"    created={kst(thread.created_at)} tags={tags} archived={archived} locked={locked}")
        # 시작 메시지 = thread.id 와 동일 id
        await show_message("starter", thread, thread.id)

    for thread in channel.threads:
        await consider(thread)
    async for thread in channel.archived_threads(limit=None):
        await consider(thread)

    if not seen:
        print("  (집계로 보이는 스레드를 못 찾음)")

    await client.close()


asyncio.run(client.start(DISCORD_TOKEN))
