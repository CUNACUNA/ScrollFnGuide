"""companylist.txt를 읽어 네이버 금융 API에서 현재 주가를 수집한 후 MongoDB CURRENT-PRICE 테이블에 Overwrite 저장한다."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import requests
import urllib3
from pymongo import MongoClient

# 경고 메시지 비활성화
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# 1. 설정 및 상수 정의
# ==============================================================================
DEFAULT_MONGODB_URI = "mongodb://127.0.0.1:27017"
DB_NAME = "fnguide_db"          # 사용 중인 DB 이름 (필요시 "Scrawl" 등으로 변경)
COLLECTION_NAME = "CURRENT-PRICE"  # 저장할 테이블(컬렉션) 이름
COMPANY_LIST_FILE = "companylist.txt"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
PRICE_UNAVAILABLE = "N/A"
REALTIME_API_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"


# ==============================================================================
# 2. price_fetcher 및 주가 정제 로직
# ==============================================================================
def resolve_verify_ssl(*, cli_secure: bool = False) -> bool:
    """SSL 검증 여부를 확인한다."""
    if cli_secure:
        return True
    env = os.environ.get("SCROLL_VERIFY_SSL", "0").strip().lower()
    return env in {"1", "true", "yes", "on"}


def _normalize_price(raw: str | int | float | None) -> str | None:
    """수집된 주가 데이터를 천 단위 쉼표 포함 수치 문자열로 정제한다."""
    if raw is None:
        return None

    text = str(raw).strip().replace(",", "")
    if not text or text in {"0", "-", "null"}:
        return None

    if text.isdigit():
        return f"{int(text):,}"

    try:
        value = float(text)
    except ValueError:
        return None

    if value <= 0:
        return None

    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _parse_realtime_payload(payload: dict) -> str | None:
    """네이버 금융 응답 JSON 객체에서 현재가/종가를 추출한다."""
    candidates: list[object] = []

    result = payload.get("result")
    if isinstance(result, dict):
        candidates.extend(
            [
                result.get("closePrice"),
                result.get("tradePrice"),
                result.get("now"),
            ]
        )

    datas = payload.get("datas")
    if isinstance(datas, list) and datas:
        first = datas[0]
        if isinstance(first, dict):
            candidates.extend(
                [
                    first.get("closePrice"),
                    first.get("tradePrice"),
                    first.get("now"),
                ]
            )

    for candidate in candidates:
        normalized = _normalize_price(candidate)
        if normalized:
            return normalized

    return None


def fetch_current_price(
    code: str,
    session: requests.Session | None = None,
    *,
    verify_ssl: bool = False,
) -> str:
    """종목 코드의 현재 주가를 네이버 금융 실시간 API에서 조회한다."""
    http = session or requests.Session()
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    realtime_url = REALTIME_API_URL.format(code=code)

    try:
        response = http.get(realtime_url, headers=headers, timeout=10, verify=verify_ssl)
        if response.ok:
            payload = response.json()
            if isinstance(payload, dict):
                price = _parse_realtime_payload(payload)
                if price:
                    return price
    except (requests.RequestException, ValueError):
        pass

    return PRICE_UNAVAILABLE


# ==============================================================================
# 3. companylist.txt 파일 파싱 로직
# ==============================================================================
def parse_company_list(file_path: str | Path) -> list[tuple[str, str]]:
    """
    companylist.txt 파일을 한 줄씩 읽어 (code, name) 튜플 리스트를 반환한다.
    '005930, 삼성전자' 및 '삼성전자, 005930' 형식 모두 지원한다.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"'{file_path}' 파일을 찾을 수 없습니다.")

    companies: list[tuple[str, str]] = []
    code_pattern = re.compile(r"^\d{6}$")

    with path.open(encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 2:
                continue

            # 첫 번째 항목이 6자리 종목 코드인 경우
            if code_pattern.fullmatch(parts[0]):
                code, name = parts[0], parts[1]
            # 두 번째 항목이 6자리 종목 코드인 경우
            elif code_pattern.fullmatch(parts[1]):
                name, code = parts[0], parts[1]
            else:
                continue

            companies.append((code, name))

    return companies


# ==============================================================================
# 4. MongoDB 저장 및 수집 메인 로직
# ==============================================================================
def update_prices_to_mongodb():
    # 1. MongoDB 연결
    mongodb_uri = os.environ.get("MONGODB_URI", DEFAULT_MONGODB_URI)
    client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]
    collection = db[COLLECTION_NAME]

    # 2. companylist.txt 파싱
    print(f"[{COMPANY_LIST_FILE}] 파일에서 기업 목록을 불러오는 중...")
    company_list = parse_company_list(COMPANY_LIST_FILE)
    print(f"총 {len(company_list)}개의 기업 코드를 파싱했습니다.\n")

    # 3. HTTP 세션 생성
    session = requests.Session()
    verify_ssl = resolve_verify_ssl()

    success_count = 0

    # 4. 종목별 주가 수집 및 MongoDB Overwrite(Upsert)
    for code, name in company_list:
        price = fetch_current_price(code, session=session, verify_ssl=verify_ssl)

        if price == PRICE_UNAVAILABLE:
            print(f"❌ [{code}] {name} - 주가를 가져오지 못했습니다.")
            continue

        # 저장 및 Overwrite 조건 설정
        doc_filter = {"기업코드": code}
        update_data = {
            "$set": {
                "기업코드": code,
                "code": code,
                "name": name,
                "current_price": price,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        }

        # upsert=True: 존재하면 Overwrite, 없으면 Insert
        result = collection.update_one(doc_filter, update_data, upsert=True)

        if result.upserted_id:
            print(f"✨ [{code}] {name}: {price}원 (신규 저장 완료)")
        else:
            print(f"🔄 [{code}] {name}: {price}원 (기존 주가 Overwrite 완료)")

        success_count += 1
        time.sleep(0.2)

    print(f"\n작업 완료: 총 {len(company_list)}개 중 {success_count}개 기업의 주가를 DB에 갱신했습니다.")


if __name__ == "__main__":
    try:
        update_prices_to_mongodb()
    except Exception as e:
        print(f"오류 발생: {e}")