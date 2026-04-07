"""현행 법령의 증분 업데이트를 수행한다.

korea-law 저장소의 기존 마크다운 파일 frontmatter를 읽어
API에서 가져온 최신 목록과 비교하여 변경된 법령만 갱신한다.
변경 감지 기준: 법령ID + 공포일자 + 시행일자
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

from tqdm import tqdm

from config import API_KEY, OUTPUT_DIR, REQUEST_DELAY, get_law_type_dir
from fetch_law_list import fetch_all_laws
from fetch_law_content import (
    convert_law_to_markdown,
    fetch_law_content,
    sanitize_filename,
)

# 커밋 작성자 정보
AUTHOR_NAME = "korea-law-bot"
AUTHOR_EMAIL = "bot@korea-law"


def git_cmd(args: list[str], cwd: str, env: dict | None = None) -> str:
    """Git 명령을 실행한다."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=full_env,
    )
    if result.returncode != 0:
        combined = result.stdout + result.stderr
        if "nothing to commit" not in combined:
            print(f"[git 오류] git {' '.join(args[:3])}: {combined.strip()[:200]}")
    return result.stdout.strip()


def parse_frontmatter(filepath: Path) -> dict | None:
    """마크다운 파일의 YAML frontmatter에서 법령ID, 공포일자, 시행일자를 추출한다."""
    try:
        # frontmatter는 파일 앞부분에만 있으므로 일부만 읽는다
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read(2048)
    except Exception:
        return None

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return None

    fm = match.group(1)
    result = {}
    for key in ("법령ID", "공포일자", "시행일자", "법령명", "법령구분"):
        m = re.search(rf'^{key}:\s*"?([^"\n]+)"?', fm, re.MULTILINE)
        if m:
            result[key] = m.group(1).strip()

    return result if "법령ID" in result else None


def scan_existing_laws(repo_dir: str) -> dict[str, dict]:
    """korea-law 저장소의 모든 법령 파일을 스캔하여 현재 상태를 반환한다.

    Returns:
        {법령ID: {"법령ID", "공포일자", "시행일자", "법령명", "법령구분", "filepath"}}
    """
    existing = {}
    repo_path = Path(repo_dir)

    for md_file in repo_path.rglob("*.md"):
        # README.md 등 제외
        if md_file.name == "README.md":
            continue

        fm = parse_frontmatter(md_file)
        if fm and "법령ID" in fm:
            fm["filepath"] = str(md_file)
            existing[fm["법령ID"]] = fm

    return existing


def get_output_path(law_name: str, law_type: str) -> Path:
    """법령의 출력 경로를 반환한다."""
    dir_name = get_law_type_dir(law_type)
    return Path(OUTPUT_DIR) / dir_name / (sanitize_filename(law_name) + ".md")


def make_law_key(pub_date: str, ef_date: str) -> str:
    """비교용 키를 생성한다."""
    return f"{pub_date}|{ef_date}"


def find_changes(existing: dict[str, dict], api_laws: list[dict]) -> dict:
    """기존 파일 상태와 API 최신 목록을 비교하여 변경 사항을 찾는다.

    Returns:
        {
            "added": [...],     # 새로 추가된 법령
            "modified": [...],  # 변경된 법령
            "removed": [...],   # 삭제된 법령 (기존 파일 정보)
        }
    """
    api_by_id = {}
    for law in api_laws:
        api_by_id[law["법령ID"]] = law

    existing_ids = set(existing.keys())
    api_ids = set(api_by_id.keys())

    added = [api_by_id[lid] for lid in sorted(api_ids - existing_ids)]
    removed = [existing[lid] for lid in sorted(existing_ids - api_ids)]

    modified = []
    for lid in (existing_ids & api_ids):
        ex = existing[lid]
        api = api_by_id[lid]

        # frontmatter의 날짜는 YYYY-MM-DD, API는 YYYYMMDD → 비교용 정규화
        ex_pub = ex.get("공포일자", "").replace("-", "")
        ex_ef = ex.get("시행일자", "").replace("-", "")
        api_pub = api.get("공포일자", "")
        api_ef = api.get("시행일자", "")

        if make_law_key(ex_pub, ex_ef) != make_law_key(api_pub, api_ef):
            # 기존 파일 경로를 API 데이터에 추가하여 덮어쓰기 시 사용
            api["_existing_filepath"] = ex.get("filepath")
            modified.append(api)

    return {"added": added, "modified": modified, "removed": removed}


def process_law(law: dict, existing_filepath: str | None = None) -> bool:
    """법령 콘텐츠를 가져와 마크다운 파일로 저장한다.

    existing_filepath가 주어지면 해당 경로에 덮어쓴다 (수정 시).
    없으면 get_output_path로 새 경로를 생성한다 (추가 시).
    """
    mst = law["법령일련번호"]
    name = law["법령명한글"]
    law_type = law["법령구분명"]

    try:
        root = fetch_law_content(mst)
    except Exception as e:
        print(f"[오류] {name} (MST={mst}) 조회 실패: {e}")
        return False

    try:
        markdown = convert_law_to_markdown(root)
    except Exception as e:
        print(f"[오류] {name} (MST={mst}) 변환 실패: {e}")
        return False

    if existing_filepath:
        filepath = Path(existing_filepath)
    else:
        filepath = get_output_path(name, law_type)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(markdown, encoding="utf-8")
    return True


