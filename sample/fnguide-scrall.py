import requests
from bs4 import BeautifulSoup
import pandas as pd

# 1. 사용자가 접속하는 기본 주소 설정
main_url = "https://wcomp.fnguide.com/CompanyInfo/FinanceRatio?c_id=AA&menu_type=01&cmp_cd=005930"
# 실제 데이터가 숨겨져 있는 Ajax 요청 주소
ajax_url = "https://fnguide.com"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": main_url
}

# 세션을 생성하여 토큰과 쿠키를 유지합니다.
session = requests.Session()

# 2. 메인 페이지에 먼저 접속하여 숨겨진 보안 토큰(__RequestVerificationToken)을 가져옵니다.
response = session.get(main_url, headers=headers)
soup = BeautifulSoup(response.text, "html.parser")
token_element = soup.find("input", {"name": "__RequestVerificationToken"})

if token_element:
    token = token_element["value"]
    
    # 3. 진짜 데이터 주소(Ajax)에 보낼 데이터 구성
    payload = {
        "__RequestVerificationToken": token,
        "cmp_cd": "005930",  # 삼성전자 종목코드
        "c_id": "AA",
        "menu_type": "01"
    }
    
    # 4. Ajax 주소로 POST 요청을 보내 실제 데이터가 담긴 HTML 조각을 받아옵니다.
    ajax_response = session.post(ajax_url, data=payload, headers=headers)
    
    # 5. 받아온 HTML 조각을 Pandas로 읽어 테이블로 변환합니다.
    dfs = pd.read_html(ajax_response.text)
    
    print(f"성공적으로 {len(dfs)}개의 재무 테이블을 가져왔습니다.\n")
    
    # 첫 번째 테이블(연간 재무비율) 출력
    print("=== [연간 재무비율 데이터] ===")
    print(dfs[0].head(15))
    
else:
    print("보안 토큰을 찾지 못했습니다. 주소나 페이지 형식이 변경되었을 수 있습니다.")
