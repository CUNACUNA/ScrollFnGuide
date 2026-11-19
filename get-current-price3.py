"""설정 파일(config.yaml)의 companylist_filename을 읽어 본주 및 우선주 전체 기업 리스트에 대해 네이버 금융 API에서 현재 주가를 수집한 후 MongoDB CURRENT-PRICE 테이블에 Overwrite 저장한다."""

from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path

from pymongo import MongoClient
import requests
import urllib3
import yaml

# 경고 메시지 비활성화
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# 1. 설정 및 상수 정의
# ==============================================================================
DEFAULT_MONGODB_URI = "mongodb://127.0.0.1:27017"
DB_NAME = "fnguide_db"          # 사용 중인 DB 이름
COLLECTION_NAME = "CURRENT-PRICE"  # 저장할 테이블(컬렉션) 이름
DEFAULT_CONFIG_FILE = "config.yaml"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
PRICE_UNAVAILABLE = "N/A"
REALTIME_API_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"


def parse_args() -> argparse.Namespace:
    """CLI 인자(--config) 파싱"""
    parser = argparse.ArgumentParser(
        description="네이버 금융 현재가 수집기 (MongoDB CURRENT-PRICE 저장)"
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_FILE,
        help=f"설정 YAML 파일 경로 (기본값: {DEFAULT_CONFIG_FILE})",
    )
    return parser.parse_args()


def load_config(config_path: str | Path) -> dict:
    """YAML 설정 파일 로드"""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"설정 파일 '{config_path}'을(를) 찾을 수 없습니다.")

    with path.open(encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    if not isinstance(data, dict):
        raise ValueError(
            f"설정 파일 '{config_path}'의 형식이 올바르지 않습니다 (딕셔너리 형태 필요)."
        )

    companylist_filename = data.get("companylist_filename")
    if not companylist_filename or not str(companylist_filename).strip():
        raise ValueError(
            f"설정 파일 '{config_path}'에 'companylist_filename' 필드가 없거나 비어 있습니다."
        )

    return data


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
# 3. companylist 파일 파싱 로직 (고정 컬럼: [구분, 종목명, 종목코드, (선택)본주코드])
# ==============================================================================
def parse_company_list(file_path: str | Path) -> list[dict]:
    """
    고정 컬럼 포맷([구분, 종목명, 종목코드, (선택)본주코드])의 종목 파일을 파싱한다.
    - 구분자: 탭('\t'), 쉼표(','), 슬래시('/'), 공백(' ') 지원
    - 6자리 숫자 및 영문/숫자 혼용 종목코드(예: 0001A0, 33626K) 지원
    - 우선주인 경우 4번째 항목(본주 코드)을 scrap_code로 지정
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"'{file_path}' 파일을 찾을 수 없습니다.")

    companies: list[dict] = []
    code_pattern = re.compile(r"^[0-9A-Za-z]{6}$")
    type_tokens = {"Preferred", "Normal", "우선주", "본주", "우선", "본"}

    with path.open(encoding="utf-8") as file:
        for line_no, raw_line in enumerate(file, 1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # 탭/쉼표/슬래시 기준으로 분할하되, 빈 뒤쪽 컬럼(예: 본주코드 없음)도 보존
            raw_parts = [p.strip() for p in re.split(r"[,/\t]", raw_line.rstrip("\r\n"))]
            while raw_parts and raw_parts[0] == "":
                raw_parts.pop(0)

            # 공백으로만 구분된 한 줄 처리
            if len(raw_parts) == 1:
                tokens = raw_parts[0].split()
                code_indices = [
                    i for i, tok in enumerate(tokens) if code_pattern.match(tok)
                ]
                if code_indices:
                    code_idx = code_indices[0]
                    scrap_code_from_tokens = (
                        tokens[code_indices[1]]
                        if len(code_indices) > 1
                        else tokens[code_idx]
                    )
                    prefix = tokens[:code_idx]
                    if prefix and prefix[0] in type_tokens:
                        stock_type = prefix[0]
                        name = " ".join(prefix[1:]).strip()
                    else:
                        stock_type = "Normal"
                        name = " ".join(prefix).strip()
                    code = tokens[code_idx]
                    parts = [stock_type, name, code, scrap_code_from_tokens]
                else:
                    parts = raw_parts
            else:
                parts = raw_parts

            if len(parts) < 3:
                print(f" ⚠️ [{file_path}:{line_no}] 컬럼 수 부족으로 건너뜀: {raw_line.strip()}")
                continue

            stock_type = parts[0] or "Normal"
            name = parts[1]
            code = parts[2].upper()
            scrap_code = (parts[3].upper() if len(parts) >= 4 and parts[3] else code)

            if not code_pattern.match(code):
                print(f" ⚠️ [{file_path}:{line_no}] 유효하지 않은 종목코드 '{code}'로 건너뜀")
                continue

            if scrap_code and not code_pattern.match(scrap_code):
                print(
                    f" ⚠️ [{file_path}:{line_no}] 유효하지 않은 본주코드 '{scrap_code}' "
                    f"-> 종목코드 '{code}'로 대체"
                )
                scrap_code = code

            if not name:
                print(f" ⚠️ [{file_path}:{line_no}] 종목명이 비어 있어 건너뜀")
                continue

            companies.append(
                {
                    "type": stock_type,
                    "name": name,
                    "code": code,
                    "scrap_code": scrap_code,
                }
            )

    return companies


# ==============================================================================
# 4. MongoDB 저장 및 수집 메인 로직
# ==============================================================================
def update_prices_to_mongodb(config_path: str | Path = DEFAULT_CONFIG_FILE):
    config = load_config(config_path)
    company_list_file = str(config["companylist_filename"]).strip()

    mongodb_uri = os.environ.get("MONGODB_URI", DEFAULT_MONGODB_URI)
    client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]
    collection = db[COLLECTION_NAME]

    print(f"[{config_path}] 설정에서 [{company_list_file}] 파일을 불러오는 중...")
    company_list = parse_company_list(company_list_file)
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
        args = parse_args()
        update_prices_to_mongodb(args.config)
    except Exception as e:
        print(f"오류 발생: {e}")