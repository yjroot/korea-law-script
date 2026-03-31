"""연혁 데이터를 기반으로 korea-law 저장소에 시간순 Git 커밋을 생성한다.

사용법:
    1. fetch_law_list.py 실행 (현행 법령 목록)
    2. fetch_history.py 실행 (연혁 목록)
    3. build_history.py 실행 (Git 커밋 생성)

주의: korea-law 저장소를 완전히 초기화한 후 실행한다.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from tqdm import tqdm

from config import API_KEY, OUTPUT_DIR, REQUEST_DELAY, get_law_type_dir
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
            print(f"\n[git 오류] git {' '.join(args[:3])}: {combined.strip()[:200]}")
    return result.stdout.strip()


def format_date_display(date_str: str) -> str:
    """YYYYMMDD → YYYY-MM-DD 표시용."""
    if len(date_str) == 8:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return date_str


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
    return Path(OUTPUT_DIR) / dir_name / (sanitize_filename(law_name) + ".md")


def process_revision(revision: dict) -> bool:
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


def build_commit_message(revision: dict) -> str:
    """연혁 정보로 커밋 메시지를 생성한다."""
    name = revision["법령명한글"]
    amend_type = revision["제개정구분명"]
    pub_date = revision["공포일자"]
    ef_date = revision["시행일자"]
    pub_no = revision.get("공포번호", "").strip()
    ministry = revision.get("소관부처명", "").strip()
    law_type = revision.get("법령구분명", "").strip()

    # 제목줄
    title = f"{name} {amend_type}"

    # 본문
    body_lines = []
    body_lines.append(f"법령구분: {law_type}")
    if pub_no:
        body_lines.append(f"공포번호: {pub_no}")
    body_lines.append(f"공포일자: {format_date_display(pub_date)}")
    body_lines.append(f"시행일자: {format_date_display(ef_date)}")
    if ministry:
        body_lines.append(f"소관부처: {ministry}")

    # 1970년 이전 날짜인 경우 안내
    if len(pub_date) == 8 and int(pub_date[:4]) < 1970:
        body_lines.append("")
        body_lines.append(f"(실제 공포일: {format_date_display(pub_date)}, Git 제한으로 커밋 날짜는 1970-01-01)")

    return title + "\n\n" + "\n".join(body_lines)


def create_commit(revision: dict, repo_dir: str) -> bool:
    """연혁 버전에 대한 Git 커밋을 생성한다."""
    pub_date = revision["공포일자"]

    git_date = format_git_date(pub_date)
    date_env = {
        "GIT_AUTHOR_DATE": git_date,
        "GIT_COMMITTER_DATE": git_date,
        "GIT_AUTHOR_NAME": AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
        "GIT_COMMITTER_NAME": AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL,
    }

    msg = build_commit_message(revision)

    # 변경 사항 스테이징
    git_cmd(["add", "-A"], cwd=repo_dir)

    # 변경 사항이 있는지 확인
    status = git_cmd(["status", "--porcelain"], cwd=repo_dir)
    if not status:
        return False  # 변경 없음

    # 커밋
    git_cmd(["commit", "-m", msg], cwd=repo_dir, env=date_env)
    return True


def init_repo(repo_dir: str):
    """저장소를 완전히 초기화한다."""
    git_dir = Path(repo_dir) / ".git"

    # 기존 .git 삭제
    if git_dir.exists():
        shutil.rmtree(git_dir)

    # 기존 법령 디렉토리 삭제
    for item in Path(repo_dir).iterdir():
        if item.is_dir() and item.name not in (".git",):
            shutil.rmtree(item)

    # git init
    git_cmd(["init"], cwd=repo_dir)
    git_cmd(["config", "user.name", AUTHOR_NAME], cwd=repo_dir)
    git_cmd(["config", "user.email", AUTHOR_EMAIL], cwd=repo_dir)

    # README를 root commit으로
    readme_path = Path(repo_dir) / "README.md"
    if readme_path.exists():
        git_cmd(["add", "README.md"], cwd=repo_dir)
        env = {
            "GIT_AUTHOR_NAME": AUTHOR_NAME,
            "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
            "GIT_COMMITTER_NAME": AUTHOR_NAME,
            "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL,
        }
        git_cmd(["commit", "-m", "Initial commit: 프로젝트 설명"], cwd=repo_dir, env=env)

    # remote 설정
    git_cmd(["remote", "add", "origin", "git@github.com:yjroot/korea-law.git"], cwd=repo_dir)

    print(f"저장소 초기화 완료: {repo_dir}")


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

    # 저장소 완전 초기화
    init_repo(repo_dir)

    success = 0
    fail = 0
    skip = 0
    commits_since_push = 0
    push_interval = 100

    for rev in tqdm(history, desc="연혁 커밋 생성"):
        ok = process_revision(rev)
        if ok:
            committed = create_commit(rev, repo_dir)
            if committed:
                success += 1
                commits_since_push += 1

                if commits_since_push >= push_interval:
                    git_cmd(["push", "--force", "origin", "main"], cwd=repo_dir)
                    commits_since_push = 0
            else:
                skip += 1
        else:
            fail += 1
        time.sleep(REQUEST_DELAY)

    # 마지막 남은 커밋 push
    if commits_since_push > 0:
        git_cmd(["push", "--force", "origin", "main"], cwd=repo_dir)

    print(f"\n완료: 커밋 {success}건, 스킵 {skip}건, 실패 {fail}건")
    print(f"git log --oneline | head -20 으로 확인하세요.")


if __name__ == "__main__":
    main()
