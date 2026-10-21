# Streamlit Community Cloud 배포 안내

## 업로드 대상

GitHub 저장소에 프로젝트 폴더의 다음 파일과 폴더를 포함합니다.

- `app.py`, `smtp_mailer.py`, `requirements.txt`
- `.streamlit/config.toml`, `components/segmented_password_input/index.html`
- `data/region_assignments.json`, `data/region_population.json`, `data/population_source.json`, `data/sigungu.geojson`, `data/지도_출처.txt`
- `폰트.ttf`

`OPENAI_API_KEY.env`, `.env`, `.streamlit/secrets.toml`, 신청/메일 이력 CSV는 저장소에 올리지 않습니다. `.gitignore`가 비밀 파일과 런타임 데이터 파일을 제외합니다. `.streamlit/secrets.toml.example`은 값 입력 형식을 보여주는 빈 템플릿입니다.

## 배포 순서

1. 위 파일을 GitHub 저장소의 기본 브랜치에 올립니다.
2. Streamlit Community Cloud에서 **Create app**을 선택하고 저장소와 브랜치를 지정합니다.
3. 앱 파일 경로를 `app.py`로 설정합니다.
4. Advanced settings에서 Python **3.12**를 선택합니다. 로컬에서 확인한 Python 계열과 맞춘 설정입니다.
5. Secrets 칸에 `.streamlit/secrets.toml.example`의 형식을 복사하고 실제 키와 SMTP 값을 입력합니다. 실제 사용하지 않을 값은 생략할 수 있습니다.
6. 저장하고 배포한 뒤 소개 화면, CSV 업로드·배분, 지도, 메일 초안 기능을 확인합니다.

## Secrets 형식

```toml
OPENAI_API_KEY = "실제 OpenAI API 키"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = "587"
SMTP_USER = "계정 이메일"
SMTP_PASSWORD = "서비스 앱 비밀번호"
SMTP_FROM = "발신 이메일"
```

OpenAI 키가 없으면 AI 초안 기능만 사용할 수 없습니다. SMTP 설정이 없으면 실제 메일 발송이 비활성화됩니다. Secrets는 앱 코드에서 환경 변수로 읽히므로 기존 OpenAI·SMTP 모듈을 그대로 사용합니다.

## 배포 전에 확인할 점

- Community Cloud의 파일 저장소는 영구 데이터베이스가 아닙니다. `data/applications.csv`, `data/mail_log.csv`, 지도 권역 오버라이드는 앱 실행 중 파일로 저장되지만 앱 재시작·재배포 후에도 보존된다고 보장할 수 없습니다. 지속적인 운영 데이터가 필요하면 외부 데이터베이스 연동이 추가로 필요합니다.
- 현재 앱에는 로그인/사용자 권한 기능이 없습니다. 실제 고객 정보를 업로드하거나 실제 발송 계정을 연결하기 전에 앱 접근 범위를 제한하거나 인증을 추가해야 합니다.
- `data/sigungu.geojson` 지도 경계와 `폰트.ttf`는 앱 실행에 필요한 정적 파일이므로 저장소에 포함해야 합니다. `run.bat`은 Windows 로컬 실행용이며 Community Cloud에서는 사용하지 않습니다.
