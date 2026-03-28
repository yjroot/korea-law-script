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
    LAW_TYPE_DIR,
    OUTPUT_DIR,
    REQUEST_DELAY,
    SERVICE_URL,
)


def sanitize_filename(name: str) -> str:
    """파일명에 사용할 수 없는 문자를 제거한다."""
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = name.strip(". ")
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

    def text(tag: str, parent=None) -> str:
        el = (parent or info).find(tag)
        if el is None:
            el = root.find(f".//{tag}")
        return el.text.strip() if el is not None and el.text else ""

    return {
        "법령명": text("법령명한글"),
        "법령ID": text("법령ID"),
        "법령일련번호": text("법령일련번호"),
        "법령구분": text("법령구분명"),
        "공포일자": text("공포일자"),
        "공포번호": text("공포번호"),
        "시행일자": text("시행일자"),
        "소관부처": text("소관부처명"),
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
    lines.append(f'법령일련번호: {meta["법령일련번호"]}')
    lines.append(f'법령구분: "{meta["법령구분"]}"')
    lines.append(f'공포일자: "{format_date(meta["공포일자"])}"')
    lines.append(f'공포번호: {meta["공포번호"] or 0}')
    lines.append(f'시행일자: "{format_date(meta["시행일자"])}"')
    lines.append(f'소관부처: "{meta["소관부처"]}"')
    lines.append(f'출처: "https://www.law.go.kr/법령/{meta["법령명"]}"')
    lines.append("---")
    return "\n".join(lines)


def xml_text(el: ET.Element | None) -> str:
    """XML 요소의 텍스트를 안전하게 추출한다. 내부 태그 포함."""
    if el is None:
        return ""
    # itertext()로 모든 하위 텍스트 결합
    text = "".join(el.itertext()).strip()
    return text


def convert_articles(root: ET.Element) -> str:
    """조문 XML을 Markdown 본문으로 변환한다."""
    lines = []

    # 편/장/절/관 구조 추적을 위한 현재 헤딩
    for jo in root.iter("조문단위"):
        jo_num = xml_text(jo.find("조문번호"))
        jo_title = xml_text(jo.find("조문제목"))
        jo_content = xml_text(jo.find("조문내용"))

        # 편, 장, 절, 관 구분
        jo_type = xml_text(jo.find("조문구분"))

        if jo_type == "편":
            lines.append(f"\n## {jo_content or jo_title}\n")
            continue
        elif jo_type == "장":
            lines.append(f"\n### {jo_content or jo_title}\n")
            continue
        elif jo_type == "절":
            lines.append(f"\n#### {jo_content or jo_title}\n")
            continue
        elif jo_type == "관":
            lines.append(f"\n##### {jo_content or jo_title}\n")
            continue

        # 일반 조문
        if jo_title:
            lines.append(f"\n###### 제{jo_num}조 ({jo_title})\n")
        elif jo_num:
            lines.append(f"\n###### 제{jo_num}조\n")

        if jo_content:
            lines.append(jo_content)
            lines.append("")

        # 항
        for hang in jo.iter("항"):
            hang_num = xml_text(hang.find("항번호"))
            hang_content = xml_text(hang.find("항내용"))
            if hang_content:
                # 항 번호가 있으면 원문자로 표시
                if hang_num:
                    lines.append(hang_content)
                else:
                    lines.append(hang_content)
                lines.append("")

            # 호
            for ho in hang.iter("호"):
                ho_content = xml_text(ho.find("호내용"))
                if ho_content:
                    lines.append(f"  {ho_content}")

                # 목
                for mok in ho.iter("목"):
                    mok_content = xml_text(mok.find("목내용"))
                    if mok_content:
                        lines.append(f"    {mok_content}")

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
    dir_name = LAW_TYPE_DIR.get(law_type, "기타")
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
