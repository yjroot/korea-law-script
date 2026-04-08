"""전체 법령 목록을 법제처 Open API에서 수집하여 data/law_list.json에 저장한다."""

import json
import math
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from tqdm import tqdm

from config import API_KEY, PAGE_SIZE, REQUEST_DELAY, SEARCH_URL


def fetch_page(page: int, retries: int = 3, extra_params: dict | None = None) -> ET.Element:
    """법령 목록 API의 특정 페이지를 조회한다."""
    params = {
        "OC": API_KEY,
        "target": "law",
        "type": "XML",
        "display": PAGE_SIZE,
        "page": page,
    }
    if extra_params:
        params.update(extra_params)
    for attempt in range(retries):
        try:
            resp = requests.get(SEARCH_URL, params=params, timeout=60)
            resp.raise_for_status()
            return ET.fromstring(resp.content)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < retries - 1:
                wait = 5 * (attempt + 1)
                print(f"\n[재시도] 페이지 {page} 요청 실패 ({e}), {wait}초 후 재시도...")
                time.sleep(wait)
            else:
                raise


def parse_item(item: ET.Element) -> dict:
    """XML item 요소에서 법령 정보를 추출한다."""
    def text(tag: str) -> str:
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    return {
        "법령일련번호": text("법령일련번호"),
        "법령ID": text("법령ID"),
        "법령명한글": text("법령명한글"),
        "법령약칭명": text("법령약칭명"),
        "공포일자": text("공포일자"),
        "공포번호": text("공포번호"),
        "시행일자": text("시행일자"),
        "소관부처명": text("소관부처명"),
        "법령구분명": text("법령구분명"),
        "제개정구분명": text("제개정구분명"),
        "법령상세링크": text("법령상세링크"),
    }


def _fetch_laws(extra_params: dict | None = None, desc: str = "법령 목록 수집") -> list[dict]:
    """법령 목록을 수집한다 (공통 로직)."""
    root = fetch_page(1, extra_params=extra_params)
    total_cnt_el = root.find("totalCnt")
    if total_cnt_el is None or not total_cnt_el.text:
        raise RuntimeError(f"API 응답에서 totalCnt를 찾을 수 없습니다: {ET.tostring(root, encoding='unicode')[:500]}")

    total_cnt = int(total_cnt_el.text)
    total_pages = math.ceil(total_cnt / PAGE_SIZE)
    print(f"법령 수: {total_cnt}, 총 페이지: {total_pages}")

    laws = []
    for item in root.iter("law"):
        laws.append(parse_item(item))

    for page in tqdm(range(2, total_pages + 1), desc=desc, initial=1, total=total_pages):
        time.sleep(REQUEST_DELAY)
        root = fetch_page(page, extra_params=extra_params)
        for item in root.iter("law"):
            laws.append(parse_item(item))

    print(f"수집 완료: {len(laws)}건")
    return laws


def fetch_all_laws() -> list[dict]:
    """전체 법령 목록을 수집한다."""
    if not API_KEY:
        raise RuntimeError(
            "API 키가 설정되지 않았습니다. "
            "환경변수 LAW_API_KEY를 설정하거나 config.py에서 직접 입력하세요."
        )
    return _fetch_laws(desc="전체 법령 수집")


def fetch_laws_since(date_from: str) -> list[dict]:
    """특정 공포일자 이후의 법령만 수집한다.

    Args:
        date_from: 시작 공포일자 (YYYYMMDD 형식)
    """
    if not API_KEY:
        raise RuntimeError(
            "API 키가 설정되지 않았습니다. "
            "환경변수 LAW_API_KEY를 설정하거나 config.py에서 직접 입력하세요."
        )
    # ancYd: 공포일자 범위 (YYYYMMDD~YYYYMMDD)
    today = time.strftime("%Y%m%d")
    extra = {"ancYd": f"{date_from}~{today}"}
    print(f"공포일자 {date_from}~{today} 범위 조회")
    return _fetch_laws(extra_params=extra, desc="최근 법령 수집")


def main():
    laws = fetch_all_laws()

    output_path = Path(__file__).parent / "data" / "law_list.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(laws, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장 완료: {output_path}")

    # 법령 유형별 통계
    from collections import Counter
    counter = Counter(law["법령구분명"] for law in laws)
    print("\n[법령 유형별 통계]")
    for law_type, count in counter.most_common():
        print(f"  {law_type}: {count}건")


if __name__ == "__main__":
    main()
