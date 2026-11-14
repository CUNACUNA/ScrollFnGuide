from pathlib import Path
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


def chrome_service():
    cached_drivers = list(
        Path.home().glob(
            ".wdm/drivers/chromedriver/win64/*/chromedriver-win64/chromedriver.exe"
        )
    )
    if cached_drivers:
        latest_driver = max(cached_drivers, key=lambda path: path.stat().st_mtime)
        return Service(str(latest_driver))
    return Service(ChromeDriverManager().install())


def change_and_extract(driver, freq_code):
    """
    FnGuide Snapshot 전용 조회 함수 um_fnSearch('Y' 또는 'Q')를 direct 호출하여
    #snpFinancial 영역 HTML을 가져옵니다.
    """
    # 1. FnGuide 페이지 내부 데이터 전환 함수 호출
    driver.execute_script(
        """
        const code = arguments[0];
        if (typeof um_fnSearch === 'function') {
            um_fnSearch(code);
        } else {
            const btn = document.querySelector(`#freqTyp button[value="${code}"]`);
            if (btn) btn.click();
        }
        """,
        freq_code,
    )

    # 2. Ajax 통신 및 DOM 교체 대기
    time.sleep(3)

    # 3. #snpFinancial 영역 HTML 추출
    snp_element = driver.find_element(By.ID, "snpFinancial")
    return snp_element.get_attribute("outerHTML")


options = webdriver.ChromeOptions()
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--window-size=1920,1080")
options.add_argument(
    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

print("가상 브라우저 구동 중...")
driver = webdriver.Chrome(service=chrome_service(), options=options)

try:
    stock_code = "005930"
    target_url = f"https://wcomp.fnguide.com/CompanyInfo/Snapshot?c_id=AA&menu_type=01&cmp_cd={stock_code}"
    driver.get(target_url)

    print("페이지 로딩 대기 중...")
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.ID, "snpFinancial"))
    )
    time.sleep(2)

    # 1. 연간(Y) 호출 및 저장
    print("'연간(Y)' 전환 함수 실행 중...")
    annual_html = change_and_extract(driver, "Y")
    with open(f"{stock_code}_Snapshot_Financial_Highlight_연간.html", "w", encoding="utf-8") as f:
        f.write(annual_html)
    print(" -> 연간 HTML 저장 완료")

    # 2. 분기(Q) 호출 및 저장
    print("'분기(Q)' 전환 함수 실행 중...")
    quarterly_html = change_and_extract(driver, "Q")
    with open(f"{stock_code}_Snapshot_Financial_Highlight_분기.html", "w", encoding="utf-8") as f:
        f.write(quarterly_html)
    print(" -> 분기 HTML 저장 완료")

except Exception as e:
    print(f"오류 발생: {e}")

finally:
    driver.quit()