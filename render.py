"""집계 결과 -> 임베드 텍스트."""

from datetime import date

import discord

from aggregate import AggregateResult

EMBED_TOTAL_LIMIT = 6000
FIELD_VALUE_LIMIT = 1024


def _format_hours(minutes: int) -> str:
    return f"{round(minutes / 60)}시간"


def _visible_users(result: AggregateResult) -> list[tuple[int, str]]:
    """누적 시간 "표시"에 포함할 참여자만 추려낸다.

    출석부 셀이 하나도 없고 누적 공부시간도 0인 사람(예: 운동만 참여)은 표시에서 제외한다.
    roster/awards 계산에는 영향을 주지 않는다.
    """
    return [
        (uid, name)
        for uid, name in result.roster.items()
        if result.table.get(uid) or result.study_minutes.get(uid, 0) > 0
    ]


def _chunk_lines(lines: list[str], limit: int = FIELD_VALUE_LIMIT) -> list[str]:
    """줄들을 임베드 필드 value 1024자 제한을 넘지 않는 묶음들로 나눈다."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1  # 줄바꿈 포함
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def build_embed(result: AggregateResult, today: date) -> discord.Embed:
    """오늘 출근 현황 + 누적 시간을 담은 임베드를 생성한다."""
    embed = discord.Embed(
        title="📊 이번 달 집계",
        description=f"{today.year}년 {today.month}월 — 현재 잠정 집계 ({today.month}/{today.day} 기준)",
        color=discord.Color.blue(),
    )

    if result.today_checkins:
        lines = [
            f"{name} ({entry_time.strftime('%H:%M')})"
            for name, entry_time in sorted(result.today_checkins.values(), key=lambda v: v[1])
        ]
        embed.add_field(name="✅ 오늘 출근", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="✅ 오늘 출근", value="아직 없음", inline=False)

    visible_users = _visible_users(result)
    if not visible_users:
        embed.add_field(name="이름별 누적 출퇴근 시간", value="아직 없음", inline=False)
        return embed

    visible_users = sorted(visible_users, key=lambda u: result.study_minutes[u[0]], reverse=True)
    lines = [f"{name}: {_format_hours(result.study_minutes[uid])}" for uid, name in visible_users]
    chunks = _chunk_lines(lines)
    multi = len(chunks) > 1

    for i, chunk in enumerate(chunks, start=1):
        field_name = f"이름별 누적 출퇴근 시간 ({i})" if multi else "이름별 누적 출퇴근 시간"
        if len(embed) + len(field_name) + len(chunk) > EMBED_TOTAL_LIMIT:
            embed.add_field(name="이름별 누적 출퇴근 시간 (안내)", value="참여자 수가 많아 일부는 생략되었습니다.", inline=False)
            break
        embed.add_field(name=field_name, value=chunk, inline=False)

    return embed
