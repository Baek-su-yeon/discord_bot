"""순수 함수: 원자료 -> 출석부/누적시간/수상 결과. 디스코드 의존성 없음."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

# 가상 퇴근 시각 (KST) — 공부 시간 계산과 출석부 표시에만 사용
VIRTUAL_CHECKOUT_TIME = time(21, 0)

# 유저 한 명의 특정 게시물 내 최초 댓글 정보: (표시 이름, 댓글 시각 KST)
UserEntry = tuple[str, datetime]

# 게시물 타입별 데이터: 게시물 날짜 -> {유저ID: UserEntry}
# 날짜 키가 존재한다는 것 자체가 "그날 해당 타입의 게시물이 있었다"는 뜻.
TypeData = dict[date, dict[int, UserEntry]]


@dataclass
class RawData:
    """fetch.py가 만들어주는 이번 달 원자료."""

    checkin: TypeData = field(default_factory=dict)
    checkout: TypeData = field(default_factory=dict)
    vacation: TypeData = field(default_factory=dict)
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
    dates: list[date]  # D
    weekday_dates: list[date]  # Dw
    roster: dict[int, str]  # user_id -> display_name
    table: dict[int, dict[date, Cell]]  # user_id -> {date: Cell} (D 날짜만)
    study_minutes: dict[int, int]  # user_id -> 총 공부 시간(분)
    awards: dict[str, Award]


def aggregate(raw: RawData, today: date) -> AggregateResult:
    """이번 달 원자료를 받아 출석부/누적시간/수상 결과를 계산한다."""

    # D = 출근 게시물과 퇴근 게시물이 모두 존재하는 날 (오늘까지)
    d = sorted(set(raw.checkin) & set(raw.checkout))
    d = [dt for dt in d if dt <= today]
    dw = [dt for dt in d if dt.weekday() < 5]  # 평일만 (월=0 ... 금=4)

    # 충돌 처리: 같은 날 휴가 + 출근을 둘 다 단 경우 출근 우선, 휴가 무시
    vacation: TypeData = {}
    for dt, entries in raw.vacation.items():
        checkin_users = raw.checkin.get(dt, {})
        vacation[dt] = {uid: val for uid, val in entries.items() if uid not in checkin_users}

    # 참여자(roster) = 이번 달에 출근/퇴근/휴가/운동 중 하나라도 댓글을 단 유저 전체
    roster: dict[int, str] = {}
    for type_data in (raw.checkin, raw.checkout, vacation, raw.exercise):
        for entries in type_data.values():
            for uid, (name, _) in entries.items():
                roster[uid] = name

    # 출석부 표 (D 날짜만, 참여자만)
    table: dict[int, dict[date, Cell]] = {uid: {} for uid in roster}
    for dt in d:
        checkins = raw.checkin.get(dt, {})
        checkouts = raw.checkout.get(dt, {})
        vacs = vacation.get(dt, {})
        for uid in roster:
            cell = Cell()
            if uid in vacs:
                cell.vacation = True
            else:
                if uid in checkins:
                    cell.checkin = checkins[uid][1]
                if uid in checkouts:
                    cell.checkout = checkouts[uid][1]
                elif cell.checkin is not None:
                    cell.virtual_checkout = True
            if cell.checkin is not None or cell.checkout is not None or cell.vacation:
                table[uid][dt] = cell

    # 인증 완료(attended): D의 각 날, 출근 댓글 AND 퇴근 댓글(가상 제외)
    attended: dict[int, set[date]] = {uid: set() for uid in roster}
    for dt in d:
        checkins = raw.checkin.get(dt, {})
        checkouts = raw.checkout.get(dt, {})
        for uid in roster:
            if uid in checkins and uid in checkouts:
                attended[uid].add(dt)

    # 공부 시간: 출근/퇴근 게시물이 있는 모든 날(오늘까지, 주말 포함) 합산
    all_dates = sorted(set(raw.checkin) | set(raw.checkout))
    all_dates = [dt for dt in all_dates if dt <= today]
    study_minutes: dict[int, int] = {uid: 0 for uid in roster}
    for dt in all_dates:
        checkins = raw.checkin.get(dt, {})
        checkouts = raw.checkout.get(dt, {})
        for uid in roster:
            checkin_entry = checkins.get(uid)
            checkout_entry = checkouts.get(uid)
            checkin_time = checkin_entry[1] if checkin_entry else None
            checkout_time = checkout_entry[1] if checkout_entry else None

            if checkin_time and checkout_time:
                delta = checkout_time - checkin_time
            elif checkin_time and not checkout_time:
                virtual_end = datetime.combine(dt, VIRTUAL_CHECKOUT_TIME, tzinfo=checkin_time.tzinfo)
                delta = virtual_end - checkin_time
            else:
                delta = timedelta(0)

            minutes = max(0, int(delta.total_seconds() // 60))
            study_minutes[uid] += minutes

    awards: dict[str, Award] = {}

    # 개근상: D의 모든 날에 attended == True
    perfect_users = [uid for uid in roster if d and attended[uid] == set(d)]
    awards["perfect_attendance"] = Award(users=perfect_users, value=len(d))

    # 출근왕: Dw 중 attended == True 인 날 수 최대
    dw_set = set(dw)
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

    # 휴가왕: 휴가 게시물에 댓글을 단 날 수 최대 (충돌 처리 반영)
    vacation_counts = {uid: 0 for uid in roster}
    for entries in vacation.values():
        for uid in entries:
            vacation_counts[uid] += 1
    max_vacation = max(vacation_counts.values(), default=0)
    awards["vacation_king"] = Award(
        users=[uid for uid, c in vacation_counts.items() if c == max_vacation and max_vacation > 0],
        value=max_vacation,
    )

    # 운동왕: 운동 게시물에 댓글을 단 날 수 최대 (하루 1회)
    exercise_counts = {uid: 0 for uid in roster}
    for entries in raw.exercise.values():
        for uid in entries:
            exercise_counts[uid] += 1
    max_exercise = max(exercise_counts.values(), default=0)
    awards["exercise_king"] = Award(
        users=[uid for uid, c in exercise_counts.items() if c == max_exercise and max_exercise > 0],
        value=max_exercise,
    )

    return AggregateResult(
        dates=d,
        weekday_dates=dw,
        roster=roster,
        table=table,
        study_minutes=study_minutes,
        awards=awards,
    )
