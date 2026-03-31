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
# "부령"으로 끝나는 것은 모두 "부령" 디렉토리로,
# "규칙"으로 끝나는 것은 유형명 그대로 디렉토리를 사용한다.
LAW_TYPE_DIR = {
    "법률": "법률",
    "헌법": "헌법",
    "대통령령": "대통령령",
    "대통령긴급명령": "대통령령",
    "총리령": "총리령",
    "국회규칙": "국회규칙",
    "대법원규칙": "대법원규칙",
    "헌법재판소규칙": "헌법재판소규칙",
    "중앙선거관리위원회규칙": "중앙선거관리위원회규칙",
    "선거관리위원회규칙": "중앙선거관리위원회규칙",
    "감사원규칙": "감사원규칙",
}


def get_law_type_dir(law_type: str) -> str:
    """법령 유형에 따른 디렉토리명을 반환한다."""
    if law_type in LAW_TYPE_DIR:
        return LAW_TYPE_DIR[law_type]
    if law_type.endswith("부령") or law_type.endswith("원령"):
        return "부령"
    return "기타"
