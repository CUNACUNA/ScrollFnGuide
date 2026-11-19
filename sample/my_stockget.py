import io
import zipfile
import requests

from datetime import date, datetime, timedelta


BASE_URL = "https://opendart.fss.or.kr/api"

# 반기보고서 기준으로 변경
REPORTS = [
    ("11012", "반기"),
]

QTY_FIELDS = [
    "bsis_qy",
    "change_qy_acqs",
    "change_qy_dsps",
    "change_qy_incnr",
    "trmend_qy",
]


class DartError(Exception):
    pass


def to_int(value):
    """DART 수량 문자열을 정수로 변환 (예외 처리 강화)"""
    if value is None:
        return None
        
    value = str(value).strip().replace(",", "").replace(" ", "")

    if value in ("", "-", "nan", "None"):
        return None

    if value.startswith("(") and value.endswith(")"):
        try:
            return -int(value[1:-1])
        except ValueError:
            return None

    try:
        return int(value)
    except ValueError:
        return None


def get_corp_code(api_key, stock_code):
    """6자리 종목코드 -> DART 고유번호"""

    url = f"{BASE_URL}/corpCode.xml"

    response = requests.get(
        url,
        params={"crtfc_key": api_key},
        timeout=30
    )
    response.raise_for_status()

    if response.content[:2] != b"PK":
        raise DartError(
            "고유번호 ZIP 다운로드 실패: " +
            response.text[:300]
        )

    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        xml_name = next(
            (n for n in z.namelist()
             if n.upper().endswith("CORPCODE.XML")),
            None
        )

        if xml_name is None:
            raise DartError("CORPCODE.xml 파일을 찾을 수 없습니다.")

        import xml.etree.ElementTree as ET
        root = ET.fromstring(z.read(xml_name))

    for item in root.findall("list"):
        code = (item.findtext("stock_code") or "").strip()

        if code == stock_code:
            return {
                "corp_code": item.findtext("corp_code"),
                "corp_name": item.findtext("corp_name")
            }

    raise DartError(
        f"종목코드 {stock_code}를 찾을 수 없습니다."
    )


def get_treasury_report(
    api_key, corp_code, year, report_code
):
    """DART 자기주식 취득 및 처분 현황 API"""

    response = requests.get(
        f"{BASE_URL}/tesstkAcqsDspsSttus.json",
        params={
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": report_code
        },
        timeout=30
    )

    response.raise_for_status()
    data = response.json()

    status = data.get("status")

    # 데이터가 없는 경우(013) 빈 데이터 구조로 반환하여 에러 방지
    if status == "013":
        return {
            "state": "empty_placeholder",
            "rows": [],
            "message": data.get("message")
        }

    if status != "000":
        raise DartError(
            f"DART API 오류 {status}: {data.get('message')}"
        )

    rows = data.get("list", [])

    valid_rows = []
    for row in rows:
        values = [
            to_int(row.get(field))
            for field in QTY_FIELDS
        ]

        if any(v is not None for v in values):
            valid_rows.append(row)

    if not valid_rows:
        return {
            "state": "empty_placeholder",
            "rows": [],
            "message": "실제 수량 데이터가 없는 응답"
        }

    return {
        "state": "available",
        "rows": valid_rows,
        "message": "정상 데이터"
    }


