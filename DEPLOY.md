# 웹 배포 가이드 — Streamlit Community Cloud (무료)

## 사전 준비물

- GitHub 계정 (없으면 github.com에서 이메일로 1분 가입)
- 이 폴더(`스팩트래커_웹`) 안의 모든 파일

## 단계 1: GitHub repo 만들기 (5분, 브라우저만 사용)

1. **github.com** 접속 → 우상단 **+** → **New repository**
2. **Repository name**: 원하는 이름 (예: `spac-tracker`)
3. **Public** 선택 (Streamlit Cloud 무료 플랜은 Public repo만 허용)
4. **Create repository** 클릭
5. 빈 repo 페이지에서 **"uploading an existing file"** 링크 클릭
6. `스팩트래커_웹` 폴더 안의 파일을 **드래그앤드롭**:
   - `app.py`, `db.py`, `pipeline.py`, `calc.py`, `excel_export.py`
   - `requirements.txt`, `README.md`, `DEPLOY.md`, `.gitignore`
   - `.streamlit/config.toml`, `.streamlit/secrets.toml.example`
7. 아래쪽 **Commit changes** 클릭

> ⚠️ `spac.db`, `__pycache__/`, `.streamlit/secrets.toml`은 절대 업로드하지 마세요. `.gitignore`가 자동으로 막아주지만 수동 업로드 시 확인 필요.

## 단계 2: Streamlit Cloud 배포 (5분)

1. **share.streamlit.io** 접속 → **Continue with GitHub** 클릭
2. 우상단 **New app** 클릭
3. **Repository**: 방금 만든 repo 선택
4. **Branch**: `main`
5. **Main file path**: `app.py`
6. **App URL**: 원하는 서브도메인 (예: `spac-tracker-kr`)
7. **Deploy!** 클릭

2~3분 기다리면 `https://<서브도메인>.streamlit.app` URL이 생성됩니다.

## 단계 3: 웹 모드 활성화 (필수, 2분)

배포 직후엔 "로컬 모드"로 동작해서 **모든 사용자가 같은 DART API 키를 공유**하게 됩니다. 웹 모드로 바꾸세요:

1. Streamlit Cloud 대시보드 → 배포한 앱 우측 **⋮** → **Settings**
2. **Secrets** 탭 → 입력란에 한 줄 입력:
   ```toml
   IS_WEB = true
   ```
3. **Save** 클릭 → 앱이 자동으로 재시작됨

이제 DART API 키는 각 사용자의 브라우저 세션에만 저장됩니다 (다른 사용자와 분리).

## 단계 4: 공유

- URL을 커뮤니티에 공유:
  > 스팩 청산가치 트래커입니다. 첫 접속 시 좌측에서 OpenDART API 키를 발급(opendart.fss.or.kr)하셔서 입력하시고, [전체 새로고침] 한 번 누르시면 됩니다.

## 자주 묻는 질문

**Q. 데이터가 사라졌어요**
A. Streamlit Cloud 컨테이너가 재시작되면 SQLite가 초기화됩니다. KRX 종목 목록과 DART 데이터는 사용자가 "전체 새로고침"을 누르면 자동으로 다시 채워집니다. KSFC 금리와 수동 보정만 재입력하시면 됩니다.

**Q. 동시 접속자가 많으면 느려지나요**
A. Streamlit Cloud 무료 플랜은 1GB 메모리·CPU 공유 환경입니다. 동시 접속 100명 정도까지는 큰 문제 없습니다. 더 많은 트래픽이 필요하면 유료 플랜으로 업그레이드하세요.

**Q. 코드를 수정하면 자동 반영되나요**
A. 네. GitHub에 push(또는 웹 UI로 commit)하면 Streamlit Cloud가 자동으로 재배포합니다.

**Q. 로컬에서도 같은 코드를 쓸 수 있나요**
A. 네. `IS_WEB` secret이 없거나 `false`면 로컬 모드로 동작 — DART 키가 DB에 영구 저장됩니다. 같은 코드로 두 환경 모두 지원합니다.

**Q. 무료 플랜으로 충분한가요**
A. 일반적인 투자 커뮤니티 규모(수십~수백 명)는 충분합니다. 트래픽이 매우 많거나 24/7 운영이 중요하면 유료 또는 자체 호스팅을 고려하세요.
