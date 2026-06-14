from datetime import date, datetime
from zoneinfo import ZoneInfo

from aggregate import AttendanceLog, RawData, aggregate

KST = ZoneInfo("Asia/Seoul")


def dt(y, m, d, h, mi):
    return datetime(y, m, d, h, mi, tzinfo=KST)


def att(name, *events):
    """AttendanceLog 헬퍼: events = (datetime, "in"/"out") ..."""
    return AttendanceLog(name=name, events=list(events))


# --- 댓글 폴백 공부시간 (음성 입력은 축 2에서 추가) ---


def test_basic_comment_study_time_and_attendance():
    # 2026-06-01 (월), 2026-06-02 (화)
    raw = RawData(
        attendance={
            date(2026, 6, 1): {1: att("alice", (dt(2026, 6, 1, 9, 0), "in"), (dt(2026, 6, 1, 18, 0), "out"))},
            # 6/2: 퇴실 댓글 없음 -> 가상 퇴실 18:00
            date(2026, 6, 2): {1: att("alice", (dt(2026, 6, 2, 9, 0), "in"))},
        },
    )
    result = aggregate(raw, today=date(2026, 6, 2))

    assert result.dates == [date(2026, 6, 1), date(2026, 6, 2)]
    assert result.weekday_dates == [date(2026, 6, 1), date(2026, 6, 2)]
    assert result.roster == {1: "alice"}

    # 6/1: 09:00-18:00 = 540분 - 60분 = 480분
    # 6/2: 입실만 -> 가상 퇴실 18:00 = 540분 - 60분 = 480분
    assert result.study_minutes[1] == 480 + 480

    assert result.awards["perfect_attendance"].users == [1]
    assert result.awards["attendance_king"].users == [1]
    assert result.awards["attendance_king"].value == 2

    assert result.today_checkins == {1: ("alice", dt(2026, 6, 2, 9, 0))}


def test_multi_session_sum():
    # 입실 -> 퇴실 -> 입실 -> 퇴실 = 각 세션 합산
    raw = RawData(
        attendance={
            date(2026, 6, 1): {
                1: att(
                    "alice",
                    (dt(2026, 6, 1, 9, 0), "in"),
                    (dt(2026, 6, 1, 12, 0), "out"),
                    (dt(2026, 6, 1, 13, 0), "in"),
                    (dt(2026, 6, 1, 18, 0), "out"),
                )
            }
        },
    )
    result = aggregate(raw, today=date(2026, 6, 1))

    # (09-12)=180 + (13-18)=300 = 480분 - 60분(식사) = 420분
    assert result.study_minutes[1] == 420


def test_virtual_checkout_18_when_no_checkout():
    raw = RawData(
        attendance={date(2026, 6, 1): {1: att("alice", (dt(2026, 6, 1, 9, 0), "in"))}},
    )
    result = aggregate(raw, today=date(2026, 6, 1))

    # 가상 퇴실 18:00 -> 540분 - 60분 = 480분
    assert result.study_minutes[1] == 480
    cell = result.table[1][date(2026, 6, 1)]
    assert cell.checkin == dt(2026, 6, 1, 9, 0)
    assert cell.virtual_checkout is True
    assert cell.vacation is False


def test_out_before_in_is_ignored():
    # 퇴실이 입실보다 먼저 나오면 그 퇴실은 무시
    raw = RawData(
        attendance={
            date(2026, 6, 1): {
                1: att(
                    "alice",
                    (dt(2026, 6, 1, 10, 0), "out"),
                    (dt(2026, 6, 1, 11, 0), "in"),
                    (dt(2026, 6, 1, 18, 0), "out"),
                )
            }
        },
    )
    result = aggregate(raw, today=date(2026, 6, 1))

    # (11-18)=420분 - 60분 = 360분
    assert result.study_minutes[1] == 360


def test_lunch_break_clamped_to_zero_for_short_day():
    raw = RawData(
        attendance={
            date(2026, 6, 1): {1: att("alice", (dt(2026, 6, 1, 9, 0), "in"), (dt(2026, 6, 1, 9, 30), "out"))}
        },
    )
    result = aggregate(raw, today=date(2026, 6, 1))

    # 30분 - 60분 = 음수 -> 0 클램프
    assert result.study_minutes[1] == 0


# --- 자동 휴가 / 수상 ---


