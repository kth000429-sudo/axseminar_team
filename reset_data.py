"""Reset application rows whenever run.bat starts a fresh dashboard session."""

from pathlib import Path

import pandas as pd
import json


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
APPLICATIONS_PATH = DATA_DIR / "applications.csv"
MAIL_LOG_PATH = DATA_DIR / "mail_log.csv"
REGION_OVERRIDES_PATH = DATA_DIR / "region_overrides.json"

APPLICATION_COLUMNS = [
    "신청자 이름",
    "신청일자",
    "신청번호",
    "인입채널",
    "신청 광역시도",
    "신청 시·군·구",
    "신청지역",
    "배정 지점",
    "배분 사유",
    "처리 상태",
    "담당자",
    "담당자 이메일",
    "배분 일시",
]
MAIL_LOG_COLUMNS = ["발송일시", "지점", "수신자", "신청건수", "제목", "상태", "메일키", "재발송 사유"]


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    pd.DataFrame(columns=APPLICATION_COLUMNS).to_csv(
        APPLICATIONS_PATH, index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(columns=MAIL_LOG_COLUMNS).to_csv(
        MAIL_LOG_PATH, index=False, encoding="utf-8-sig"
    )
    REGION_OVERRIDES_PATH.write_text(json.dumps({}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("신청 데이터, 메일 발송 이력 및 권역 변경 내역이 초기화되었습니다.")


if __name__ == "__main__":
    main()
