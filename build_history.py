"""연혁 데이터를 기반으로 korea-law 저장소에 시간순 Git 커밋을 생성한다.

사용법:
    1. fetch_law_list.py 실행 (현행 법령 목록)
    2. fetch_history.py 실행 (연혁 목록)
    3. build_history.py 실행 (Git 커밋 생성)

주의: korea-law 저장소를 초기화(git init)한 후 실행해야 한다.
"""

import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from tqdm import tqdm

from config import API_KEY, OUTPUT_DIR, REQUEST_DELAY, SERVICE_URL, get_law_type_dir
from fetch_law_content import (
    convert_law_to_markdown,
    fetch_law_content,
    sanitize_filename,
)


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
            print(f"\n[git 오류] git {' '.join(args)}: {combined.strip()[:200]}")
    return result.stdout.strip()


def format_git_date(date_str: str) -> str:
    """공포일자(YYYYMMDD)를 Git 날짜 형식으로 변환한다.

    Git은 1970-01-01 이전 날짜를 지원하지 않으므로,
    그 이전 날짜는 1970-01-01로 대체한다.
    """
    date_str = date_str.replace(".", "")
    if len(date_str) == 8:
        year = int(date_str[:4])
        if year < 1970:
            return "1970-01-01 12:00:00 +0900"
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} 12:00:00 +0900"
    return date_str


def get_output_path(law_name: str, law_type: str) -> Path:
    """법령의 출력 경로를 반환한다."""
    dir_name = get_law_type_dir(law_type)
    return Path(OUTPUT_DIR) / "korea" / dir_name / (sanitize_filename(law_name) + ".md")


def process_revision(revision: dict, repo_dir: str) -> bool:
    """단일 연혁 버전을 처리하여 파일을 생성/수정한다."""
    mst = revision["법령일련번호"]
    name = revision["법령명한글"]
    law_type = revision["법령구분명"]

    try:
        root = fetch_law_content(mst)
    except Exception as e:
        print(f"\n[오류] {name} (MST={mst}) 조회 실패: {e}")
        return False

    try:
        markdown = convert_law_to_markdown(root)
    except Exception as e:
        print(f"\n[오류] {name} (MST={mst}) 변환 실패: {e}")
        return False

    filepath = get_output_path(name, law_type)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(markdown, encoding="utf-8")
    return True


def create_commit(revision: dict, repo_dir: str) -> bool:
    """연혁 버전에 대한 Git 커밋을 생성한다."""
    name = revision["법령명한글"]
    amend_type = revision["제개정구분명"]
    pub_date = revision["공포일자"]
    pub_no = revision.get("공포번호", "")

    # 커밋 메시지 생성
    if amend_type == "제정":
        msg = f"{name} 제정"
    elif amend_type == "전부개정":
        msg = f"{name} 전부개정"
    elif amend_type == "일부개정":
        msg = f"{name} 일부개정"
    elif amend_type == "타법개정":
        msg = f"{name} 타법개정"
    elif amend_type == "폐지":
        msg = f"{name} 폐지"
    else:
        msg = f"{name} {amend_type}"

    if pub_no:
        msg += f" ({pub_no})"

    git_date = format_git_date(pub_date)
    date_env = {
        "GIT_AUTHOR_DATE": git_date,
        "GIT_COMMITTER_DATE": git_date,
    }

    # 변경 사항 스테이징
    git_cmd(["add", "-A"], cwd=repo_dir)

    # 변경 사항이 있는지 확인
    status = git_cmd(["status", "--porcelain"], cwd=repo_dir)
    if not status:
        return False  # 변경 없음

    # 커밋
    git_cmd(["commit", "-m", msg], cwd=repo_dir, env=date_env)
    return True


def main():
    if not API_KEY:
        raise RuntimeError(
            "API 키가 설정되지 않았습니다. "
            "환경변수 LAW_API_KEY를 설정하거나 config.py에서 직접 입력하세요."
        )

    history_path = Path(__file__).parent / "data" / "law_history.json"
    if not history_path.exists():
        raise FileNotFoundError(
            f"{history_path}가 없습니다. 먼저 fetch_history.py를 실행하세요."
        )

    raw_history = json.loads(history_path.read_text(encoding="utf-8"))

    # 중복 MST 제거 (현행/시행예정이 같은 MST를 가질 수 있음)
    seen_mst = set()
    history = []
    for h in raw_history:
        if h["법령일련번호"] not in seen_mst:
            seen_mst.add(h["법령일련번호"])
            history.append(h)

    print(f"총 {len(history)}건의 연혁을 처리합니다. (원본 {len(raw_history)}건에서 중복 제거)")

    repo_dir = OUTPUT_DIR
    print(f"저장소 경로: {repo_dir}")

    # 저장소 초기화 확인
    git_dir = Path(repo_dir) / ".git"
    if not git_dir.exists():
        print("Git 저장소를 초기화합니다.")
        git_cmd(["init"], cwd=repo_dir)

    # 기존 법령 파일 삭제 (연혁부터 다시 쌓기 위해)
    korea_dir = Path(repo_dir) / "korea"
    if korea_dir.exists():
        import shutil
        shutil.rmtree(korea_dir)
        git_cmd(["add", "-A"], cwd=repo_dir)
        status = git_cmd(["status", "--porcelain"], cwd=repo_dir)
        if status:
            git_cmd(["commit", "-m", "초기화: 연혁 기반 재구성 시작"], cwd=repo_dir)

    success = 0
    fail = 0
    skip = 0

    for rev in tqdm(history, desc="연혁 커밋 생성"):
        ok = process_revision(rev, repo_dir)
        if ok:
            committed = create_commit(rev, repo_dir)
            if committed:
                success += 1
            else:
                skip += 1
        else:
            fail += 1
        time.sleep(REQUEST_DELAY)

    print(f"\n완료: 커밋 {success}건, 스킵 {skip}건, 실패 {fail}건")
    print(f"git log --oneline | head -20 으로 확인하세요.")


if __name__ == "__main__":
    main()
