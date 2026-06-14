"""순수 함수: 원자료 -> 출석부/누적시간/수상 결과. 디스코드 의존성 없음.

공부시간은 "음성 체류 > 댓글 폴백" 2단계로 계산한다(음성 입력은 축 2에서 추가).
이 모듈은 입력 자료구조와 댓글 기반(폴백) 계산을 담당한다.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

# 가상 퇴실 시각 (KST) — 댓글 폴백에서 퇴실 댓글이 없을 때만 사용. 패널티 성격.
VIRTUAL_CHECKOUT_TIME = time(18, 0)

# 공부 시간 계산 시 하루마다 빼는 식사 시간 (댓글 폴백 전용)
LUNCH_BREAK_MINUTES = 60

# 집계 대상 시간 범위 시작 (매일 09:00 ~ 자정)
DAY_START_TIME = time(9, 0)

# 입퇴실 게시물에서 한 유저의 댓글 이벤트: (시각 KST, "in" | "out")
AttendanceEvent = tuple[datetime, str]

# 유저 한 명의 특정 게시물 내 최초 댓글 정보: (표시 이름, 댓글 시각 KST)
UserEntry = tuple[str, datetime]

# 게시물 타입별 데이터: 게시물 날짜 -> {유저ID: UserEntry} (운동 등 최초댓글 기반)
TypeData = dict[date, dict[int, UserEntry]]


@dataclass
class AttendanceLog:
    """입퇴실 게시물에서 한 유저의 댓글 로그: 표시 이름 + 시간순 입실/퇴실 이벤트."""

    name: str
    events: list[AttendanceEvent] = field(default_factory=list)


# 입퇴실 게시물 데이터: 게시물 날짜 -> {유저ID: AttendanceLog}
# 날짜 키 존재 = "그날 입퇴실 게시물이 있었다"는 뜻.
AttendanceData = dict[date, dict[int, AttendanceLog]]


@dataclass
class RawData:
    """fetch.py가 만들어주는 이번 달 원자료."""

    # 입퇴실 게시물(입실/퇴실 댓글 시퀀스)
    attendance: AttendanceData = field(default_factory=dict)
    # 운동 게시물(유저별 최초 댓글)
    exercise: TypeData = field(default_factory=dict)


@dataclass
class Cell:
    """출석부 표의 한 칸."""

    checkin: datetime | None = None
    checkout: datetime | None = None
    vacation: bool = False
    virtual_checkout: bool = False


@dataclass
class Award:
    """수상 결과: 수상자 목록(공동 수상 가능) + 수치."""

    users: list[int]
    value: int


@dataclass
class AggregateResult:
    dates: list[date]  # D = 입퇴실 게시물이 존재하는 날 (오늘까지)
    weekday_dates: list[date]  # Dw
    roster: dict[int, str]  # user_id -> display_name
    table: dict[int, dict[date, Cell]]  # user_id -> {date: Cell} (D 날짜만)
    study_minutes: dict[int, int]  # user_id -> 총 공부 시간(분)
    awards: dict[str, Award]
    today_checkins: dict[int, UserEntry]  # user_id -> (display_name, 오늘 첫 입실 시각)


def _pair_sessions(
    events: list[AttendanceEvent],
) -> tuple[list[tuple[datetime, datetime]], datetime | None]:
    """시간순 (시각, "in"/"out") 이벤트를 입실→퇴실 페어로 묶는다.

    - 시간순 정렬 후 입실 다음 퇴실과 페어링, 반복.
    - 퇴실이 입실보다 먼저 나오면 그 퇴실은 무시.
    - 입실 중 추가 입실은 무시(이미 입실 상태).
    반환: (완성된 (입실, 퇴실) 페어 목록, 짝 없는 마지막 입실 시각 또는 None)
    """
    pairs: list[tuple[datetime, datetime]] = []
    pending: datetime | None = None
    for t, kind in sorted(events, key=lambda e: e[0]):
        if kind == "in":
            if pending is None:
                pending = t
        else:  # "out"
            if pending is not None:
                pairs.append((pending, t))
                pending = None
    return pairs, pending


def _comment_study_minutes(events: list[AttendanceEvent], day: date) -> int:
    """댓글 폴백 공부시간(분): 다중 세션 합산 - 식사 60분, 가상퇴실 18:00, 음수 0 클램프."""
    pairs, dangling = _pair_sessions(events)

    total = timedelta()
    for start, end in pairs:
        if end > start:
            total += end - start

    # 마지막 입실이 짝 없이 끝나면 가상 퇴실 18:00 으로 간주 (패널티)
    if dangling is not None:
        virtual_end = datetime.combine(day, VIRTUAL_CHECKOUT_TIME, tzinfo=dangling.tzinfo)
        if virtual_end > dangling:
            total += virtual_end - dangling

    if total <= timedelta():
        return 0

    total -= timedelta(minutes=LUNCH_BREAK_MINUTES)
    return max(0, int(total.total_seconds() // 60))


def _first_checkin(events: list[AttendanceEvent]) -> datetime | None:
    """이벤트 중 가장 이른 입실 시각. 입실이 없으면 None."""
    checkins = [t for t, kind in events if kind == "in"]
    return min(checkins) if checkins else None


def _last_checkout(events: list[AttendanceEvent]) -> datetime | None:
    """이벤트 중 가장 늦은 퇴실 시각. 퇴실이 없으면 None."""
    checkouts = [t for t, kind in events if kind == "out"]
    return max(checkouts) if checkouts else None


def aggregate(raw: RawData, today: date) -> AggregateResult:
    """이번 달 원자료를 받아 출석부/누적시간/수상 결과를 계산한다."""

    # D = 입퇴실 게시물이 존재하는 날 (오늘까지)
    d = sorted(dt for dt in raw.attendance if dt <= today)
    dw = [dt for dt in d if dt.weekday() < 5]  # 평일만 (월=0 ... 금=4)
    dw_set = set(dw)

    # 참여자(roster) = 이번 달에 입퇴실/운동 중 하나라도 댓글을 단 유저 전체
    roster: dict[int, str] = {}
    for entries in raw.attendance.values():
        for uid, log in entries.items():
            roster[uid] = log.name
    for entries in raw.exercise.values():
        for uid, (name, _) in entries.items():
            roster.setdefault(uid, name)

    # 출근 인정(attended): D의 각 날, 입실 댓글이 하나라도 있는 유저
    attended: dict[int, set[date]] = {uid: set() for uid in roster}
    for dt in d:
        entries = raw.attendance.get(dt, {})
        for uid, log in entries.items():
            if _first_checkin(log.events) is not None:
                attended[uid].add(dt)

    # 출석부 표 (D 날짜만, 참여자만). 입실 없으면 자동 휴가.
    table: dict[int, dict[date, Cell]] = {uid: {} for uid in roster}
    for dt in d:
        entries = raw.attendance.get(dt, {})
        for uid in roster:
            log = entries.get(uid)
            cell = Cell()
            checkin = _first_checkin(log.events) if log else None
            if checkin is not None:
                cell.checkin = checkin
                checkout = _last_checkout(log.events)
                if checkout is not None:
                    cell.checkout = checkout
                else:
                    cell.virtual_checkout = True
            else:
                cell.vacation = True  # 입실 없음 -> 사유 불문 자동 휴가
            if cell.checkin is not None or cell.vacation:
                table[uid][dt] = cell

    # 공부 시간: 입퇴실 게시물이 있는 모든 날(오늘까지, 주말 포함) 댓글 폴백 합산
    study_minutes: dict[int, int] = {uid: 0 for uid in roster}
    for dt in d:
        entries = raw.attendance.get(dt, {})
        for uid, log in entries.items():
            study_minutes[uid] += _comment_study_minutes(log.events, dt)

    awards: dict[str, Award] = {}

    # 개근상: D의 모든 날에 attended == True
    perfect_users = [uid for uid in roster if d and attended[uid] == set(d)]
    awards["perfect_attendance"] = Award(users=perfect_users, value=len(d))

    # 출근왕: Dw 중 attended == True 인 날 수 최대
    attendance_counts = {uid: len(attended[uid] & dw_set) for uid in roster}
    max_attendance = max(attendance_counts.values(), default=0)
    awards["attendance_king"] = Award(
        users=[uid for uid, c in attendance_counts.items() if c == max_attendance and max_attendance > 0],
        value=max_attendance,
    )

    # 공부왕: 공부 시간 총합 최대
    max_study = max(study_minutes.values(), default=0)
    awards["study_king"] = Award(
        users=[uid for uid, m in study_minutes.items() if m == max_study and max_study > 0],
        value=max_study,
    )

    # 휴가왕: D 중 입실 안 한 날 수 최대 (자동 휴가 포함)
    vacation_counts = {uid: len(d) - len(attended[uid] & set(d)) for uid in roster}
    max_vacation = max(vacation_counts.values(), default=0)
    awards["vacation_king"] = Award(
        users=[uid for uid, c in vacation_counts.items() if c == max_vacation and max_vacation > 0],
        value=max_vacation,
    )

    # 운동왕: 운동 게시물에 댓글을 단 날 수 최대 (현행 유지)
    exercise_counts = {uid: 0 for uid in roster}
    for entries in raw.exercise.values():
        for uid in entries:
            exercise_counts[uid] += 1
    max_exercise = max(exercise_counts.values(), default=0)
    awards["exercise_king"] = Award(
        users=[uid for uid, c in exercise_counts.items() if c == max_exercise and max_exercise > 0],
        value=max_exercise,
    )

    # 오늘 출근: 오늘 입퇴실 게시물에 입실 댓글을 단 유저 (첫 입실 시각)
    today_checkins: dict[int, UserEntry] = {}
    for uid, log in raw.attendance.get(today, {}).items():
        checkin = _first_checkin(log.events)
        if checkin is not None:
            today_checkins[uid] = (log.name, checkin)

    return AggregateResult(
        dates=d,
        weekday_dates=dw,
        roster=roster,
        table=table,
        study_minutes=study_minutes,
        awards=awards,
        today_checkins=today_checkins,
    )
