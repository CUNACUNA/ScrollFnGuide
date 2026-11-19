"""companylist.txt를 읽어 본주 및 우선주 전체 기업 리스트에 대해 네이버 금융 API에서 현재 주가를 수집한 후 MongoDB CURRENT-PRICE 테이블에 Overwrite 저장한다."""

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
DB_NAME = "fnguide_db"          # 사용 중인 DB 이름
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
# 3. companylist.txt 파일 파싱 로직 (강력한 유연 파서 적용)
# ==============================================================================
def parse_company_list(file_path: str | Path) -> list[dict]:
    """
    companylist.txt 파일을 파싱하여 전체 기업(본주 및 우선주) 정보를 정확히 추출한다.
    - 3자리/4자리 형식 및 콤마/공백 구분 모두 유연하게 처리
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"'{file_path}' 파일을 찾을 수 없습니다.")

    companies: list[dict] = []
    code_pattern = re.compile(r"\b\d{6}\b")

    with path.open(encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            # 라인 내 6자리 코드 추출 (첫 번째: 해당 종목 코드, 두 번째: 우선주인 경우 본주 코드)
            codes = code_pattern.findall(line)
            if not codes:
                continue

            code = codes[0]
            scrap_code = codes[1] if len(codes) > 1 else code

            # 코드 영역을 제거하여 이름 및 타입 추출
            clean_line = line
            for c in codes:
                clean_line = clean_line.replace(c, "")

            parts = [p.strip() for p in re.split(r"[,/]", clean_line) if p.strip()]
            
            stock_type = "Normal"
            name = ""

            if parts:
                first_part = parts[0]
                sub_parts = first_part.split(maxsplit=1)
                if sub_parts[0] in ["Preferred", "Normal", "우선주", "본주", "우선", "본"]:
                    stock_type = sub_parts[0]
                    name = sub_parts[1] if len(sub_parts) > 1 else (parts[1] if len(parts) > 1 else "")
                else:
                    name = first_part
                    if not name and len(parts) > 1:
                        name = parts[1]

            if not name:
                tokens = [t.strip() for t in re.split(r"[,/\s]+", clean_line) if t.strip()]
                valid_tokens = [t for t in tokens if t not in ["Preferred", "Normal", "우선주", "본주", "우선", "본"]]
                if valid_tokens:
                    name = "".join(valid_tokens)

            companies.append({
                "type": stock_type,
                "name": name,
                "code": code,
                "scrap_code": scrap_code
            })

    return companies


# ==============================================================================
# 4. MongoDB 저장 및 수집 메인 로직
# ==============================================================================
def update_prices_to_mongodb():
    mongodb_uri = os.environ.get("MONGODB_URI", DEFAULT_MONGODB_URI)
    client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]
    collection = db[COLLECTION_NAME]

    print(f"[{COMPANY_LIST_FILE}] 파일에서 전체 기업 목록을 불러오는 중...")
    company_list = parse_company_list(COMPANY_LIST_FILE)
    print(f"총 {len(company_list)}개의 기업 코드를 파싱했습니다.\n")

    session = requests.Session()
    verify_ssl = resolve_verify_ssl()

    success_count = 0

    # 전체 리스트(본주, 우선주 포함)를 순회하며 현재가 수집 및 저장
    for company in company_list:
        code = company["code"]   # 현재가 조회 및 저장 키 (우선주면 우선주 코드)
        name = company["name"]
        
        price = fetch_current_price(code, session=session, verify_ssl=verify_ssl)

        if price == PRICE_UNAVAILABLE:
            print(f"❌ [{code}] {name} - 주가를 가져오지 못했습니다.")
            continue

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