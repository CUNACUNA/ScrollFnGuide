
import io
import zipfile
import requests

from datetime import date, datetime, timedelta


BASE_URL = "https://opendart.fss.or.kr/api"


def check_kb_reports(api_key, corp_code):
    """KB금융의 2025년 반기보고서 최초공시와 정정공시 확인"""

    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": "20250801",
        "end_de": "20260331",
        "last_reprt_at": "N",
        "pblntf_ty": "A",
        "page_no": "1",
        "page_count": "100"
    }

    response = requests.get(
        f"{BASE_URL}/list.json",
        params=params,
        timeout=30
    )

    response.raise_for_status()
    data = response.json()

    print("\n========== KB금융 공시 목록 ==========")
    print("status:", data.get("status"))
    print("message:", data.get("message"))

    for item in data.get("list", []):
        report_name = item.get("report_nm", "")

        if "반기보고서" in report_name:
            print({
                "report_nm": report_name,
                "rcept_no": item.get("rcept_no"),
                "rcept_dt": item.get("rcept_dt"),
                "corp_code": item.get("corp_code")
            })




check_kb_reports("4552181bd152118d4e67ccd635119bbc4b81bfa5", "105560")



import requests
import zipfile
import io
import html
import re

API_KEY = "4552181bd152118d4e67ccd635119bbc4b81bfa5"
RECEIPT_NO = "20260324000827"

url = "https://opendart.fss.or.kr/api/document.xml"

response = requests.get(
    url,
    params={
        "crtfc_key": API_KEY,
        "rcept_no": RECEIPT_NO
    },
    timeout=60
)

content = response.content

# ZIP이 아닌 경우 API 오류 응답 확인
if not zipfile.is_zipfile(io.BytesIO(content)):
    print("ZIP 파일이 아닙니다.")
    print(content.decode("utf-8", errors="replace")[:2000])
    raise SystemExit

keywords = ["자기주식", "취득", "처분", "소각", "기초수량", "기말수량"]

with zipfile.ZipFile(io.BytesIO(content)) as z:
    print("========== 원문 파일 목록 ==========")
    for name in z.namelist():
        print(name)

    print("\n========== 자기주식 관련 내용 ==========")

    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        filename = z.namelist()[0]
        raw = z.read(filename)

        text = raw.decode("utf-8", errors="replace")
        text = html.unescape(text)

        # 정정 전/후 관련 문구의 위치 확인
        keywords = [
            "자기주식 직접 취득",
            "정정 전",
            "정정 후",
            "공시대상기간 중",
            "자기주식 현황"
        ]

        print("========== 키워드 위치 ==========")

        for keyword in keywords:
            positions = [
                m.start() for m in re.finditer(re.escape(keyword), text)
            ]
            print(f"{keyword}: {positions}")

        # 자기주식 관련 문구가 나오는 위치 주변을 넓게 출력
        print("\n========== 자기주식 관련 원문 ==========")

        positions = [
            m.start() for m in re.finditer("자기주식", text)
        ]

        for i, pos in enumerate(positions):
            start = max(0, pos - 1000)
            end = min(len(text), pos + 6000)

            print(f"\n========== 구간 {i + 1} ==========")
            print(text[start:end])