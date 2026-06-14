"""환경값 로드: 토큰, 길드/채널 ID, 태그명, 실행시각, 타임존."""

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
ATTENDANCE_CHANNEL_ID = int(os.getenv("ATTENDANCE_CHANNEL_ID", "0"))

SUMMARY_POST_TITLE = os.getenv("SUMMARY_POST_TITLE", "📊 이번 달 집계")

# 집계 게시물에 부여할 태그 이름 (선택). 태그가 필수인 포럼 채널에서만 필요.
# 입퇴실/휴가/운동 3종과는 달라야 함.
SUMMARY_POST_TAG = os.getenv("SUMMARY_POST_TAG") or None

# 포럼 태그 이름 (3종, 정확히 일치해야 함)
# 기존 출근/퇴근 2종은 "입퇴실" 하나로 통합. 옛 태그 게시물은 fetch에서 무시한다.
TAG_ATTENDANCE = "입퇴실"
TAG_VACATION = "휴가"
TAG_EXERCISE = "운동"

# 입퇴실 게시물 댓글에서 출퇴근을 판별하는 키워드 (부분 포함 매칭, 정확 일치 아님)
KEYWORD_CHECK_IN = "입실"
KEYWORD_CHECK_OUT = "퇴실"

# 타임존
TIMEZONE = ZoneInfo("Asia/Seoul")

# 매일 집계 실행 시각 (KST)
RUN_HOUR = 21
RUN_MINUTE = 0

# 상태 파일 경로
STATE_FILE = "state.json"
