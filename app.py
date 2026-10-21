from __future__ import annotations

import base64
import copy
import io
import hashlib
from io import BytesIO
import json
import math
import os
import random
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import altair as alt
import pydeck as pdk
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from smtp_mailer import send_email, smtp_is_configured

st.set_page_config(page_title="마케팅 신청 배분 대시보드", page_icon="📬", layout="wide", initial_sidebar_state="expanded")

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
APPLICATIONS_PATH = DATA_DIR / "applications.csv"
MAIL_LOG_PATH = DATA_DIR / "mail_log.csv"
REGION_OVERRIDES_PATH = DATA_DIR / "region_overrides.json"
REGION_ASSIGNMENTS_PATH = DATA_DIR / "region_assignments.json"
SIGUNGU_GEOJSON_PATH = DATA_DIR / "sigungu.geojson"
TODAY = date.today()
MODEL = "gpt-6-luna"
segmented_password_input = components.declare_component(
    "segmented_password_input",
    path=str(APP_DIR / "components" / "segmented_password_input"),
)

# Prefer the requested .env file, but keep compatibility with the pre-existing
# OPENAI_API_KEY.env in this project. Values are never displayed in the UI.
load_dotenv(APP_DIR / ".env", override=False)
if not os.getenv("OPENAI_API_KEY"):
    load_dotenv(APP_DIR / "OPENAI_API_KEY.env", override=False)

# Streamlit Community Cloud provides credentials through st.secrets. Mirror
# those values to the environment so the existing SMTP/OpenAI adapters work
# unchanged locally and in the cloud.
try:
    cloud_secrets = st.secrets
except Exception:
    cloud_secrets = {}
