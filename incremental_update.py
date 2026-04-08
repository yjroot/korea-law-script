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

from datetime import datetime, timedelta

from config import API_KEY, OUTPUT_DIR, REQUEST_DELAY, get_law_type_dir
from fetch_law_list import fetch_all_laws, fetch_laws_since
from fetch_law_content import (
    convert_law_to_markdown,
    fetch_law_content,
    sanitize_filename,
)
from fetch_history import fetch_law_history

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


def process_law(law: dict, existing_filepath: str | None = None) -> str | None:
    """법령 콘텐츠를 가져와 마크다운 파일로 저장한다.

    existing_filepath가 주어지면 해당 경로에 덮어쓴다 (수정 시).
    없으면 get_output_path로 새 경로를 생성한다 (추가 시).

    Returns:
        성공 시 저장된 파일 경로 문자열, 실패 시 None.
    """
    mst = law["법령일련번호"]
    name = law["법령명한글"]
    law_type = law["법령구분명"]

    try:
        root = fetch_law_content(mst)
    except Exception as e:
        print(f"[오류] {name} (MST={mst}) 조회 실패: {e}")
        return None

    try:
        markdown = convert_law_to_markdown(root)
    except Exception as e:
        print(f"[오류] {name} (MST={mst}) 변환 실패: {e}")
        return None

    new_filepath = get_output_path(name, law_type)
    new_filepath.parent.mkdir(parents=True, exist_ok=True)

    # 법령명이 바뀌어 경로가 달라진 경우, 기존 파일 삭제
    if existing_filepath:
        old_path = Path(existing_filepath)
        if old_path != new_filepath and old_path.is_file():
            old_path.unlink()

    new_filepath.write_text(markdown, encoding="utf-8")
    return str(new_filepath)


def remove_law_file(law_info: dict):
    """폐지된 법령의 파일을 삭제한다."""
    filepath_str = law_info.get("filepath", "")
    if not filepath_str:
        return
    filepath = Path(filepath_str)
    if filepath.is_file():
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
    """공포일자(YYYYMMDD)를 Git 날짜 형식으로 변환한다.

    Git은 1970-01-01 이전 날짜를 지원하지 않으므로 대체한다.
    """
    date_str = date_str.replace("-", "")
    if len(date_str) == 8:
        year = int(date_str[:4])
        if year < 1970:
            return "1970-01-01 12:00:00 +0900"
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

    # 2. 최근 변경된 법령만 수집
    #    기존 법령 중 가장 최근 공포일자에서 30일 전부터 조회
    #    (여유를 두어 누락 방지)
    max_pub = max(
        (v.get("공포일자", "").replace("-", "") for v in existing.values()),
        default="",
    )
    if max_pub:
        try:
            since_date = datetime.strptime(max_pub, "%Y%m%d") - timedelta(days=30)
            since_str = since_date.strftime("%Y%m%d")
        except ValueError:
            since_str = ""
    else:
        since_str = ""

    if since_str:
        print(f"최근 법령 목록 수집 중 (공포일자 {since_str} 이후)...")
        recent_laws = fetch_laws_since(since_str)
        print(f"최근 변경 법령 수: {len(recent_laws)}")
    else:
        print("기준 날짜 없음, 전체 목록 수집...")
        recent_laws = fetch_all_laws()
        print(f"전체 법령 수: {len(recent_laws)}")

    # 3. 변경 사항 비교 (최근 법령만 대상, 삭제는 감지 불가)
    changes = find_changes(existing, recent_laws)
    # 날짜 범위 조회이므로 removed는 무시 (실제 폐지가 아닌 범위 밖 법령)
    changes["removed"] = []
    n_added = len(changes["added"])
    n_modified = len(changes["modified"])
    n_removed = len(changes["removed"])
    print(f"변경 사항: 추가 {n_added}건, 수정 {n_modified}건, 삭제 {n_removed}건")

    if n_added == 0 and n_modified == 0 and n_removed == 0:
        print("변경 사항 없음. 종료합니다.")
        return

    # 4. 모든 변경을 작업 목록으로 수집
    #    각 작업: {"action", "law", "pub_date", "existing_filepath"}
    tasks = []

    # 삭제된 법령
    for law_info in changes["removed"]:
        tasks.append({
            "action": "폐지",
            "law": law_info,
            "pub_date": law_info.get("공포일자", "").replace("-", ""),
            "existing_filepath": law_info.get("filepath"),
        })

    # 추가된 법령
    for law in changes["added"]:
        tasks.append({
            "action": "신규",
            "law": law,
            "pub_date": law.get("공포일자", ""),
            "existing_filepath": None,
        })

    # 수정된 법령: 연혁 조회하여 중간 개정 포함
    for law in tqdm(changes["modified"], desc="변경 법령 연혁 조회"):
        existing_path = law.get("_existing_filepath")
        ex_pub = existing.get(law["법령ID"], {}).get("공포일자", "").replace("-", "")

        try:
            history = fetch_law_history(law["법령명한글"])
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"[오류] {law['법령명한글']} 연혁 조회 실패: {e}")
            # 연혁 조회 실패 시 현행 버전만 추가
            tasks.append({
                "action": "변경",
                "law": law,
                "pub_date": law.get("공포일자", ""),
                "existing_filepath": existing_path,
            })
            continue

        # 기존 공포일자 이후의 개정만 필터
        newer = [h for h in history if h["공포일자"] > ex_pub]

        if not newer:
            tasks.append({
                "action": "변경",
                "law": law,
                "pub_date": law.get("공포일자", ""),
                "existing_filepath": existing_path,
            })
        else:
            for rev in newer:
                tasks.append({
                    "action": "변경",
                    "law": rev,
                    "pub_date": rev.get("공포일자", ""),
                    "existing_filepath": existing_path,
                })

    # 5. 공포일자 기준 시간순 정렬
    tasks.sort(key=lambda t: t["pub_date"])
    print(f"총 {len(tasks)}건의 작업을 시간순으로 처리합니다.")

    # 6. 시간순으로 처리 및 커밋
    commits = 0
    success = 0
    fail = 0
    # 법령ID → 최신 파일 경로 추적 (같은 법령의 연속 개정 시 경로 갱신)
    path_tracker: dict[str, str] = {}

    for task in tqdm(tasks, desc="법령 커밋"):
        law = task["law"]
        action = task["action"]
        pub_date = task["pub_date"]
        law_id = law.get("법령ID", "")

        # 이전 처리에서 갱신된 경로가 있으면 우선 사용
        existing_path = path_tracker.get(law_id) or task["existing_filepath"]

        if action == "폐지":
            remove_law_file(law)
            msg = build_commit_message(law, action)
            if commit_one(repo_dir, msg, pub_date=pub_date):
                commits += 1
            success += 1
        else:
            written_path = process_law(law, existing_filepath=existing_path)
            if written_path:
                msg = build_commit_message(law, action)
                if commit_one(repo_dir, msg, pub_date=pub_date):
                    commits += 1
                path_tracker[law_id] = written_path
                success += 1
            else:
                fail += 1
            time.sleep(REQUEST_DELAY)

    print(f"처리 완료: 성공 {success}건, 실패 {fail}건, 커밋 {commits}건")

    # 7. 푸시
    if commits > 0:
        git_cmd(["push", "origin", "main"], cwd=repo_dir)
        print(f"푸시 완료: {commits}건 커밋")
    print("=== 증분 업데이트 완료 ===")


if __name__ == "__main__":
    main()
