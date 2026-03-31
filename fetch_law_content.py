"""법령 본문을 법제처 Open API에서 수집하여 Markdown으로 변환한다."""

import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from tqdm import tqdm

from config import (
    API_KEY,
    OUTPUT_DIR,
    REQUEST_DELAY,
    SERVICE_URL,
    get_law_type_dir,
)


def sanitize_filename(name: str) -> str:
    """파일명에 사용할 수 없는 문자를 제거하고 길이를 제한한다.

    파일시스템 제한(255바이트)을 고려하여 ".md" 확장자 포함
    최대 250바이트로 제한한다. 초과 시 해시를 붙여 고유성을 보장한다.
    """
    import hashlib

    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = name.strip(". ")

    # .md 확장자(3바이트) 포함 255바이트 이내로 제한
    max_bytes = 250
    encoded = name.encode("utf-8")
    if len(encoded) > max_bytes:
        # 해시 8자리 추가 (9바이트: _+8자)
        name_hash = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        truncated = max_bytes - 9
        # UTF-8 멀티바이트 경계에서 잘리지 않도록
        while truncated > 0:
            try:
                short = encoded[:truncated].decode("utf-8")
                break
            except UnicodeDecodeError:
                truncated -= 1
        name = f"{short}_{name_hash}"

    return name


def fetch_law_content(mst: str) -> ET.Element:
    """법령 본문 XML을 조회한다."""
    params = {
        "OC": API_KEY,
        "target": "law",
        "type": "XML",
        "MST": mst,
    }
    resp = requests.get(SERVICE_URL, params=params, timeout=60)
    resp.raise_for_status()
    return ET.fromstring(resp.content)


def extract_metadata(root: ET.Element) -> dict:
    """XML에서 법령 메타데이터를 추출한다."""
    info = root.find("기본정보")
    if info is None:
        info = root

    def text(tag: str) -> str:
        # 기본정보 내에서 먼저 찾고, 없으면 전체에서 탐색
        el = info.find(tag)
        if el is None:
            el = root.find(f".//{tag}")
        return el.text.strip() if el is not None and el.text else ""

    # 소관부처는 태그 텍스트가 아닌 경우도 있음
    소관부처_el = info.find("소관부처")
    if 소관부처_el is None:
        소관부처_el = root.find(".//소관부처")
    소관부처 = 소관부처_el.text.strip() if 소관부처_el is not None and 소관부처_el.text else ""

    # 법종구분은 태그 텍스트
    법종_el = info.find("법종구분")
    if 법종_el is None:
        법종_el = root.find(".//법종구분")
    법종구분 = 법종_el.text.strip() if 법종_el is not None and 법종_el.text else ""

    return {
        "법령명": text("법령명_한글"),
        "법령ID": text("법령ID"),
        "법령구분": 법종구분,
        "공포일자": text("공포일자"),
        "공포번호": text("공포번호"),
        "시행일자": text("시행일자"),
        "소관부처": 소관부처,
    }


def format_date(date_str: str) -> str:
    """YYYYMMDD → YYYY-MM-DD 형식으로 변환한다."""
    if len(date_str) == 8:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return date_str


def build_frontmatter(meta: dict) -> str:
    """YAML frontmatter를 생성한다."""
    lines = ["---"]
    lines.append(f'법령명: "{meta["법령명"]}"')
    lines.append(f'법령ID: "{meta["법령ID"]}"')
    lines.append(f'법령구분: "{meta["법령구분"]}"')
    lines.append(f'공포일자: "{format_date(meta["공포일자"])}"')
    lines.append(f'공포번호: {meta["공포번호"] or 0}')
    lines.append(f'시행일자: "{format_date(meta["시행일자"])}"')
    lines.append(f'소관부처: "{meta["소관부처"]}"')
    lines.append(f'출처: "https://www.law.go.kr/법령/{meta["법령명"]}"')
    lines.append("---")
    return "\n".join(lines)


def xml_text(el: ET.Element | None) -> str:
    """XML 요소의 텍스트를 안전하게 추출한다."""
    if el is None:
        return ""
    return (el.text or "").strip()


def clean_article_content(content: str) -> str:
    """조문 내용에서 조번호+제목 접두사를 제거한다.

    예: '제1조(목적) 이 법은...' → '이 법은...'
    """
    # 제N조(제목) 또는 제N조의N(제목) 패턴 제거
    cleaned = re.sub(r'^제\d+조(?:의\d+)?\s*(?:\([^)]*\)\s*)?', '', content)
    return cleaned.strip()


