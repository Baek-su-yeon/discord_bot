from datetime import date, datetime
from zoneinfo import ZoneInfo

from aggregate import RawData, aggregate

KST = ZoneInfo("Asia/Seoul")


def dt(y, m, d, h, mi):
    return datetime(y, m, d, h, mi, tzinfo=KST)


def test_basic_attendance_and_study_time():
    # 2026-06-01 (월), 2026-06-02 (화)
    raw = RawData(
        checkin={
            date(2026, 6, 1): {1: ("alice", dt(2026, 6, 1, 9, 0))},
            date(2026, 6, 2): {1: ("alice", dt(2026, 6, 2, 9, 0))},
        },
        checkout={
            date(2026, 6, 1): {1: ("alice", dt(2026, 6, 1, 18, 0))},
            # 6/2 퇴근 게시물 없음 -> D에서 제외
        },
    )
    result = aggregate(raw, today=date(2026, 6, 2))

    assert result.dates == [date(2026, 6, 1)]
    assert result.weekday_dates == [date(2026, 6, 1)]
    assert result.roster == {1: "alice"}

    # 6/1: 09:00-18:00 = 540분, 6/2: 출근만 -> 가상 퇴근 21:00 = 720분
    assert result.study_minutes[1] == 540 + 720

    assert result.awards["perfect_attendance"].users == [1]
    assert result.awards["attendance_king"].users == [1]
    assert result.awards["attendance_king"].value == 1


def test_weekend_excluded_from_attendance_king_but_perfect_needs_all_days():
    # 2026-06-06 (토), 2026-06-08 (월)
    raw = RawData(
        checkin={
            date(2026, 6, 6): {1: ("alice", dt(2026, 6, 6, 9, 0))},
            date(2026, 6, 8): {1: ("alice", dt(2026, 6, 8, 9, 0)), 2: ("bob", dt(2026, 6, 8, 9, 0))},
        },
        checkout={
            date(2026, 6, 6): {1: ("alice", dt(2026, 6, 6, 18, 0))},
            date(2026, 6, 8): {1: ("alice", dt(2026, 6, 8, 18, 0)), 2: ("bob", dt(2026, 6, 8, 18, 0))},
        },
    )
    result = aggregate(raw, today=date(2026, 6, 8))

    assert result.dates == [date(2026, 6, 6), date(2026, 6, 8)]
    assert result.weekday_dates == [date(2026, 6, 8)]

    # alice: 토/월 모두 attended -> 개근
    assert set(result.awards["perfect_attendance"].users) == {1}
    # 출근왕은 평일(Dw)만 카운트 -> 6/8 둘 다 attended -> 공동 출근왕
    assert set(result.awards["attendance_king"].users) == {1, 2}
    assert result.awards["attendance_king"].value == 1


def test_vacation_checkin_conflict_checkin_wins():
    raw = RawData(
        checkin={
            date(2026, 6, 1): {1: ("alice", dt(2026, 6, 1, 9, 0))},
        },
        checkout={
            date(2026, 6, 1): {1: ("alice", dt(2026, 6, 1, 18, 0))},
        },
        vacation={
            date(2026, 6, 1): {1: ("alice", dt(2026, 6, 1, 8, 0))},
        },
    )
    result = aggregate(raw, today=date(2026, 6, 1))

    # 휴가 무시 -> 출근왕 후보 (개근상도 D가 1일이라 충족)
    assert result.awards["vacation_king"].users == []
    assert result.table[1][date(2026, 6, 1)].vacation is False
    assert result.table[1][date(2026, 6, 1)].checkin == dt(2026, 6, 1, 9, 0)


def test_checkout_without_checkin_zero_study_time():
    raw = RawData(
        checkin={date(2026, 6, 1): {}},
        checkout={date(2026, 6, 1): {1: ("alice", dt(2026, 6, 1, 18, 0))}},
    )
    result = aggregate(raw, today=date(2026, 6, 1))

    assert result.study_minutes[1] == 0
    assert result.awards["perfect_attendance"].users == []


def test_no_dates_yet_no_perfect_attendance():
    raw = RawData()
    result = aggregate(raw, today=date(2026, 6, 1))

    assert result.dates == []
    assert result.awards["perfect_attendance"].users == []
    assert result.awards["attendance_king"].users == []
