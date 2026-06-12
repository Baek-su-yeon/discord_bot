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


def _format_minutes(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return f"{hours}시간 {mins}분"


def _award_text(result: AggregateResult, award: Award, value_fmt) -> str:
    if not award.users:
        return "아직 없음"
    names = ", ".join(result.roster[uid] for uid in award.users)
    return f"{names} ({value_fmt(award.value)})"


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

    if result.roster:
        lines = []
        for uid, name in result.roster.items():
            lines.append(f"{name}: {_format_minutes(result.study_minutes[uid])}")
        embed.add_field(name="이름별 누적 출퇴근 시간", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="이름별 누적 출퇴근 시간", value="아직 없음", inline=False)

    return embed


def _format_cell(cell) -> str:
    if cell.vacation:
        return "휴가"
    if cell.checkin is not None and cell.checkout is not None:
        checkout_label = "21:00" if cell.virtual_checkout else cell.checkout.strftime("%H:%M")
        return f"{cell.checkin.strftime('%H:%M')}–{checkout_label}"
    return ""


def render_attendance_table(result: AggregateResult, output_path: str) -> str:
    """날짜별 출석부를 PNG로 렌더링하고 경로를 반환한다."""
    dates = result.dates
    users = list(result.roster.items())  # [(user_id, display_name), ...]

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