def remove_law_file(law_info: dict):
    """폐지된 법령의 파일을 삭제한다."""
    filepath = Path(law_info.get("filepath", ""))
    if filepath.exists():
        filepath.unlink()
        print(f"[삭제] {law_info.get('법령명', filepath.stem)}")


def ensure_repo(repo_dir: str):
    """korea-law 저장소가 준비되어 있는지 확인한다."""
    git_dir = Path(repo_dir) / ".git"
    if not git_dir.exists():
        raise RuntimeError(
            f"{repo_dir}에 Git 저장소가 없습니다. "
            "build_history.py를 먼저 실행하여 저장소를 초기화하세요."
        )


def build_commit_message(law: dict, action: str) -> str:
    """법령 정보로 커밋 메시지를 생성한다."""
    name = law.get("법령명한글") or law.get("법령명", "")
    law_type = law.get("법령구분명") or law.get("법령구분", "")
    pub_date = law.get("공포일자", "")
    ef_date = law.get("시행일자", "")
    pub_no = law.get("공포번호", "").strip()
    ministry = law.get("소관부처명", "").strip()
    amend_type = law.get("제개정구분명", "").strip()

    # 제목줄
    if amend_type:
        title = f"{name} {amend_type}"
    else:
        title = f"{name} {action}"

    # 본문
    body_lines = []
    if law_type:
        body_lines.append(f"법령구분: {law_type}")
    if pub_no:
        body_lines.append(f"공포번호: {pub_no}")
    if pub_date:
        body_lines.append(f"공포일자: {format_date(pub_date)}")
    if ef_date:
        body_lines.append(f"시행일자: {format_date(ef_date)}")
    if ministry:
        body_lines.append(f"소관부처: {ministry}")

    if body_lines:
        return title + "\n\n" + "\n".join(body_lines)
    return title


def format_date(date_str: str) -> str:
    """YYYYMMDD → YYYY-MM-DD 표시용."""
    date_str = date_str.replace("-", "")
    if len(date_str) == 8:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return date_str


def format_git_date(date_str: str) -> str:
    """공포일자(YYYYMMDD)를 Git 날짜 형식으로 변환한다."""
    date_str = date_str.replace("-", "")
    if len(date_str) == 8:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} 12:00:00 +0900"
    return date_str


def commit_one(repo_dir: str, message: str, pub_date: str = "") -> bool:
    """단일 법령 변경을 커밋한다. pub_date가 있으면 커밋 날짜로 사용."""
    git_cmd(["add", "-A"], cwd=repo_dir)

    status = git_cmd(["status", "--porcelain"], cwd=repo_dir)
    if not status:
        return False

    env = {
        "GIT_AUTHOR_NAME": AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
        "GIT_COMMITTER_NAME": AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL,
    }

    if pub_date:
        git_date = format_git_date(pub_date)
        env["GIT_AUTHOR_DATE"] = git_date
        env["GIT_COMMITTER_DATE"] = git_date

    git_cmd(["commit", "-m", message], cwd=repo_dir, env=env)
    return True


def main():
    if not API_KEY:
        raise RuntimeError(
            "API 키가 설정되지 않았습니다. "
            "환경변수 LAW_API_KEY를 설정하거나 config.py에서 직접 입력하세요."
        )

    repo_dir = OUTPUT_DIR
    ensure_repo(repo_dir)

    print("=== 증분 업데이트 시작 ===")

    # 1. korea-law 저장소의 기존 파일에서 현재 상태 스캔
    print("기존 법령 파일 스캔 중...")
    existing = scan_existing_laws(repo_dir)
    print(f"기존 법령 수: {len(existing)}")

    # 2. 최신 목록 수집
    print("최신 법령 목록 수집 중...")
    new_laws = fetch_all_laws()
    print(f"최신 법령 수: {len(new_laws)}")

    # 3. 변경 사항 비교
    changes = find_changes(existing, new_laws)
    n_added = len(changes["added"])
    n_modified = len(changes["modified"])
    n_removed = len(changes["removed"])
    print(f"변경 사항: 추가 {n_added}건, 수정 {n_modified}건, 삭제 {n_removed}건")

    if n_added == 0 and n_modified == 0 and n_removed == 0:
        print("변경 사항 없음. 종료합니다.")
        return

    commits = 0

    # 4. 삭제된 법령 처리 (각각 커밋)
    for law_info in changes["removed"]:
        remove_law_file(law_info)
        msg = build_commit_message(law_info, "폐지")
        pub_date = law_info.get("공포일자", "")
        if commit_one(repo_dir, msg, pub_date=pub_date):
            commits += 1

    # 5. 추가/수정된 법령 처리 (각각 커밋)
    to_update = changes["added"] + changes["modified"]
    success = 0
    fail = 0

    for law in tqdm(to_update, desc="법령 업데이트"):
        existing_path = law.get("_existing_filepath")
        if process_law(law, existing_filepath=existing_path):
            is_added = law in changes["added"]
            action = "신규" if is_added else "변경"
            msg = build_commit_message(law, action)
            pub_date = law.get("공포일자", "")
            if commit_one(repo_dir, msg, pub_date=pub_date):
                commits += 1
            success += 1
        else:
            fail += 1
        time.sleep(REQUEST_DELAY)

    print(f"처리 완료: 성공 {success}건, 실패 {fail}건, 커밋 {commits}건")

    # 6. 푸시
    if commits > 0:
        git_cmd(["push", "origin", "main"], cwd=repo_dir)
        print(f"푸시 완료: {commits}건 커밋")
    print("=== 증분 업데이트 완료 ===")


if __name__ == "__main__":
    main()
