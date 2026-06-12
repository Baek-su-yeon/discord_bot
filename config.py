"""환경값 로드: 토큰, 길드/채널 ID, 태그명, 실행시각, 타임존."""

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
ATTENDANCE_CHANNEL_ID = int(os.getenv("ATTENDANCE_CHANNEL_ID", "0"))

SUMMARY_POST_TITLE = os.getenv("SUMMARY_POST_TITLE", "📊 이번 달 집계")

# 포럼 태그 이름 (4종, 정확히 일치해야 함)
TAG_CHECK_IN = "출근"
TAG_CHECK_OUT = "퇴근"
TAG_VACATION = "휴가"
TAG_EXERCISE = "운동"

# 타임존
TIMEZONE = ZoneInfo("Asia/Seoul")

# 매일 집계 실행 시각 (KST)
RUN_HOUR = 21
RUN_MINUTE = 0

# 상태 파일 경로
STATE_FILE = "state.json"
