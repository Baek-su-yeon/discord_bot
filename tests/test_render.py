from datetime import date, datetime
from zoneinfo import ZoneInfo

from aggregate import AttendanceLog, RawData, aggregate
from render import _format_hours, build_embed

KST = ZoneInfo("Asia/Seoul")


def dt(y, m, d, h, mi):
    return datetime(y, m, d, h, mi, tzinfo=KST)


def att(name, *events):
    """AttendanceLog 헬퍼: events = (datetime, "in"/"out") ..."""
    return AttendanceLog(name=name, events=list(events))


def test_format_hours_rounds_and_drops_minutes():
    assert _format_hours(0) == "0시간"
    assert _format_hours(89) == "1시간"  # 1시간 29분 -> 1시간
    assert _format_hours(91) == "2시간"  # 1시간 31분 -> 2시간
    assert _format_hours(120) == "2시간"


def test_build_embed_today_checkins_field():
    raw = RawData(
        attendance={
            date(2026, 6, 1): {
                1: att("alice", (dt(2026, 6, 1, 9, 30), "in")),
                2: att("bob", (dt(2026, 6, 1, 9, 0), "in")),
            }
        },
    )
    result = aggregate(raw, today=date(2026, 6, 1))
    embed = build_embed(result, date(2026, 6, 1))

    field = next(f for f in embed.fields if f.name == "✅ 오늘 출근")
    # 입실 시각 순으로 정렬
    assert field.value == "bob (09:00)\nalice (09:30)"


def test_build_embed_no_checkins_today():
    raw = RawData()
    result = aggregate(raw, today=date(2026, 6, 1))
    embed = build_embed(result, date(2026, 6, 1))

    field = next(f for f in embed.fields if f.name == "✅ 오늘 출근")
    assert field.value == "아직 없음"


def test_build_embed_cumulative_hours_sorted_descending():
    raw = RawData(
        attendance={
            date(2026, 6, 1): {
                1: att("alice", (dt(2026, 6, 1, 9, 0), "in"), (dt(2026, 6, 1, 13, 0), "out")),  # 4h-1h=3h
                2: att("bob", (dt(2026, 6, 1, 9, 0), "in"), (dt(2026, 6, 1, 19, 0), "out")),  # 10h-1h=9h
                3: att("carol", (dt(2026, 6, 1, 9, 0), "in"), (dt(2026, 6, 1, 16, 0), "out")),  # 7h-1h=6h
            }
        },
    )
    result = aggregate(raw, today=date(2026, 6, 1))
    embed = build_embed(result, date(2026, 6, 1))

    field = next(f for f in embed.fields if f.name == "이름별 누적 출퇴근 시간")
    assert field.value == "bob: 9시간\ncarol: 6시간\nalice: 3시간"


def test_build_embed_does_not_show_awards():
    raw = RawData(
        attendance={
            date(2026, 6, 1): {1: att("alice", (dt(2026, 6, 1, 9, 0), "in"), (dt(2026, 6, 1, 18, 0), "out"))}
        },
    )
    result = aggregate(raw, today=date(2026, 6, 1))
    embed = build_embed(result, date(2026, 6, 1))

    field_names = [f.name for f in embed.fields]
    assert not any("왕" in name or "개근" in name for name in field_names)


def test_build_embed_cumulative_hours_only():
    raw = RawData(
        attendance={
            date(2026, 6, 1): {1: att("alice", (dt(2026, 6, 1, 9, 0), "in"), (dt(2026, 6, 1, 18, 0), "out"))}
        },
    )
    result = aggregate(raw, today=date(2026, 6, 1))
    embed = build_embed(result, date(2026, 6, 1))

    field = next(f for f in embed.fields if f.name == "이름별 누적 출퇴근 시간")
    # 09:00-18:00 = 540분 - 60분(식사) = 480분 = 8시간
    assert field.value == "alice: 8시간"
