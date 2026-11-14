from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import pandas as pd
from io import StringIO
import time

# 1. 크롬 가상 브라우저를 띄우지 않고 백그라운드에서 실행하는 옵션
options = webdriver.ChromeOptions()
options.add_argument('--headless')  
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
options.add_argument('--window-size=1920,1080')
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

print("🚀 가상 브라우저를 구동하는 중입니다...")
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

try:
    # 2. 요청하신 wcomp 주소로 직접 접속
    target_url = "https://wcomp.fnguide.com/CompanyInfo/FinanceRatio?c_id=AA&menu_type=01&cmp_cd=005930"
    driver.get(target_url)
    
    print("🚀 자바스크립트 재무 데이터가 표에 완전히 채워질 때까지 대기 중...")
    
    # 3. [wcomp 전용 핵심 로직] 
    # wcomp 페이지는 데이터가 로드되면 클래스명이 'g_table' 또는 'u_table'인 영역에 숫자가 박힙니다.
    # 테이블 내부에 텍스트(숫자 데이터)를 가진 td 요소가 렌더링될 때까지 안전하게 대기합니다.
    WebDriverWait(driver, 12).until(
        EC.presence_of_element_located((By.XPATH, "//table//td[not(text()='')]"))
    )
    
    # 렌더링 안정화를 위해 2초 추가 대기
    time.sleep(2)
    
    # 4. 데이터 수치가 가득 찬 최종 HTML 소스 코드 획득
    complete_html = driver.page_source
    
    # 5. 판다스(Pandas)로 HTML 내부의 실제 테이블 추출
    dfs = pd.read_html(StringIO(complete_html))
    
    print(f"\n🎯 성공! 수치가 포함된 총 {len(dfs)}개의 테이블을 긁어왔습니다.")
    
    # 6. 가져온 테이블들을 하나의 엑셀 파일 내부 시트별로 분할 저장
    output_filename = "삼성전자_wcomp_재무비율_성공.xlsx"
    with pd.ExcelWriter(output_filename, engine="openpyxl") as writer:
        for i, df in enumerate(dfs):
            # 행이나 열이 완전히 비어있는 무의미한 테이블은 제외하고 저장
            if df.empty or len(df.columns) < 2:
                continue
                
            sheet_name = f"재무테이블_{i+1}"
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            
            # 첫 번째 주요 데이터 테이블 내용 콘솔 확인용 출력
            if i == 0:
                print("\n[첫 번째 테이블(Growth Ratio 등) 수집 데이터 확인]")
                print(df.head(10))
                
    print(f"\n📁 '{output_filename}' 파일에 데이터 저장이 완료되었습니다!")

except Exception as e:
    print(f"❌ 데이터 추출 실패: {e}")

finally:
    # 가상 브라우저 프로세스 완벽히 종료하여 메모리 확보
    driver.quit()