for secret_name in ("OPENAI_API_KEY", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"):
    if not os.getenv(secret_name):
        try:
            secret_value = cloud_secrets.get(secret_name)
        except Exception:
            secret_value = None
        if secret_value:
            os.environ[secret_name] = str(secret_value)

BRANCHES = ["서울서부", "서울동부", "수원", "천안", "광주", "부산", "대구"]
CHANNELS = ["맞춤정보", "마케팅", "캐시백미션", "외부광고"]
CONTACTS = {
    "서울서부": ("김하늘", "seoul-west@example.com", "서울동부 → 수원"),
    "서울동부": ("이도윤", "seoul-east@example.com", "수원 → 서울서부"),
    "수원": ("박서연", "suwon@example.com", "천안 → 서울동부"),
    "천안": ("최민준", "cheonan@example.com", "수원 → 광주"),
    "광주": ("정유진", "gwangju@example.com", "부산 → 천안"),
    "부산": ("한지우", "busan@example.com", "대구 → 광주"),
    "대구": ("윤서준", "daegu@example.com", "부산 → 천안"),
}

PROVINCE_ALIASES = {
    "서울": "서울특별시", "서울시": "서울특별시", "서울특별시": "서울특별시",
    "부산": "부산광역시", "부산시": "부산광역시", "부산광역시": "부산광역시",
    "대구": "대구광역시", "대구시": "대구광역시", "대구광역시": "대구광역시",
    "인천": "인천광역시", "인천시": "인천광역시", "인천광역시": "인천광역시",
    "광주": "광주광역시", "광주시": "광주광역시", "광주광역시": "광주광역시",
    "대전": "대전광역시", "대전시": "대전광역시", "대전광역시": "대전광역시",
    "울산": "울산광역시", "울산시": "울산광역시", "울산광역시": "울산광역시",
    "세종": "세종특별자치시", "세종시": "세종특별자치시", "세종특별자치시": "세종특별자치시",
    "경기": "경기도", "경기도": "경기도", "강원": "강원특별자치도", "강원도": "강원특별자치도", "강원특별자치도": "강원특별자치도",
    "충북": "충청북도", "충청북도": "충청북도", "충남": "충청남도", "충청남도": "충청남도",
    "전북": "전북특별자치도", "전라북도": "전북특별자치도", "전북특별자치도": "전북특별자치도",
    "전남": "전라남도", "전라남도": "전라남도", "경북": "경상북도", "경상북도": "경상북도",
    "경남": "경상남도", "경상남도": "경상남도", "제주": "제주특별자치도", "제주도": "제주특별자치도", "제주특별자치도": "제주특별자치도",
}

NAME_ALIASES = {
    "신청자 이름": ["신청자 이름", "이름", "성명", "name", "applicant_name"],
    "신청일자": ["신청일자", "신청일", "접수일", "신청 날짜", "application_date", "date"],
    "신청 광역시도": ["신청 광역시도", "광역시도", "시도", "시/도", "province", "sido"],
    "신청 시·군·구": ["신청 시·군·구", "신청 시군구", "시군구", "시·군·구", "구군", "district", "sigungu"],
    "신청지역": ["신청지역", "지역", "주소(시군구)", "location", "address"],
    "신청번호": ["신청번호", "신청 ID", "신청ID", "접수번호", "application_id", "id"],
    "인입채널": ["인입채널", "유입채널", "마케팅 채널", "channel", "source_channel"],
}


def read_store(path: Path) -> pd.DataFrame:
    if path.exists():
        try:
            return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        except (pd.errors.EmptyDataError, UnicodeDecodeError):
            return pd.DataFrame()
    return pd.DataFrame()


def save_store(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def normalize_header(value: object) -> str:
    return re.sub(r"[\s_\-·]", "", str(value).strip().lower())


def find_column(columns, aliases):
    lookup = {normalize_header(c): c for c in columns}
    for alias in aliases:
        key = normalize_header(alias)
        if key in lookup:
            return lookup[key]
    return None


def normalize_province(value: object) -> str:
    text = str(value).strip()
    return PROVINCE_ALIASES.get(text, text)


def region_key(province: object, district: object) -> str:
    return f"{normalize_province(province)}|{str(district).strip()}"


def load_region_overrides() -> dict[str, str]:
    try:
        payload = json.loads(REGION_OVERRIDES_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return {str(key): str(branch) for key, branch in payload.items() if branch in BRANCHES}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


REGION_OVERRIDES = load_region_overrides()


def load_region_assignments() -> dict[str, str]:
    """Load the complete default mapping for the current nationwide SGG list."""
    try:
        payload = json.loads(REGION_ASSIGNMENTS_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            assignments = {}
            for raw_key, branch in payload.items():
                if branch not in BRANCHES or "|" not in str(raw_key):
                    continue
                province, district = str(raw_key).split("|", 1)
                assignments[region_key(province, district)] = str(branch)
            return assignments
    except (OSError, json.JSONDecodeError):
        pass
    return {}


REGION_ASSIGNMENTS = load_region_assignments()


def split_combined_region(value: object) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", str(value).strip())
    tokens = text.split(" ")
    if len(tokens) < 2:
        return "", ""
    province = PROVINCE_ALIASES.get(tokens[0])
    if province:
        return (province, tokens[1]) if len(tokens) == 2 else ("", "")
    return "", ""


def assign_branch(province_value: object, district_value: object) -> tuple[str, str]:
    province = normalize_province(province_value)
    district = str(district_value).strip()
    if not province or not district:
        return "", "광역시도와 시·군·구를 모두 입력해야 합니다."
    key = region_key(province, district)
    override = REGION_OVERRIDES.get(key)
    if override:
        return override, "권역 설정 페이지에서 직접 지정"
    branch = REGION_ASSIGNMENTS.get(key)
    if branch:
        return branch, "전국 시·군·구 기본 배분표"
    return "", "기본 배분표에 등록되지 않은 시·군·구입니다. 권역 설정에서 담당 지점을 지정해 주세요."


def parse_upload(uploaded_file) -> tuple[pd.DataFrame, list[str]]:
    raw_bytes = uploaded_file.getvalue()
    frame = None
    last_error = None
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            frame = pd.read_csv(io.BytesIO(raw_bytes), dtype=str, keep_default_na=False, encoding=encoding)
            break
        except Exception as exc:  # encoding/parser fallback
            last_error = exc
    if frame is None:
        raise ValueError(f"CSV를 읽을 수 없습니다: {last_error}")
    errors = []
    mapping = {target: find_column(frame.columns, aliases) for target, aliases in NAME_ALIASES.items()}
    missing = [field for field in ["신청자 이름", "신청일자", "인입채널"] if not mapping[field]]
    if not mapping["신청지역"] and not (mapping["신청 광역시도"] and mapping["신청 시·군·구"]):
        missing.append("신청지역 또는 신청 광역시도+신청 시·군·구")
    if missing:
        raise ValueError("필수 열을 찾지 못했습니다: " + ", ".join(missing))
    out = pd.DataFrame(index=frame.index)
    for field in ["신청자 이름", "신청일자", "신청번호"]:
        src = mapping[field]
        out[field] = frame[src].astype(str).str.strip() if src else ""
    out["인입채널"] = frame[mapping["인입채널"]].astype(str).str.strip()
    if mapping["신청 광역시도"] and mapping["신청 시·군·구"]:
        out["신청 광역시도"] = frame[mapping["신청 광역시도"]].map(normalize_province)
        out["신청 시·군·구"] = frame[mapping["신청 시·군·구"]].astype(str).str.strip()
    else:
        split = frame[mapping["신청지역"]].map(split_combined_region)
        out["신청 광역시도"] = split.map(lambda pair: pair[0])
        out["신청 시·군·구"] = split.map(lambda pair: pair[1])
    out["신청일자"] = pd.to_datetime(out["신청일자"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    out["신청지역"] = out["신청 광역시도"].str.cat(out["신청 시·군·구"], sep=" ").str.strip()
    out["배정 지점"] = ""
    out["배분 사유"] = ""
    out["처리 상태"] = "배분 완료"
    out["담당자"] = ""
    out["담당자 이메일"] = ""
    out["배분 일시"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for idx, row in out.iterrows():
        row_valid = True
        if not row["신청자 이름"]:
            errors.append(f"{idx + 2}행: 신청자 이름이 비어 있습니다.")
            row_valid = False
        if not row["신청일자"]:
            errors.append(f"{idx + 2}행: 신청일자를 날짜로 읽을 수 없습니다.")
            row_valid = False
        if row["인입채널"] not in CHANNELS:
            errors.append(f"{idx + 2}행: 인입채널은 {', '.join(CHANNELS)} 중 하나여야 합니다.")
            row_valid = False
        branch, reason = assign_branch(row["신청 광역시도"], row["신청 시·군·구"])
        out.at[idx, "배정 지점"] = branch
        out.at[idx, "배분 사유"] = reason
        if branch:
            contact, email, _ = CONTACTS[branch]
            out.at[idx, "담당자"] = contact
            out.at[idx, "담당자 이메일"] = email
        else:
            out.at[idx, "처리 상태"] = "미배분"
            errors.append(f"{idx + 2}행: {reason}")
        if not row_valid:
            out.at[idx, "처리 상태"] = "검토 필요"
    return out, errors


def dedupe_key(row: pd.Series) -> str:
    app_id = str(row.get("신청번호", "")).strip()
    if app_id:
        return "ID:" + app_id
    parts = [str(row.get(k, "")).strip().casefold() for k in ["신청자 이름", "신청일자", "신청 광역시도", "신청 시·군·구"]]
    return "ROW:" + "|".join(parts)


def make_local_draft(branch: str, group: pd.DataFrame, cumulative_count: int) -> str:
    contact = CONTACTS[branch][0]
    count = len(group)
    today_text = TODAY.strftime("%Y년 %m월 %d일")
    styles = [
        (f"안녕하세요, {contact} 담당자님.", "늘 협조해 주셔서 감사합니다.", "확인해 주셔서 감사드리며, 진행 중 문의사항이 있으시면 편하게 말씀 부탁드립니다."),
        (f"{contact} 담당자님, 안녕하세요.", "신청 고객 정보를 아래와 같이 전달드립니다.", "바쁘시겠지만 확인과 진행을 부탁드립니다. 감사합니다."),
        (f"안녕하세요, {contact} 담당자님.", "상담에 참고하실 수 있도록 신청 고객 정보를 공유드립니다.", "협조에 감사드립니다. 잘 부탁드리겠습니다."),
    ]
    greeting, intro, closing = styles[cumulative_count % len(styles)]
    customer_lines = [f"{i}. {r['신청자 이름']} / 신청일 {r['신청일자']} / 신청지역 {r['신청지역']} / 인입채널 {r['인입채널']}" for i, (_, r) in enumerate(group.iterrows(), start=1)]
    customer_list = "\n".join(customer_lines)
    return (
        f"받는 사람: {branch} {contact} 담당자\n\n{greeting}\n\n"
        f"CLM팀 김태호A입니다. {intro} 오늘({today_text}) {branch} 지점에 신청 고객 총 {count}명을 전달드립니다.\n\n"
        f"[신청정보]\n{customer_list}\n\n고객 혜택은 주유권 3만원, 네고금리 0.3%, 캐시백 20만원입니다. "
        "고객 상담 후 당사 한도조회를 유도해 주시고, 고객이 구매 의사를 보이면 CLM팀 담당자에게 별도로 공유 부탁드립니다.\n\n"
        f"{closing}\n\n감사합니다.\nCLM팀 김태호A 드림"
    )


def generate_ai_draft(branch: str, group: pd.DataFrame, cumulative_count: int) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 설정되어 있지 않습니다. 로컬은 .env, Streamlit Cloud는 앱 Secrets에 키를 입력하세요.")
    from openai import OpenAI

    contact, email, _ = CONTACTS[branch]
    records = group[["신청자 이름", "신청일자", "신청지역", "인입채널"]].to_dict(orient="records")
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=MODEL,
        input=[
            {"role": "system", "content": "당신은 정중하고 간결한 한국어 기업 업무 메일 작성 도우미입니다. 제공된 사실만 사용하고 신청정보나 혜택을 바꾸지 마세요. 이메일은 초안만 작성합니다."},
            {"role": "user", "content": (
                f"지점 배분 안내 메일을 작성하세요. 오늘 날짜는 {TODAY.isoformat()}입니다.\n"
                f"받는 사람: {branch} {contact} 담당자 <{email}>\n발신자: CLM팀 김태호A\n"
                f"이번 전달 인원: {len(records)}명\n신청정보: {records}\n"
                "반드시 포함할 고객 혜택: 주유권 3만원, 네고금리 0.3%, 캐시백 20만원.\n"
                "프로세스: 고객 상담 후 당사 한도조회 유도, 고객이 구매 의사가 있을 경우 CLM팀 담당자에게 별도 공유.\n"
                f"이 지점의 누적 배분 횟수(이번 배분 포함)는 {cumulative_count}회입니다. 이 횟수에 따라 인사말이나 맺음말 표현을 자연스럽게 변형하되, 혜택·절차·신청정보는 변경하지 마세요.\n"
                "상단에는 받는 사람(지점명, 담당자명)을 먼저 적고, 제목과 본문을 작성하세요. 정중하되 요점을 명확히 하고, 신청정보는 읽기 쉬운 번호 목록으로 정리하세요. 설명이나 인사말 외 부가 내용 없이 이메일 초안만 반환하세요."
            )},
        ],
    )
    return response.output_text.strip()


def mail_fingerprint(branch: str, group: pd.DataFrame) -> str:
    cols = [c for c in ["신청번호", "신청자 이름", "신청일자", "신청지역", "인입채널"] if c in group.columns]
    rows = group[cols].fillna("").astype(str).sort_values(cols).to_dict(orient="records")
    payload = branch + "\n" + repr(rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_mail_page(applications: pd.DataFrame) -> None:
    st.subheader("신규 신청 배분")
    st.caption("CSV를 올리면 전국 시·군·구 기본 배분표에 따라 자동 배정합니다. 배분은 광역시도와 시·군·구 조합 기준입니다.")
    with st.expander("SMTP 발송 설정", expanded=not smtp_is_configured(st.session_state.get("smtp_config"))):
        st.caption("입력 정보는 현재 실행 세션에서만 사용됩니다. 로컬은 .env 파일, Streamlit Cloud는 앱 설정의 Secrets에 저장할 수 있습니다.")
        active = st.session_state.get("smtp_config", {})
        configured_user = active.get("SMTP_USER", os.getenv("SMTP_USER", ""))
        default_local, _, default_domain = configured_user.partition("@")
        domain_options = ["gmail.com", "naver.com", "daum.net", "hanmail.net", "outlook.com", "직접 입력"]
        known_domain = default_domain if default_domain in domain_options else ("직접 입력" if default_domain else "gmail.com")
        left, right = st.columns([1.3, 1])
        with left:
            local_part = st.text_input("SMTP 계정 이메일 · @ 앞", value=st.session_state.get("smtp_local_part", default_local), placeholder="예: myname", key="smtp_local_part")
        with right:
            selected_domain = st.selectbox("도메인 · @ 뒤", domain_options, index=domain_options.index(known_domain), key="smtp_domain_choice")
        if selected_domain == "직접 입력":
            domain = st.text_input("직접 입력 도메인", value=st.session_state.get("smtp_custom_domain", default_domain if default_domain not in domain_options else ""), placeholder="예: company.co.kr", key="smtp_custom_domain")
        else:
            domain = selected_domain
        username = f"{local_part.strip()}@{domain.strip()}" if local_part.strip() and domain.strip() else ""
        st.caption("메일 계정의 @ 앞부분을 입력한 뒤, @ 뒤 도메인을 선택하세요. 예: myname + gmail.com → myname@gmail.com")

        host_examples = {"gmail.com": "smtp.gmail.com", "naver.com": "smtp.naver.com", "daum.net": "smtp.daum.net", "hanmail.net": "smtp.daum.net", "outlook.com": "smtp-mail.outlook.com"}
        host_default = active.get("SMTP_HOST", os.getenv("SMTP_HOST", host_examples.get(domain, "")))
        host = st.text_input("SMTP 서버", value=host_default, placeholder=f"예: {host_examples.get(domain, '메일 서비스에서 안내한 SMTP 서버')}", key="smtp_host_input")
        port_default = active.get("SMTP_PORT", os.getenv("SMTP_PORT", "587"))
        port = st.text_input(
            "포트 (3자리 SMTP 코드)", value=port_default, key="smtp_port_input",
            help="메일 서비스의 SMTP 연결 포트입니다. Gmail: 587(STARTTLS) 또는 465(SSL). 네이버: 587(TLS) 또는 465(SSL). 서비스 설정에서 SMTP 사용을 켜고 안내된 포트를 입력하세요.",
        )
        st.caption("예시: Google Gmail은 smtp.gmail.com · 587(STARTTLS) 또는 465(SSL), 네이버 메일은 smtp.naver.com · 587(TLS) 또는 465(SSL)입니다. 다른 서비스는 해당 서비스의 SMTP 안내값을 사용하세요.")
        password_default = active.get("SMTP_PASSWORD", os.getenv("SMTP_PASSWORD", ""))
        password = segmented_password_input(
            label="앱 비밀번호 (16자리)", help_text="Google 앱 비밀번호처럼 서비스에서 발급한 16자리 비밀번호를 4자리씩 입력합니다. 일반 계정 비밀번호가 아닌 앱 비밀번호가 필요한 서비스가 있습니다.",
            value=password_default, key="smtp_app_password", default=password_default,
            font_css=font_face_css,
        ) or ""
        sender_key = "smtp_sender_email"
        if not st.session_state.get("smtp_sender_custom", bool(active.get("SMTP_FROM") or os.getenv("SMTP_FROM"))):
            st.session_state[sender_key] = username or active.get("SMTP_FROM", os.getenv("SMTP_FROM", ""))
        def mark_sender_custom():
            st.session_state["smtp_sender_custom"] = True
        sender = st.text_input("발신 이메일 주소 (기본값: SMTP 계정과 동일)", key=sender_key, placeholder="SMTP 계정 이메일과 같게 설정됩니다.", on_change=mark_sender_custom)
        st.caption("발신 주소는 SMTP 계정 이메일을 기본값으로 사용합니다. 필요하면 이 칸에서 별도로 수정할 수 있습니다.")
        save_smtp = st.button("SMTP 설정 적용", type="primary", width="stretch")
        if save_smtp:
            normalized_password = "".join(str(password).split())
            config = {"SMTP_HOST": host.strip(), "SMTP_PORT": port.strip(), "SMTP_USER": username.strip(), "SMTP_PASSWORD": normalized_password, "SMTP_FROM": (sender or username).strip()}
            if len(normalized_password) == 16 and smtp_is_configured(config):
                st.session_state["smtp_config"] = config
                st.success("SMTP 설정을 적용했습니다. 각 지점의 실제 수신 주소를 입력하고 발송을 승인하세요.")
            else:
                st.error("SMTP 서버, 3자리 포트, 계정 이메일, 16자리 앱 비밀번호, 발신 주소를 확인해 주세요.")
    uploaded = st.file_uploader("신청 CSV 업로드", type=["csv"], help="필수 열: 신청자 이름, 신청일자, 인입채널, 신청 광역시도+신청 시·군·구. 채널은 맞춤정보, 마케팅, 캐시백미션, 외부광고 중 하나입니다. '신청지역' 한 열(예: 서울특별시 강서구)도 지원합니다.")
    if not uploaded:
        st.info("CSV 업로드 후 배분 결과를 미리 확인하고 메일 초안을 생성하세요.")
        st.caption("예시 CSV도 현재 권역 설정을 기준으로 신청 지역을 구성합니다. 업로드하면 배정 지점은 현재 규칙으로 다시 계산되므로, CSV의 고정된 지점 정보 없이 최신 담당 지점이 적용됩니다.")
        sample_col, all_col = st.columns(2)
        sample_col.download_button("10건 예시 CSV 다운로드", sample_csv(), "더미_신청_10건.csv", "text/csv", width="stretch")
        all_col.download_button("100건 예시 CSV 다운로드", sample_csv(100), "더미_신청_100건.csv", "text/csv", width="stretch")
        return
    try:
        incoming, errors = parse_upload(uploaded)
    except Exception as exc:
        st.error(str(exc))
        return
    existing_keys = {dedupe_key(row) for _, row in applications.iterrows()} if not applications.empty else set()
    seen_in_upload: set[str] = set()
    duplicate_flags = []
    for _, row in incoming.iterrows():
        key = dedupe_key(row)
        duplicate_flags.append(key in existing_keys or key in seen_in_upload)
        seen_in_upload.add(key)
    incoming["중복 여부"] = duplicate_flags
    st.markdown("#### 배분 미리보기")
    metric_cols = st.columns(4)
    metric_cols[0].metric("업로드", f"{len(incoming)}건")
    metric_cols[1].metric("배분 완료", f"{(incoming['처리 상태'] == '배분 완료').sum()}건")
    metric_cols[2].metric("미배분", f"{(incoming['처리 상태'] == '미배분').sum()}건")
    metric_cols[3].metric("기존 중복", f"{incoming['중복 여부'].sum()}건")
    st.dataframe(incoming, width="stretch", hide_index=True)
    if errors:
        with st.expander(f"검토 필요 {len(errors)}건", expanded=True):
            for error in errors:
                st.warning(error)
    valid = incoming[(incoming["처리 상태"] == "배분 완료") & (~incoming["중복 여부"])].copy()
    if valid.empty:
        st.warning("저장할 신규 배분 건이 없습니다. 오류 또는 중복을 확인하세요.")
    else:
        if st.button(f"신규 배분 {len(valid)}건 저장 · 대시보드 즉시 반영", type="primary", width="stretch"):
            combined = pd.concat([applications, valid.drop(columns=["중복 여부"])], ignore_index=True)
            save_store(combined, APPLICATIONS_PATH)
            st.session_state["draft_groups"] = {branch: g.copy() for branch, g in valid.groupby("배정 지점")}
            st.success(f"{len(valid)}건을 저장했습니다. 대시보드에 즉시 반영되었으며, 아래에서 메일 초안을 생성할 수 있습니다.")
            st.rerun()
    groups = st.session_state.get("draft_groups", {})
    if not groups:
        return
    st.divider()
    st.markdown("#### 지점별 메일 초안")
    st.caption("기본 초안은 로컬에서 바로 만들 수 있습니다. OpenAI API 키를 설정하면 GPT-6 Luna가 지점별 문안을 다듬습니다.")
    for branch, group in groups.items():
        contact, email, _ = CONTACTS[branch]
        cumulative = int((applications["배정 지점"] == branch).sum()) if not applications.empty else len(group)
        key = f"mail_draft_{branch}"
        batch_key = mail_fingerprint(branch, group)
        mail_log = read_store(MAIL_LOG_PATH)
        already_sent = (not mail_log.empty and "메일키" in mail_log.columns and batch_key in set(mail_log["메일키"]))
        with st.expander(f"{branch} · {len(group)}명 · 누적 {cumulative}건", expanded=True):
            recipient = st.text_input("받는 이메일 주소", value=email, key=f"recipient_{branch}")
            if recipient.lower().endswith("@example.com"):
                st.warning("데모 주소입니다. 실제 담당자 주소로 바꾸기 전까지 발송할 수 없습니다.")
            col1, col2 = st.columns([1, 2])
            with col1:
                if st.button("GPT-6 Luna로 초안 작성", key=f"ai_{branch}", width="stretch"):
                    try:
                        with st.spinner("OpenAI API로 초안을 작성하고 있습니다..."):
                            st.session_state[key] = generate_ai_draft(branch, group, cumulative)
                    except Exception as exc:
                        st.error(f"AI 초안 생성 실패: {exc}")
            with col2:
                if st.button("기본 초안 만들기", key=f"local_{branch}", width="stretch"):
                    st.session_state[key] = make_local_draft(branch, group, cumulative)
            if key not in st.session_state:
                st.session_state[key] = make_local_draft(branch, group, cumulative)
            draft = st.text_area("메일 초안 (수정 가능)", key=key, height=360)
            subject = f"[마케팅 프로그램] {branch} 신청 고객 {len(group)}명 전달 ({TODAY:%Y.%m.%d})"
            st.text_input("메일 제목", value=subject, key=f"subject_{branch}")
            resend = False
            resend_reason = ""
            if already_sent:
                st.warning("이 신청 묶음은 이미 발송된 이력이 있습니다. 재발송은 사유를 남겨야 합니다.")
                resend = st.checkbox("재발송 승인", key=f"resend_{branch}")
                resend_reason = st.text_input("재발송 사유", key=f"resend_reason_{branch}")
            confirm = st.checkbox(f"{branch} 실제 수신자에게 이 메일 발송을 승인합니다.", key=f"confirm_{branch}")
            active_smtp = st.session_state.get("smtp_config")
            can_send = confirm and smtp_is_configured(active_smtp) and bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient)) and "@example.com" not in recipient.lower() and (not already_sent or (resend and bool(resend_reason.strip())))
            if st.button(f"{branch} 메일 발송", key=f"send_{branch}", type="primary", disabled=not can_send, width="stretch"):
                try:
                    attachment_columns = ["신청자 이름", "신청지역", "신청일자"]
                    attachment_data = group[attachment_columns].copy()
                    workbook = BytesIO()
                    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
                        attachment_data.to_excel(writer, index=False, sheet_name="배분 고객")
                    attachment_name = f"{branch}_배분고객_{TODAY:%Y%m%d}.xlsx"
                    send_email(
                        recipient,
                        st.session_state[f"subject_{branch}"],
                        draft,
                        config=active_smtp,
                        attachments=[(
                            attachment_name,
                            workbook.getvalue(),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )],
                    )
                    log = read_store(MAIL_LOG_PATH)
                    entry = pd.DataFrame([{"발송일시": datetime.now().isoformat(timespec="seconds"), "지점": branch, "수신자": recipient, "신청건수": len(group), "제목": st.session_state[f"subject_{branch}"], "상태": "발송 완료", "메일키": batch_key, "재발송 사유": resend_reason.strip()}])
                    save_store(pd.concat([log, entry], ignore_index=True), MAIL_LOG_PATH)
                    st.success(f"{branch} 메일 발송 완료")
                except Exception as exc:
                    st.error(f"메일 발송 실패: {exc}")


def sample_data(count: int = 10) -> pd.DataFrame:
    """Build illustrative CSV rows from the current territory-to-branch map."""
    columns = ["신청자 이름", "신청일자", "신청 광역시도", "신청 시·군·구", "신청번호", "인입채널"]
    if count <= 0:
        return pd.DataFrame(columns=columns)

    population_file = DATA_DIR / "region_population.json"
    try:
        population = json.loads(population_file.read_text(encoding="utf-8")).get("population", {})
    except (OSError, json.JSONDecodeError):
        population = {}

    # Apply the same live overrides as parse_upload/assign_branch. Recompute
    # branch population shares so dummy locations follow edits made on the map.
    active_map = dict(REGION_ASSIGNMENTS)
    active_map.update(REGION_OVERRIDES)
    areas_by_branch: dict[str, list[tuple[str, str, float]]] = {branch: [] for branch in BRANCHES}
    for key, branch in active_map.items():
        if branch not in areas_by_branch or "|" not in key:
            continue
        province, district = key.split("|", 1)
        try:
            weight = max(1.0, float(population.get(key, 1)))
        except (TypeError, ValueError):
            weight = 1.0
        areas_by_branch[branch].append((province, district, weight))
    active_branches = [branch for branch in BRANCHES if areas_by_branch[branch]]
    if not active_branches:
        raise ValueError("현재 배분표에서 예시 데이터에 사용할 담당 권역을 찾을 수 없습니다.")

    branch_weights = [sum(area[2] for area in areas_by_branch[branch]) for branch in active_branches]
    total_weight = sum(branch_weights)
    quotas = [weight / total_weight * count for weight in branch_weights]
    counts = [int(quota) for quota in quotas]
    if count >= len(active_branches):
        counts = [max(1, amount) for amount in counts]
    while sum(counts) > count:
        eligible = [i for i, amount in enumerate(counts) if amount > 1 or count < len(active_branches)]
        if not eligible:
            break
        idx = max(eligible, key=lambda i: counts[i] - quotas[i])
        counts[idx] -= 1
    while sum(counts) < count:
        idx = max(range(len(active_branches)), key=lambda i: quotas[i] - counts[i])
        counts[idx] += 1

    rng = random.Random(20261021 + count)
    assigned_areas = []
    for branch, amount in zip(active_branches, counts):
        branch_areas = areas_by_branch[branch]
        if amount:
            assigned_areas.extend(rng.choices(branch_areas, weights=[area[2] for area in branch_areas], k=amount))
    rng.shuffle(assigned_areas)
    start_day = date(2026, 9, 1)
    # Model campaign waves so weekly volume is visibly uneven but reproducible.
    week_weights = [1, 1, 2, 3, 5, 8, 12]
    offsets = sorted(rng.choices(range(50), weights=[week_weights[min(day // 7, 6)] for day in range(50)], k=count))
    rows = [(province, district, (start_day + timedelta(days=offset)).isoformat())
            for (province, district, _), offset in zip(assigned_areas, offsets)]
    # Keep all four channels represented, while strongly favoring the first channels.
    channel_weights = [55, 25, 15, 5]
    channel_quotas = [weight * count / sum(channel_weights) for weight in channel_weights]
    channel_counts = [max(1, int(quota)) for quota in channel_quotas]
    while sum(channel_counts) > count:
        candidates = [i for i, amount in enumerate(channel_counts) if amount > 1]
        channel_counts[min(candidates, key=lambda i: channel_quotas[i] - channel_counts[i])] -= 1
    while sum(channel_counts) < count:
        channel_counts[max(range(len(CHANNELS)), key=lambda i: channel_quotas[i] - channel_counts[i])] += 1
    channel_values = [channel for channel, amount in zip(CHANNELS, channel_counts) for _ in range(amount)]
    random.Random(20261021 + count).shuffle(channel_values)
    return pd.DataFrame([
        {"신청자 이름": f"예시고객{i:03d}", "신청일자": day,
         "신청 광역시도": province, "신청 시·군·구": district,
         "신청번호": f"DEMO-{i:04d}", "인입채널": channel_values[i - 1]}
        for i, (province, district, day) in enumerate(rows, start=1)
    ])


def sample_csv(count: int = 10) -> bytes:
    return sample_data(count).to_csv(index=False).encode("utf-8-sig")


def sample_applications() -> pd.DataFrame:
    frame, _ = parse_upload(io.BytesIO(sample_csv()))
    return frame


def page_intro():
    st.title("마케팅 프로그램 신청 대시보드")
    st.write("신청 CSV를 권역 기준으로 지점에 배분하고, 누적 현황과 지점별 안내 메일을 한곳에서 관리합니다.")
    a, b, c, d = st.columns(4)
    a.metric("자동 처리", "CSV 업로드")
    b.metric("담당 지점", "7개")
    c.metric("배정 단위", "시·군·구")
    d.metric("결과", "대시보드·메일")
    st.markdown("### 이용 순서")
    flow = st.columns(3)
    flow[0].markdown("**1 · CSV 업로드**\n\n신청자 이름, 신청일자, 신청지역(광역시도·시군구), 인입채널을 불러옵니다.")
    flow[1].markdown("**2 · 배분 및 확인**\n\n전국 권역표로 지점을 자동 배정하고 중복·미배분을 확인한 뒤 저장합니다. 저장 즉시 대시보드에 반영됩니다.")
    flow[2].markdown("**3 · 메일 전달**\n\n지점별 초안을 검토·수정하고, 고객정보 엑셀(신청자 이름·신청지역·신청일자)과 함께 승인 후 발송합니다.")
    st.markdown("### 예시 데이터는 이렇게 쓰입니다")
    st.write("배분 페이지에서 내려받는 10건·100건 예시 CSV는 열 형식과 자동 배분, 초안 생성 흐름을 미리 확인하기 위한 가상 자료입니다. 지역은 현재 지점별 권역과 지도에서 수정한 설정을 반영해 인구 가중치로 생성하고, CSV 업로드 때 배정 지점을 최신 규칙으로 다시 계산합니다. 업로드 후 **신규 배분 저장**을 눌러야 대시보드에 반영되며, 소개 페이지의 미리보기와 다운로드만으로는 누적 데이터가 바뀌지 않습니다. `run.bat`을 새로 실행하면 누적 신청·메일 이력·권역 변경은 초기화됩니다.")
    st.markdown("### 메일 설정")
    st.write("OpenAI 키가 있으면 GPT-6 Luna로 초안을 다듬을 수 있습니다. 실제 발송은 배분 페이지에서 SMTP 서버·포트·계정·앱 비밀번호를 설정하고 수신자와 본문을 확인한 뒤 발송 확인을 선택해야 합니다. Gmail은 Gmail 앱 비밀번호를 사용할 수 있으며, 16자리를 4자씩 입력합니다. 발신 주소는 SMTP 계정과 같게 시작하지만 수정할 수 있습니다.")
    st.caption("메일 초안에는 주유권 3만원·네고금리 0.3%·캐시백 20만원 혜택과 상담 후 한도조회, 구매 의향 고객의 CLM팀 별도 공유 절차가 포함됩니다. 권역 설정의 전국 지도에서 담당 지점을 조정하면 이후 업로드부터 반영되며, run.bat을 다시 실행하면 신청·발송·권역 변경 이력이 초기화됩니다.")
    st.markdown("### 신청 데이터 미리보기")
    preview = sample_applications()[["신청자 이름", "신청일자", "신청지역", "인입채널", "배정 지점", "담당자"]]
    st.caption("가상 신청 데이터 10건의 화면 예시입니다. 이 표는 실제 누적 신청 데이터에 저장되지 않습니다.")
    st.dataframe(preview, width="stretch", hide_index=True)


def page_dashboard(applications: pd.DataFrame):
    st.title("누적 신청 대시보드")
    if applications.empty:
        st.info("누적 신청 데이터가 없습니다. 배분 페이지에서 CSV를 업로드하고 배분 결과를 저장하면 이곳에 바로 표시됩니다.")
        return
    data = applications.copy()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("누적 신청", f"{len(data):,}건")
    c2.metric("배분 완료", f"{(data['배정 지점'].astype(str) != '').sum():,}건")
    c3.metric("미배분", f"{(data['배정 지점'].astype(str) == '').sum():,}건")
    sent_count = len(read_store(MAIL_LOG_PATH))
    c4.metric("메일 발송", f"{sent_count:,}건")
    st.markdown("### 지점별 누적 배분")
    counts = data[data["배정 지점"].isin(BRANCHES)].groupby("배정 지점").size().reindex(BRANCHES, fill_value=0)
    branch_chart = counts.rename("신청건수").rename_axis("담당 지점").reset_index()
    st.altair_chart(make_count_bar(branch_chart, "담당 지점", "#1769AA", height=320), width="stretch")
    left, right = st.columns(2)
    with left:
        st.markdown("### 인입채널별 신청")
        channels = data["인입채널"] if "인입채널" in data else pd.Series("미기재", index=data.index)
        channel_counts = channels.replace("", "미기재").value_counts().rename("신청건수").rename_axis("인입채널").reset_index()
        st.altair_chart(make_count_bar(channel_counts, "인입채널", "#4B74C8", height=320), width="stretch")
    with right:
        st.markdown("### 신청 추이")
        dates = pd.to_datetime(data["신청일자"], errors="coerce").dropna()
        if not dates.empty:
            trend = dates.dt.to_period("W").value_counts().sort_index()
            trend_frame = pd.DataFrame({
                "신청 주차": [week.start_time for week in trend.index],
                "신청건수": trend.to_numpy(dtype=int),
            })
            st.altair_chart(make_trend_line(trend_frame), width="stretch")
        else:
            st.caption("표시할 날짜 데이터가 없습니다.")
    st.markdown("### 신청 목록")
    f1, f2 = st.columns(2)
    branches = ["전체"] + BRANCHES + ["미배분"]
    selected_branch = f1.selectbox("지점 필터", branches)
    search = f2.text_input("신청자/지역 검색")
    filtered = data.copy()
    if selected_branch == "미배분":
        filtered = filtered[filtered["배정 지점"].astype(str) == ""]
    elif selected_branch != "전체":
        filtered = filtered[filtered["배정 지점"] == selected_branch]
    if search:
        mask = filtered.astype(str).apply(lambda col: col.str.contains(search, case=False, regex=False)).any(axis=1)
        filtered = filtered[mask]
    st.dataframe(filtered, width="stretch", hide_index=True)
    st.download_button("현재 결과 CSV 다운로드", filtered.to_csv(index=False).encode("utf-8-sig"), "배분_분석결과.csv", "text/csv", width="stretch")


BRANCH_COLORS = {
    "서울서부": (23, 105, 170),
    "서울동부": (72, 151, 204),
    "수원": (37, 137, 116),
    "천안": (197, 126, 45),
    "광주": (117, 99, 174),
    "부산": (211, 99, 83),
    "대구": (151, 86, 120),
    "미배분": (151, 163, 175),
}


@st.cache_data(show_spinner=False)
def load_sigungu_geojson() -> dict:
    return json.loads(SIGUNGU_GEOJSON_PATH.read_text(encoding="utf-8"))


def page_territories():
    st.title("권역 설정 지도")
    st.write("전국 시·군·구의 현재 담당 지점을 확인하고, 구역을 선택해 담당 지점을 변경할 수 있습니다.")
    st.caption("변경은 저장 즉시 이후 신규 CSV 배분에 적용됩니다. 이미 저장된 과거 배분 건은 변경하지 않습니다. run.bat을 다시 실행하면 기본 권역으로 초기화됩니다.")

    try:
        source = load_sigungu_geojson()
    except (OSError, json.JSONDecodeError) as exc:
        st.error(f"전국 행정구역 경계 데이터를 읽을 수 없습니다: {exc}")
        return

    groups: dict[str, list[dict]] = {branch: [] for branch in BRANCHES}
    groups["미배분"] = []
    for original in source.get("features", []):
        feature = copy.deepcopy(original)
        properties = feature.setdefault("properties", {})
        province = normalize_province(properties.get("SIDO_NM", ""))
        district = str(properties.get("SIG_KOR_NM", "")).strip()
        if not province or not district:
            continue
        branch, reason = assign_branch(province, district)
        key = region_key(province, district)
        properties.update({
            "app_province": province,
            "app_district": district,
            "app_full_name": f"{province} {district}",
            "app_region_key": key,
            "app_branch": branch or "미배분",
            "app_reason": reason,
        })
        groups[branch or "미배분"].append(feature)

    layers = []
    for branch in [*BRANCHES, "미배분"]:
        features = groups[branch]
        if not features:
            continue
        layer_id = f"territory-{BRANCHES.index(branch) if branch in BRANCHES else 'unassigned'}"
        layers.append(pdk.Layer(
            "GeoJsonLayer",
            data={"type": "FeatureCollection", "features": features},
            id=layer_id,
            pickable=True,
            auto_highlight=True,
            stroked=True,
            filled=True,
            get_fill_color=[*BRANCH_COLORS[branch], 180],
            get_line_color=[255, 255, 255, 230],
            get_line_width=1,
            line_width_min_pixels=1,
        ))

    legend_branches = [*BRANCHES, *(["미배분"] if groups["미배분"] else [])]
    legend_cols = st.columns(len(legend_branches))
    for col, branch in zip(legend_cols, legend_branches):
        rgb = BRANCH_COLORS[branch]
        col.markdown(
            f'<div style="display:flex;align-items:center;gap:7px;margin:4px 0;white-space:nowrap;">'
            f'<span style="width:14px;height:14px;border-radius:3px;background:rgb{rgb};display:inline-block;"></span>'
            f'<strong>{branch}</strong></div>',
            unsafe_allow_html=True,
        )

    deck = pdk.Deck(
        layers=layers,
        initial_view_state=pdk.ViewState(latitude=36.2, longitude=127.8, zoom=6.1, pitch=0, bearing=0, controller=True),
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        tooltip={"text": "{app_full_name}\n담당 지점: {app_branch}"},
    )
    map_state = st.pydeck_chart(
        deck, height=720, on_select="rerun", selection_mode="single-object", key="territory_assignment_map"
    )
    selected_objects = []
    try:
        selected_objects = [obj for objects in map_state.selection.objects.values() for obj in objects]
    except (AttributeError, TypeError):
        pass
    if selected_objects:
        selected = selected_objects[0]
        properties = selected.get("properties", selected)
        region_key_value = properties.get("app_region_key", "")
        if region_key_value:
            st.session_state["selected_territory"] = {
                "key": region_key_value,
                "province": properties.get("app_province", ""),
                "district": properties.get("app_district", ""),
                "branch": properties.get("app_branch", "미배분"),
            }

    selected_region = st.session_state.get("selected_territory")
    if selected_region:
        st.divider()
        st.subheader(f"{selected_region['province']} {selected_region['district']}")
        current_branch, reason = assign_branch(selected_region["province"], selected_region["district"])
        st.write(f"현재 담당 지점: **{current_branch or '미배분'}**")
        if reason:
            st.caption(f"현재 기준: {reason}")
        selected_key = selected_region["key"]
        edit_col, reset_col = st.columns([3, 1])
        with edit_col:
            index = BRANCHES.index(current_branch) if current_branch in BRANCHES else 0
            chosen_branch = st.selectbox("변경할 담당 지점", BRANCHES, index=index, key=f"edit_branch_{selected_key}")
            if st.button("담당 지점 저장", type="primary", key=f"save_branch_{selected_key}"):
                REGION_OVERRIDES[selected_key] = chosen_branch
                REGION_OVERRIDES_PATH.write_text(
                    json.dumps(REGION_OVERRIDES, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                st.success(f"{selected_region['province']} {selected_region['district']} → {chosen_branch}로 변경했습니다.")
                st.rerun()
        with reset_col:
            if selected_key in REGION_OVERRIDES and st.button("기본값 복원", key=f"reset_branch_{selected_key}"):
                REGION_OVERRIDES.pop(selected_key, None)
                REGION_OVERRIDES_PATH.write_text(
                    json.dumps(REGION_OVERRIDES, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                st.success("기본 배분 규칙으로 복원했습니다.")
                st.rerun()
    else:
        st.info("지도에서 시·군·구를 클릭하면 현재 담당 지점과 수정 버튼이 표시됩니다.")
    st.caption("행정구역 경계 출처: 국가공간정보포털(V-World) 행정구역 경계, KnellBalm 가공 GeoJSON. 지도는 인터넷 지도 타일을 배경으로 표시합니다.")


def make_count_bar(
    frame: pd.DataFrame,
    category: str,
    color: str,
    height: int = 340,
    category_sort: str = "-y",
):
    count_values = pd.to_numeric(frame["신청건수"], errors="coerce").fillna(0)
    max_count = max(1, int(count_values.max()))
    tick_step = max(1, math.ceil(max_count / 5))
    axis_max = tick_step * math.ceil(max_count / tick_step)
    item_count = len(frame)
    label_angle = 0 if item_count <= 3 else -35
    label_font = 13 if item_count <= 6 else 11 if item_count <= 10 else 10
    chart = alt.Chart(frame).mark_bar(color=color, cornerRadiusEnd=4).encode(
        x=alt.X(
            f"{category}:N",
            title=None,
            sort=category_sort,
            axis=alt.Axis(
                labelAngle=label_angle,
                labelAlign="right" if label_angle else "center",
                labelLimit=115,
                labelOverlap="greedy",
                labelFontSize=label_font,
                labelPadding=8,
            ),
        ),
        y=alt.Y(
            "신청건수:Q",
            title=None,
            scale=alt.Scale(domain=[0, axis_max], nice=False),
            axis=alt.Axis(format="d", tickMinStep=tick_step, tickCount=6, grid=True),
        ),
        tooltip=[alt.Tooltip(f"{category}:N", title=category), alt.Tooltip("신청건수:Q", format="d")],
    ).properties(height=height, padding={"left": 14, "right": 12, "top": 10, "bottom": 58 if label_angle else 28})
    return chart.configure_axis(
        labelColor="#162F49", titleColor="#162F49", gridColor="#DCE6F0", labelFontSize=13,
        labelFont="MarketingDashboardFont", titleFont="MarketingDashboardFont",
    ).configure_view(stroke="#D9E3EC").configure_legend(
        labelFont="MarketingDashboardFont", titleFont="MarketingDashboardFont", labelFontSize=13
    ).configure_title(font="MarketingDashboardFont")


def make_trend_line(frame: pd.DataFrame):
    max_count = max(1, int(pd.to_numeric(frame["신청건수"], errors="coerce").fillna(0).max()))
    tick_step = max(1, math.ceil(max_count / 5))
    axis_max = tick_step * math.ceil(max_count / tick_step)
    chart = alt.Chart(frame).mark_line(
        color="#1769AA", strokeWidth=3, point=alt.OverlayMarkDef(filled=True, size=65)
    ).encode(
        x=alt.X("신청 주차:T", title=None, axis=alt.Axis(format="%m/%d", labelAngle=-35, labelLimit=90)),
        y=alt.Y("신청건수:Q", title=None, scale=alt.Scale(domain=[0, axis_max], nice=False),
                axis=alt.Axis(format="d", tickMinStep=tick_step, tickCount=6, grid=True)),
        tooltip=[alt.Tooltip("신청 주차:T", title="주차", format="%Y-%m-%d"), alt.Tooltip("신청건수:Q", format="d")],
    ).properties(height=320, padding={"left": 14, "right": 12, "top": 10, "bottom": 58})
    return chart.configure_axis(
        labelColor="#162F49", titleColor="#162F49", gridColor="#DCE6F0", labelFontSize=13,
        labelFont="MarketingDashboardFont", titleFont="MarketingDashboardFont",
    ).configure_view(stroke="#D9E3EC").configure_legend(
        labelFont="MarketingDashboardFont", titleFont="MarketingDashboardFont", labelFontSize=13
    ).configure_title(font="MarketingDashboardFont")


app_font_path = APP_DIR / "폰트.ttf"
app_font_base64 = base64.b64encode(app_font_path.read_bytes()).decode("ascii") if app_font_path.exists() else ""
font_face_css = """@font-face {
    font-family: 'MarketingDashboardFont';
    src: url('data:font/ttf;base64,__APP_FONT_BASE64__') format('truetype');
    font-style: normal;
    font-weight: 400;
    font-display: swap;
  }""".replace("__APP_FONT_BASE64__", app_font_base64) if app_font_base64 else ""
st.markdown(("""
<style>
  __APP_FONT_FACE__
  :root {
    --ink: #162F49;
    --muted: #5B7087;
    --blue: #1769AA;
    --blue-dark: #125586;
    --canvas: #F5F8FC;
    --line: #DCE6F0;
    --card: #FFFFFF;
  }
  html, body, [class*="css"] {
    color: var(--ink);
    font-family: 'MarketingDashboardFont', 'Malgun Gothic', sans-serif !important;
    font-size: 16px;
    line-height: 1.55;
  }
  body [data-testid="stAppViewContainer"] :not(svg):not(path):not(.material-icons):not([class*="material-symbol"]):not([data-testid*="Icon"]) {
    font-family: 'MarketingDashboardFont', 'Malgun Gothic', sans-serif !important;
  }
  /* Keep Streamlit's ligature icon font intact; applying the custom Korean font
     turns names such as "arrow_right" into visible text in expander headers. */
  body [data-testid="stAppViewContainer"] [class*="material-symbol"] {
    font-family: "Material Symbols Rounded" !important;
    font-feature-settings: "liga" !important;
  }
  body [data-testid="stAppViewContainer"] .material-icons {
    font-family: "Material Icons" !important;
  }
  body p, body li, body label, body button, body input, body textarea, body select,
  body small, body strong, body em, body summary, body [data-testid="stWidgetLabel"],
  body [data-testid="stMarkdownContainer"], body [data-testid="stCaptionContainer"],
  body [data-testid="stMetricLabel"], body [data-testid="stMetricValue"],
  body [data-testid="stDataFrame"], body [data-testid="stTable"] *,
  body [data-testid="stAlert"] *,
  body [data-testid="stTabs"] *, body [data-testid="stRadio"] *, body [data-testid="stCheckbox"] * {
    font-family: 'MarketingDashboardFont', 'Malgun Gothic', sans-serif !important;
  }
  body p, body li, body label, body [data-testid="stWidgetLabel"] { font-size: 1rem; line-height: 1.6; }
  body [data-testid="stCaptionContainer"] { font-size: .9rem !important; line-height: 1.55; }
  h1, h2, h3, h4 { font-family: 'MarketingDashboardFont', 'Malgun Gothic', sans-serif !important; line-height: 1.3 !important; }
  h1 { font-size: 2.15rem !important; }
  h2 { font-size: 1.7rem !important; }
  h3 { font-size: 1.35rem !important; }
  button, input, textarea, select { font-size: .98rem !important; }
  button { line-height: 1.4 !important; }
  [data-testid="stAppViewContainer"] { background: var(--canvas); color: var(--ink); }
  [data-testid="stHeader"] { background: rgba(245,248,252,.96); }
  .block-container {padding-top: 1.35rem; padding-bottom: 2rem; max-width: 1380px;}
  h1, h2, h3, h4 { color: var(--ink) !important; letter-spacing: -0.025em; }
  h1 { font-weight: 760 !important; }
  p, li, label, [data-testid="stCaptionContainer"] { color: var(--muted); }
  [data-testid="stSidebar"] { background: #E7EFF8; border-right: 1px solid #DCE6F0; }
  [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
  [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3,
  [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
  [data-testid="stSidebar"] label { color: #162F49 !important; }
  [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: #5B7087 !important; }
  [data-testid="stSidebar"] [data-testid="stRadio"] label { color: #425B74 !important; font-weight: 650; }
  [data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) { color: #174B78 !important; font-weight: 750; }
  [data-testid="stMetric"] {
    background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--blue);
    padding: 15px 18px; border-radius: 10px; box-shadow: 0 2px 9px rgba(24,58,88,.06);
  }
  [data-testid="stMetricLabel"] p { color: #5B7087 !important; font-size: .96rem !important; font-weight: 700; }
  [data-testid="stMetricValue"] { color: #162F49 !important; font-size: 2.05rem !important; font-weight: 780 !important; }
  [data-testid="stMetricDelta"] { font-weight: 650; }
  [data-testid="stVegaLiteChart"], [data-testid="stArrowVegaLiteChart"] {
    background: #FFFFFF; border: 1px solid var(--line); border-radius: 10px; padding: 8px;
  }
  [data-testid="stDataFrame"] { font-family: 'MarketingDashboardFont', 'Malgun Gothic', sans-serif !important; }
  [data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
  [data-testid="stAlert"] p { color: #162F49 !important; }
  [data-testid="stWidgetLabel"] p { color: #334E68 !important; font-weight: 620; }
  /* Lock text/background contrast across Streamlit's hover, pressed and focus states. */
  [data-testid="stBaseButton-primary"],
  [data-testid="stBaseButton-primary"]:visited {
    background: #1769AA !important; border: 1px solid #1769AA !important;
    color: #FFFFFF !important; font-weight: 750 !important;
  }
  [data-testid="stBaseButton-primary"] *,
  [data-testid="stBaseButton-primary"]:hover *,
  [data-testid="stBaseButton-primary"]:active *,
  [data-testid="stBaseButton-primary"]:focus *,
  [data-testid="stBaseButton-primary"]:focus-visible * { color: #FFFFFF !important; }
  [data-testid="stBaseButton-primary"]:hover {
    background: #125586 !important; border-color: #125586 !important; color: #FFFFFF !important;
  }
  [data-testid="stBaseButton-primary"]:active {
    background: #0D426B !important; border-color: #0D426B !important; color: #FFFFFF !important;
  }
  [data-testid="stBaseButton-primary"]:focus-visible {
    outline: 3px solid #8BC5F2 !important; outline-offset: 2px;
  }
  [data-testid="stBaseButton-primary"]:disabled,
  [data-testid="stBaseButton-primary"][aria-disabled="true"] {
    background: #E2E8F0 !important; border-color: #CBD5E1 !important;
    color: #475569 !important; opacity: 1 !important; cursor: not-allowed;
  }
  [data-testid="stBaseButton-primary"]:disabled *,
  [data-testid="stBaseButton-primary"][aria-disabled="true"] * { color: #475569 !important; }
  /* Streamlit form submit buttons use a wrapper test id in some releases. */
  div[data-testid="stFormSubmitButton"] button {
    background: #125586 !important; border: 1px solid #125586 !important;
    color: #FFFFFF !important; font-weight: 800 !important; text-shadow: none !important;
  }
  div[data-testid="stFormSubmitButton"] button *,
  div[data-testid="stFormSubmitButton"] button:hover *,
  div[data-testid="stFormSubmitButton"] button:active *,
  div[data-testid="stFormSubmitButton"] button:focus * { color: #FFFFFF !important; }
  div[data-testid="stFormSubmitButton"] button:hover,
  div[data-testid="stFormSubmitButton"] button:focus {
    background: #0D426B !important; border-color: #0D426B !important; color: #FFFFFF !important;
  }
  div[data-testid="stFormSubmitButton"] button:active {
    background: #093451 !important; border-color: #093451 !important; color: #FFFFFF !important;
  }
  div[data-testid="stFormSubmitButton"] button:focus-visible {
    outline: 3px solid #8BC5F2 !important; outline-offset: 2px;
  }
  [data-testid="stBaseButton-secondary"] {
    color: #17324D !important; border: 1px solid #A9BBCB !important; background: #FFFFFF !important;
    font-weight: 650 !important;
  }
  [data-testid="stBaseButton-secondary"] * { color: #17324D !important; }
  [data-testid="stBaseButton-secondary"]:hover,
  [data-testid="stBaseButton-secondary"]:active,
  [data-testid="stBaseButton-secondary"]:focus {
    color: #102A43 !important; background: #E7EFF8 !important; border-color: #54738F !important;
  }
  [data-testid="stBaseButton-secondary"]:hover *,
  [data-testid="stBaseButton-secondary"]:active *,
  [data-testid="stBaseButton-secondary"]:focus * { color: #102A43 !important; }
  [data-testid="stBaseButton-secondary"]:focus-visible {
    outline: 3px solid #8BC5F2 !important; outline-offset: 2px;
  }
  [data-testid="stBaseButton-secondary"]:disabled,
  [data-testid="stBaseButton-secondary"][aria-disabled="true"] {
    background: #E2E8F0 !important; border-color: #CBD5E1 !important;
    color: #475569 !important; opacity: 1 !important;
  }
  [data-testid="stBaseButton-secondary"]:disabled *,
  [data-testid="stBaseButton-secondary"][aria-disabled="true"] * { color: #475569 !important; }
  input, textarea { color: #162F49 !important; }
  @media (max-width: 700px) {
    .block-container {padding: .8rem .8rem 1.5rem;}
    h1 {font-size: 1.75rem !important;}
    h2 {font-size: 1.45rem !important;}
    h3 {font-size: 1.2rem !important;}
    body p, body li, body label {font-size: .96rem;}
    [data-testid="stMetric"] {padding: 11px 12px;}
    [data-testid="stMetricValue"] {font-size: 1.65rem !important;}
  }
</style>
""").replace("__APP_FONT_FACE__", font_face_css), unsafe_allow_html=True)
with st.sidebar:
    st.title("📬 신청 배분")
    page = st.radio("페이지", ["소개", "대시보드", "배분", "권역 설정"], label_visibility="collapsed")
    st.divider()
    st.caption("마케팅 프로그램 신청 관리")

applications = read_store(APPLICATIONS_PATH)
if page == "소개":
    page_intro()
elif page == "대시보드":
    page_dashboard(applications)
elif page == "권역 설정":
    page_territories()
else:
    st.title("신청 배분 및 메일")
    render_mail_page(applications)


