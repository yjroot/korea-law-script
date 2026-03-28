import os

# 법제처 Open API 인증키 (https://open.law.go.kr 에서 발급)
# 환경변수 LAW_API_KEY 또는 직접 설정
API_KEY = os.environ.get("LAW_API_KEY", "")

# API 기본 URL
SEARCH_URL = "http://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "http://www.law.go.kr/DRF/lawService.do"

# korea-law 저장소 경로
OUTPUT_DIR = os.environ.get("KOREA_LAW_DIR", os.path.expanduser("~/korea-law"))

# 요청 간 대기 시간 (초)
REQUEST_DELAY = 0.5

# 페이지당 결과 수 (최대 100)
PAGE_SIZE = 100

# 법령 유형별 디렉토리 매핑
LAW_TYPE_DIR = {
    "법률": "법률",
    "대통령령": "대통령령",
    "총리령": "총리령",
    "부령": "부령",
    "행정규칙": "행정규칙",
}