def convert_articles(root: ET.Element) -> str:
    """조문 XML을 Markdown 본문으로 변환한다."""
    lines = []

    for jo in root.iter("조문단위"):
        jo_num = xml_text(jo.find("조문번호"))
        jo_title = xml_text(jo.find("조문제목"))
        jo_content = xml_text(jo.find("조문내용"))
        jo_type = xml_text(jo.find("조문여부"))

        # 편, 장, 절, 관은 조문여부가 아닌 조문내용으로 판별
        if jo_type != "조문" and jo_content:
            # 편장절관 등의 구조적 제목
            if re.match(r'제\d+편\s', jo_content):
                lines.append(f"\n## {jo_content.strip()}\n")
            elif re.match(r'제\d+장\s', jo_content):
                lines.append(f"\n### {jo_content.strip()}\n")
            elif re.match(r'제\d+절\s', jo_content):
                lines.append(f"\n#### {jo_content.strip()}\n")
            elif re.match(r'제\d+관\s', jo_content):
                lines.append(f"\n##### {jo_content.strip()}\n")
            else:
                lines.append(f"\n## {jo_content.strip()}\n")
            continue

        # 일반 조문
        if jo_title:
            lines.append(f"\n###### 제{jo_num}조 ({jo_title})\n")
        elif jo_num:
            lines.append(f"\n###### 제{jo_num}조\n")

        # 직접 자식인 항이 있는지 확인
        hangs = list(jo.findall("항"))

        if hangs:
            # 항이 있는 경우: 조문내용은 보통 항 내용과 중복이므로 스킵
            for hang in hangs:
                hang_content = xml_text(hang.find("항내용"))
                if hang_content:
                    lines.append(hang_content)

                # 호 (항의 직접 자식)
                for ho in hang.findall("호"):
                    ho_content = xml_text(ho.find("호내용"))
                    if ho_content:
                        lines.append(f"  {ho_content}")

                    # 목 (호의 직접 자식)
                    for mok in ho.findall("목"):
                        mok_content = xml_text(mok.find("목내용"))
                        if mok_content:
                            lines.append(f"    {mok_content}")

                lines.append("")
        elif jo_content:
            # 항이 없는 단순 조문
            body = clean_article_content(jo_content)
            if body:
                lines.append(body)
                lines.append("")

    return "\n".join(lines)


def convert_addenda(root: ET.Element) -> str:
    """부칙을 Markdown으로 변환한다."""
    lines = []
    addenda = list(root.iter("부칙단위"))
    if not addenda:
        return ""

    lines.append("\n---\n")
    lines.append("## 부칙\n")

    for bucheck in addenda:
        title = xml_text(bucheck.find("부칙제목"))
        content = xml_text(bucheck.find("부칙내용"))
        if title:
            lines.append(f"### {title}\n")
        if content:
            lines.append(content)
            lines.append("")

    return "\n".join(lines)


def convert_law_to_markdown(root: ET.Element) -> str:
    """법령 XML 전체를 Markdown 문서로 변환한다."""
    meta = extract_metadata(root)
    parts = []

    # Frontmatter
    parts.append(build_frontmatter(meta))
    parts.append("")

    # 제목
    parts.append(f"# {meta['법령명']}")
    parts.append("")

    # 조문
    articles = convert_articles(root)
    if articles:
        parts.append(articles)

    # 부칙
    addenda = convert_addenda(root)
    if addenda:
        parts.append(addenda)

    return "\n".join(parts) + "\n"


def get_output_dir(law_type: str) -> Path:
    """법령 유형에 따른 출력 디렉토리를 반환한다."""
    dir_name = get_law_type_dir(law_type)
    return Path(OUTPUT_DIR) / "korea" / dir_name


def process_single_law(law: dict) -> Path | None:
    """단일 법령을 처리하여 Markdown 파일로 저장한다."""
    mst = law["법령일련번호"]
    name = law["법령명한글"]
    law_type = law["법령구분명"]

    try:
        root = fetch_law_content(mst)
    except Exception as e:
        print(f"\n[오류] {name} (MST={mst}) 조회 실패: {e}")
        return None

    try:
        markdown = convert_law_to_markdown(root)
    except Exception as e:
        print(f"\n[오류] {name} (MST={mst}) 변환 실패: {e}")
        return None

    out_dir = get_output_dir(law_type)
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = sanitize_filename(name) + ".md"
    filepath = out_dir / filename
    filepath.write_text(markdown, encoding="utf-8")
    return filepath


def main():
    if not API_KEY:
        raise RuntimeError(
            "API 키가 설정되지 않았습니다. "
            "환경변수 LAW_API_KEY를 설정하거나 config.py에서 직접 입력하세요."
        )

    law_list_path = Path(__file__).parent / "data" / "law_list.json"
    if not law_list_path.exists():
        raise FileNotFoundError(
            f"{law_list_path}가 없습니다. 먼저 fetch_law_list.py를 실행하세요."
        )

    laws = json.loads(law_list_path.read_text(encoding="utf-8"))
    print(f"총 {len(laws)}개 법령을 변환합니다.")
    print(f"출력 경로: {OUTPUT_DIR}/korea/")

    success = 0
    fail = 0

    for law in tqdm(laws, desc="법령 변환"):
        result = process_single_law(law)
        if result:
            success += 1
        else:
            fail += 1
        time.sleep(REQUEST_DELAY)

    print(f"\n완료: 성공 {success}건, 실패 {fail}건")


if __name__ == "__main__":
    main()
