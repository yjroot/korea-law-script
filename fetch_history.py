"""각 법령의 연혁 목록을 수집하여 data/law_history.json에 저장한다."""

import json
import re
import time
from pathlib import Path

import requests
from tqdm import tqdm

from config import API_KEY, REQUEST_DELAY


SEARCH_URL = "http://www.law.go.kr/DRF/lawSearch.do"


def normalize_date(date_str: str) -> str:
    """'1953.9.18' 등의 날짜를 'YYYYMMDD' 형식으로 정규화한다."""
    parts = date_str.strip().split(".")
    if len(parts) == 3:
        y, m, d = parts
        return f"{int(y):04d}{int(m):02d}{int(d):02d}"
    # 이미 숫자만인 경우
    return date_str.replace(".", "")


def fetch_history_html(query: str, page: int = 1) -> str:
    """연혁법령 목록 HTML을 조회한다."""
    params = {
        "OC": API_KEY,
        "target": "lsHistory",
        "type": "HTML",
        "query": query,
        "display": 100,
        "page": page,
        "sort": "efasc",  # 시행일자 오름차순
    }
    resp = requests.get(SEARCH_URL, params=params, timeout=60)
    resp.raise_for_status()
    return resp.text


def parse_history_html(html: str) -> list[dict]:
    """연혁법령 HTML에서 법령 정보를 파싱한다."""
    results = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)

    for row in rows:
        mst_match = re.search(r"MST=(\d+)", row)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if not mst_match or len(tds) < 8:
            continue

        cells = [re.sub(r"<[^>]+>", "", td).strip() for td in tds]
        results.append({
            "법령일련번호": mst_match.group(1),
            "법령명한글": cells[1] if len(cells) > 1 else "",
            "소관부처명": cells[2] if len(cells) > 2 else "",
            "제개정구분명": cells[3] if len(cells) > 3 else "",
            "법령구분명": cells[4] if len(cells) > 4 else "",
            "공포번호": cells[5] if len(cells) > 5 else "",
            "공포일자": normalize_date(cells[6]) if len(cells) > 6 else "",
            "시행일자": normalize_date(cells[7]) if len(cells) > 7 else "",
            "현행연혁": cells[8] if len(cells) > 8 else "",
        })

    return results


def fetch_law_history(law_name: str) -> list[dict]:
    """특정 법령의 모든 연혁 버전을 수집한다.

    법령명으로 검색 후 정확히 일치하는 것만 필터링한다.
    """
    all_results = []
    page = 1

    while True:
        html = fetch_history_html(law_name, page)
        results = parse_history_html(html)

        if not results:
            break

        # 정확히 일치하는 법령명만 필터
        matched = [r for r in results if r["법령명한글"] == law_name]
        all_results.extend(matched)

        # 결과가 100건 미만이면 마지막 페이지
        if len(results) < 100:
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    return all_results


def main():
    if not API_KEY:
        raise RuntimeError(
            "API 키가 설정되지 않았습니다. "
            "환경변수 LAW_API_KEY를 설정하거나 config.py에서 직접 입력하세요."
        )

    # 현행 법령 목록에서 고유 법령명 추출
    law_list_path = Path(__file__).parent / "data" / "law_list.json"
    if not law_list_path.exists():
        raise FileNotFoundError(
            f"{law_list_path}가 없습니다. 먼저 fetch_law_list.py를 실행하세요."
        )

    laws = json.loads(law_list_path.read_text(encoding="utf-8"))
    # 고유 법령명 목록 (현행 법령 기준)
    law_names = sorted(set(law["법령명한글"] for law in laws))
    print(f"총 {len(law_names)}개 법령의 연혁을 수집합니다.")

    all_history = []
    failed = []

    for name in tqdm(law_names, desc="연혁 수집"):
        try:
            history = fetch_law_history(name)
            all_history.extend(history)
        except Exception as e:
            print(f"\n[오류] {name}: {e}")
            failed.append(name)
        time.sleep(REQUEST_DELAY)

    # 공포일자 기준 정렬
    all_history.sort(key=lambda x: x.get("공포일자", ""))

    output_path = Path(__file__).parent / "data" / "law_history.json"
    output_path.write_text(
        json.dumps(all_history, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n수집 완료: {len(all_history)}건")
    print(f"저장 완료: {output_path}")
    if failed:
        print(f"실패: {len(failed)}건 - {failed[:10]}")

    # 통계
    from collections import Counter
    counter = Counter(h["제개정구분명"] for h in all_history)
    print("\n[제개정 유형별 통계]")
    for k, v in counter.most_common():
        print(f"  {k}: {v}건")


if __name__ == "__main__":
    main()
