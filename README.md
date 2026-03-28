# korea-law-script

[korea-law](https://github.com/yjroot/korea-law) 저장소를 위한 법령 수집/변환 도구입니다.

법제처 Open API에서 대한민국의 모든 법령을 가져와 Markdown 파일로 변환합니다.

## 설치

```bash
pip install -r requirements.txt
```

## 사전 준비

1. [법제처 Open API](https://open.law.go.kr) 회원가입
2. API 키 발급 (승인 1~2일 소요)
3. 환경변수 설정:

```bash
export LAW_API_KEY="발급받은_API_키"
export KOREA_LAW_DIR="$HOME/korea-law"  # korea-law 저장소 경로
```

## 사용법

### 1단계: 법령 목록 수집

```bash
python fetch_law_list.py
```

전체 법령 목록을 `data/law_list.json`에 저장합니다.

### 2단계: 법령 본문 변환

```bash
python fetch_law_content.py
```

각 법령의 본문을 조회하여 Markdown으로 변환하고 `korea-law` 저장소에 저장합니다.

## 파일 구조

| 파일 | 설명 |
|------|------|
| `config.py` | API 키, URL, 경로 설정 |
| `fetch_law_list.py` | 전체 법령 목록 수집 |
| `fetch_law_content.py` | 법령 본문 → Markdown 변환 |
| `build_history.py` | (향후) 개정 이력을 Git 커밋으로 생성 |

## 데이터 출처

[법제처 국가법령정보센터 Open API](https://open.law.go.kr/)

## 라이선스

MIT