def extract_totals(rows):
    """취득방법 계층의 총계 행을 추출"""
    if not rows:
        return {"acq": 0, "dsp": 0, "cancel": 0, "begin": 0, "end": 0}

    method_fields = [
        "acqs_mth1",
        "acqs_mth2",
        "acqs_mth3"
    ]

    totals = {}

    for row in rows:
        stock = row.get("stock_knd", "").strip()

        if not stock or stock == "-":
            continue

        matched_level = None

        for level, field in enumerate(method_fields):
            raw_val = row.get(field, "")
            value = str(raw_val or "").strip().replace(" ", "")

            if value in ("총계", "합계", "총계(합계)", "계"):
                matched_level = level
                break

        if matched_level is None:
            continue

        old = totals.get(stock)

        if old is None or matched_level > old["level"]:
            totals[stock] = {
                "level": matched_level,
                "row": row
            }

    if not totals:
        return {"acq": 0, "dsp": 0, "cancel": 0, "begin": 0, "end": 0}

    result = {
        "acq": 0,
        "dsp": 0,
        "cancel": 0,
        "begin": 0,
        "end": 0
    }

    for item in totals.values():
        row = item["row"]

        result["acq"] += to_int(row.get("change_qy_acqs")) or 0
        result["dsp"] += to_int(row.get("change_qy_dsps")) or 0
        result["cancel"] += to_int(row.get("change_qy_incnr")) or 0
        result["begin"] += to_int(row.get("bsis_qy")) or 0
        result["end"] += to_int(row.get("trmend_qy")) or 0

    return result


def get_report_data(
    api_key, corp_code, year, report_code
):
    """보고서 조회 후 총계 추출 (비어있어도 0으로 처리)"""

    response = get_treasury_report(
        api_key, corp_code, year, report_code
    )

    if response["state"] != "available":
        return {
            "state": "available",
            "data": {"acq": 0, "dsp": 0, "cancel": 0, "begin": 0, "end": 0},
            "rows": []
        }

    return {
        "state": "available",
        "data": extract_totals(response["rows"]),
        "rows": response["rows"]
    }


def get_ttm_treasury(api_key, stock_code):
    """
    반기 보고서 기준 최근 12개월 자기주식 취득/처분/소각 수량 조회.
    TTM = (당해 반기 누적) + (전년도 사업보고서 연간) - (전년도 반기 누적)
    """

    if not (
        isinstance(stock_code, str)
        and len(stock_code) == 6
        and stock_code.isdigit()
    ):
        raise ValueError("종목코드는 6자리 문자열이어야 합니다.")

    today = date.today()

    company = get_corp_code(api_key, stock_code)
    corp_code = company["corp_code"]

    diagnostics = []

    for report_code, report_name in REPORTS:
        year = today.year
        previous_year = year - 1

        # 당해 반기, 전년도 반기, 전년도 사업보고서(연간)
        current = get_report_data(api_key, corp_code, year, "11012")
        previous = get_report_data(api_key, corp_code, previous_year, "11012")
        annual = get_report_data(api_key, corp_code, previous_year, "11011")

        diagnostics.append({
            "report": report_name,
            "current_state": current["state"],
            "previous_state": previous["state"],
            "annual_state": annual["state"]
        })

        c = current["data"]
        p = previous["data"]
        a = annual["data"]

        # TTM 연산: 당해 반기 + 전년도 연간 - 전년도 반기
        acquired = c["acq"] + a["acq"] - p["acq"]
        disposed = c["dsp"] + a["dsp"] - p["dsp"]
        cancelled = c["cancel"] + a["cancel"] - p["cancel"]

        if min(acquired, disposed, cancelled) < 0:
            raise DartError(
                "계산 결과에 음수가 발생했습니다. "
                "정정공시 또는 보고서 계정 구조를 확인하세요."
            )

        return {
            "stock_code": stock_code,
            "corp_code": corp_code,
            "corp_name": company["corp_name"],

            "report": report_name,
            "acquired_shares": acquired,
            "disposed_shares": disposed,
            "cancelled_shares": cancelled,
            "net_acquired_shares": acquired - disposed,

            "data_status": "verified_report_basis",
            "calculated_at": today.isoformat(),
            "diagnostics": diagnostics
        }


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():
    api_key = "4552181bd152118d4e67ccd635119bbc4b81bfa5"

    # KB금융지주 종목코드 (105560)
    stock_code = "105560"

    result = get_ttm_treasury(
        api_key,
        stock_code
    )

    print("\n========== 자기주식 현황 (반기 기준 TTM) ==========")

    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()