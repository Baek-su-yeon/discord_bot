"""집계 결과 -> 임베드 텍스트 + 출석부 PNG."""

from datetime import date

import discord
from PIL import Image, ImageDraw, ImageFont

from aggregate import AggregateResult, Award

FONT_PATH = "C:/Windows/Fonts/malgun.ttf"
FONT_PATH_BOLD = "C:/Windows/Fonts/malgunbd.ttf"

CELL_WIDTH = 110
CELL_HEIGHT = 36
NAME_COL_WIDTH = 100
HEADER_HEIGHT = 36
PADDING = 10

EMBED_TOTAL_LIMIT = 6000
FIELD_VALUE_LIMIT = 1024


def _format_minutes(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return f"{hours}시간 {mins}분"


def _award_text(result: AggregateResult, award: Award, value_fmt) -> str:
    if not award.users:
        return "아직 없음"
    names = ", ".join(result.roster[uid] for uid in award.users)
    return f"{names} ({value_fmt(award.value)})"


def _visible_users(result: AggregateResult) -> list[tuple[int, str]]:
    """출석부/누적시간 "표시"에 포함할 참여자만 추려낸다.

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
    """수상 현황 + 누적 시간을 담은 임베드를 생성한다."""
    embed = discord.Embed(
        title="📊 이번 달 집계",
        description=f"{today.year}년 {today.month}월 — 현재 잠정 집계 ({today.month}/{today.day} 기준)",
        color=discord.Color.blue(),
    )

    awards = result.awards
    embed.add_field(name="🏆 개근상", value=_award_text(result, awards["perfect_attendance"], lambda v: f"{v}일 전부 출석"), inline=False)
    embed.add_field(name="📚 공부왕", value=_award_text(result, awards["study_king"], _format_minutes), inline=False)
    embed.add_field(name="🌅 출근왕", value=_award_text(result, awards["attendance_king"], lambda v: f"{v}일"), inline=False)
    embed.add_field(name="🏖️ 휴가왕", value=_award_text(result, awards["vacation_king"], lambda v: f"{v}일"), inline=False)
    embed.add_field(name="💪 운동왕", value=_award_text(result, awards["exercise_king"], lambda v: f"{v}일"), inline=False)

    visible_users = _visible_users(result)
    if not visible_users:
        embed.add_field(name="이름별 누적 출퇴근 시간", value="아직 없음", inline=False)
        return embed

    lines = [f"{name}: {_format_minutes(result.study_minutes[uid])}" for uid, name in visible_users]
    chunks = _chunk_lines(lines)
    multi = len(chunks) > 1

    for i, chunk in enumerate(chunks, start=1):
        field_name = f"이름별 누적 출퇴근 시간 ({i})" if multi else "이름별 누적 출퇴근 시간"
        if len(embed) + len(field_name) + len(chunk) > EMBED_TOTAL_LIMIT:
            embed.add_field(name="이름별 누적 출퇴근 시간 (안내)", value="참여자 수가 많아 일부는 생략되었습니다.", inline=False)
            break
        embed.add_field(name=field_name, value=chunk, inline=False)

    return embed


def _format_cell(cell) -> str:
    if cell.vacation:
        return "휴가"
    if cell.checkin is not None:
        if cell.checkout is not None:
            return f"{cell.checkin.strftime('%H:%M')}–{cell.checkout.strftime('%H:%M')}"
        if cell.virtual_checkout:
            return f"{cell.checkin.strftime('%H:%M')}–21:00"
    return ""


def render_attendance_table(result: AggregateResult, output_path: str) -> str:
    """날짜별 출석부를 PNG로 렌더링하고 경로를 반환한다."""
    dates = result.dates
    users = _visible_users(result)  # [(user_id, display_name), ...]

    cols = len(dates)
    rows = len(users)

    width = NAME_COL_WIDTH + cols * CELL_WIDTH + PADDING * 2
    height = HEADER_HEIGHT + max(rows, 1) * CELL_HEIGHT + PADDING * 2

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    font = ImageFont.truetype(FONT_PATH, 14)
    font_bold = ImageFont.truetype(FONT_PATH_BOLD, 14)

    if not dates or not users:
        draw.text((PADDING, PADDING), "이번 달 데이터가 아직 없습니다.", font=font, fill="black")
        img.save(output_path)
        return output_path

    # 헤더 (날짜)
    for c, dt in enumerate(dates):
        x = PADDING + NAME_COL_WIDTH + c * CELL_WIDTH
        label = f"{dt.month}/{dt.day}"
        draw.rectangle([x, PADDING, x + CELL_WIDTH, PADDING + HEADER_HEIGHT], outline="black")
        draw.text((x + CELL_WIDTH / 2, PADDING + HEADER_HEIGHT / 2), label, font=font_bold, fill="black", anchor="mm")

    # 모서리
    draw.rectangle([PADDING, PADDING, PADDING + NAME_COL_WIDTH, PADDING + HEADER_HEIGHT], outline="black")

    # 행
    for r, (uid, name) in enumerate(users):
        y = PADDING + HEADER_HEIGHT + r * CELL_HEIGHT
        draw.rectangle([PADDING, y, PADDING + NAME_COL_WIDTH, y + CELL_HEIGHT], outline="black")
        draw.text((PADDING + 8, y + CELL_HEIGHT / 2), name, font=font_bold, fill="black", anchor="lm")

        for c, dt in enumerate(dates):
            x = PADDING + NAME_COL_WIDTH + c * CELL_WIDTH
            draw.rectangle([x, y, x + CELL_WIDTH, y + CELL_HEIGHT], outline="black")
            cell = result.table.get(uid, {}).get(dt)
            if cell is not None:
                text = _format_cell(cell)
                draw.text((x + CELL_WIDTH / 2, y + CELL_HEIGHT / 2), text, font=font, fill="black", anchor="mm")

    img.save(output_path)
    return output_path
