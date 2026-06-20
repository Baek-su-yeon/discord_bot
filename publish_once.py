"""단발성: 보관된 집계 결과(pending_embed)를 집계 게시물에 한 번만 게시.

09:00 게시(run_morning_post)가 봇 다운으로 누락된 날, 집계 수정만 수동으로
적용하기 위한 보조 스크립트. 입퇴실/휴가 게시물은 만들지 않는다(publish_summary만 호출).
"""

import asyncio

import discord

from bot import _get_channel, client, publish_summary, logger


@client.event
async def on_ready() -> None:
    try:
        logger.info("수동 집계 게시 시작: %s", client.user)
        channel = await _get_channel()
        await publish_summary(channel)
        logger.info("수동 집계 게시 종료")
    finally:
        await client.close()


from config import DISCORD_TOKEN  # noqa: E402

asyncio.run(client.start(DISCORD_TOKEN))