def test_auto_vacation_and_vacation_king():
    # bob은 6/2에 입실 댓글이 없음 -> 자동 휴가
    raw = RawData(
        attendance={
            date(2026, 6, 1): {
                1: att("alice", (dt(2026, 6, 1, 9, 0), "in"), (dt(2026, 6, 1, 18, 0), "out")),
                2: att("bob", (dt(2026, 6, 1, 10, 0), "in"), (dt(2026, 6, 1, 18, 0), "out")),
            },
            date(2026, 6, 2): {
                1: att("alice", (dt(2026, 6, 2, 9, 0), "in"), (dt(2026, 6, 2, 18, 0), "out")),
            },
        },
    )
    result = aggregate(raw, today=date(2026, 6, 2))

    # alice는 매일 입실 -> 개근, 휴가 0일
    assert result.awards["perfect_attendance"].users == [1]
    assert result.table[1][date(2026, 6, 1)].vacation is False
    # bob은 6/2 입실 없음 -> 자동 휴가 1일 -> 휴가왕
    assert result.table[2][date(2026, 6, 2)].vacation is True
    assert result.awards["vacation_king"].users == [2]
    assert result.awards["vacation_king"].value == 1


def test_checkout_only_no_study_and_auto_vacation():
    # 퇴실 댓글만 있고 입실 없음 -> 공부시간 0, 자동 휴가
    raw = RawData(
        attendance={date(2026, 6, 1): {1: att("alice", (dt(2026, 6, 1, 18, 0), "out"))}},
    )
    result = aggregate(raw, today=date(2026, 6, 1))

    assert result.study_minutes[1] == 0
    assert result.table[1][date(2026, 6, 1)].vacation is True
    assert result.awards["perfect_attendance"].users == []
    assert result.awards["vacation_king"].users == [1]


def test_weekend_excluded_from_attendance_king_but_perfect_needs_all_days():
    # 2026-06-06 (토), 2026-06-08 (월)
    raw = RawData(
        attendance={
            date(2026, 6, 6): {1: att("alice", (dt(2026, 6, 6, 9, 0), "in"), (dt(2026, 6, 6, 18, 0), "out"))},
            date(2026, 6, 8): {
                1: att("alice", (dt(2026, 6, 8, 9, 0), "in"), (dt(2026, 6, 8, 18, 0), "out")),
                2: att("bob", (dt(2026, 6, 8, 9, 0), "in"), (dt(2026, 6, 8, 18, 0), "out")),
            },
        },
    )
    result = aggregate(raw, today=date(2026, 6, 8))

    assert result.dates == [date(2026, 6, 6), date(2026, 6, 8)]
    assert result.weekday_dates == [date(2026, 6, 8)]

    # alice: 토/월 모두 입실 -> 개근
    assert set(result.awards["perfect_attendance"].users) == {1}
    # 출근왕은 평일(Dw)만 카운트 -> 6/8 둘 다 입실 -> 공동 출근왕
    assert set(result.awards["attendance_king"].users) == {1, 2}
    assert result.awards["attendance_king"].value == 1


# --- 빈 데이터 방어 ---


def test_empty_data_no_errors():
    raw = RawData()
    result = aggregate(raw, today=date(2026, 6, 1))

    assert result.dates == []
    assert result.roster == {}
    assert result.study_minutes == {}
    assert result.awards["perfect_attendance"].users == []
    assert result.awards["attendance_king"].users == []
    assert result.awards["study_king"].users == []
    assert result.awards["vacation_king"].users == []
    assert result.awards["exercise_king"].users == []
    assert result.today_checkins == {}


def test_attendance_post_with_no_comments():
    # 게시물은 있으나 댓글 0개 -> D에는 포함, 참여자/수상 없음
    raw = RawData(attendance={date(2026, 6, 1): {}})
    result = aggregate(raw, today=date(2026, 6, 1))

    assert result.dates == [date(2026, 6, 1)]
    assert result.roster == {}
    assert result.awards["perfect_attendance"].users == []


def test_comment_trailing_checkin_dropped_when_pairs_exist():
    # 완성 페어가 있는데 마지막 입실이 짝(퇴실) 없이 끝나면 그 세션은 버림
    raw = RawData(
        attendance={
            date(2026, 6, 1): {
                1: att(
                    "alice",
                    (dt(2026, 6, 1, 9, 0), "in"),
                    (dt(2026, 6, 1, 12, 0), "out"),
                    (dt(2026, 6, 1, 13, 0), "in"),  # 짝 없음 -> 버림 (가상퇴실 미적용)
                )
            }
        },
    )
    result = aggregate(raw, today=date(2026, 6, 1))

    # (09-12)=180분 - 60분(식사) = 120분. 마지막 13:00 입실은 버려짐.
    assert result.study_minutes[1] == 120


# --- 음성 우선 / 폴백 (voice 입력) ---


def test_voice_takes_priority_over_comment():
    # 음성 세션이 유효하면 댓글은 무시되고 음성만 사용 (식사 차감 없음)
    raw = RawData(
        attendance={
            date(2026, 6, 1): {1: att("alice", (dt(2026, 6, 1, 9, 0), "in"), (dt(2026, 6, 1, 18, 0), "out"))}
        },
    )
    voice = {date(2026, 6, 1): {1: [(dt(2026, 6, 1, 10, 0), dt(2026, 6, 1, 15, 0))]}}
    result = aggregate(raw, today=date(2026, 6, 1), voice=voice)

    # 음성 10:00-15:00 = 300분 (식사 차감 없음). 댓글 기반 480분은 무시.
    assert result.study_minutes[1] == 300


def test_voice_multi_session_sum():
    raw = RawData(
        attendance={date(2026, 6, 1): {1: att("alice", (dt(2026, 6, 1, 9, 0), "in"))}},
    )
    voice = {
        date(2026, 6, 1): {
            1: [
                (dt(2026, 6, 1, 9, 0), dt(2026, 6, 1, 12, 0)),  # 180분
                (dt(2026, 6, 1, 13, 0), dt(2026, 6, 1, 17, 0)),  # 240분
            ]
        }
    }
    result = aggregate(raw, today=date(2026, 6, 1), voice=voice)

    assert result.study_minutes[1] == 420


def test_voice_null_end_falls_back_to_comment():
    # 종료 None(유실) 세션이 있으면 음성 소스를 폐기하고 댓글 폴백
    raw = RawData(
        attendance={
            date(2026, 6, 1): {1: att("alice", (dt(2026, 6, 1, 9, 0), "in"), (dt(2026, 6, 1, 18, 0), "out"))}
        },
    )
    voice = {date(2026, 6, 1): {1: [(dt(2026, 6, 1, 10, 0), None)]}}
    result = aggregate(raw, today=date(2026, 6, 1), voice=voice)

    # 음성 폐기 -> 댓글 폴백 09:00-18:00 = 540분 - 60분 = 480분
    assert result.study_minutes[1] == 480


def test_voice_present_but_no_checkin_comment_no_study():
    # 음성 세션이 있어도 그날 입실 댓글이 없으면 공부시간 미집계 (자격 게이트)
    raw = RawData(
        attendance={date(2026, 6, 1): {1: att("alice", (dt(2026, 6, 1, 18, 0), "out"))}},  # 퇴실만
    )
    voice = {date(2026, 6, 1): {1: [(dt(2026, 6, 1, 9, 0), dt(2026, 6, 1, 18, 0))]}}
    result = aggregate(raw, today=date(2026, 6, 1), voice=voice)

    # 입실 없음 -> 공부시간 0 (음성 9시간 무시)
    assert result.study_minutes[1] == 0
    # 다른 상에는 영향: 입실 없으니 자동 휴가
    assert result.table[1][date(2026, 6, 1)].vacation is True
    assert result.awards["vacation_king"].users == [1]


def test_voice_clamped_to_0900_and_midnight():
    # 09:00 이전 시작은 09:00부터, 자정 넘긴 세션은 자정에서 끊는다
    raw = RawData(
        attendance={date(2026, 6, 1): {1: att("alice", (dt(2026, 6, 1, 9, 0), "in"))}},
    )
    voice = {
        date(2026, 6, 1): {
            1: [(dt(2026, 6, 1, 7, 0), dt(2026, 6, 2, 1, 0))]  # 07:00 ~ 다음날 01:00
        }
    }
    result = aggregate(raw, today=date(2026, 6, 1), voice=voice)

    # 09:00 ~ 자정(00:00) = 15시간 = 900분
    assert result.study_minutes[1] == 900
