from pathlib import Path
import time
from pymongo import MongoClient
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


def chrome_service():
    """이미 내려받은 ChromeDriver가 있으면 그것을 우선 사용합니다."""
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
    """FnGuide Snapshot 전용 조회 함수 um_fnSearch('Y' 또는 'Q')를 호출하여 #snpFinancial HTML 추출"""
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
    time.sleep(3)
    snp_element = driver.find_element(By.ID, "snpFinancial")
    return snp_element.get_attribute("outerHTML")


def save_to_mongodb(stock_code, annual_html, quarterly_html):
    """종목 코드(stock_code)를 primary key로 삼아 snapshot > 연간/분기 표를 upsert 저장"""
    client = MongoClient("mongodb://localhost:27017/")
    db = client["fnguide_db"]
    collection = db["stock_tables"]

    # Dot notation($set)을 활용해 문서 전체를 덮어쓰지 않고 snapshot 하위 표만 갱신/추가
    filter_query = {"_id": stock_code}
    update_query = {
        "$set": {
            "code": stock_code,
            "tables.snapshot.annual": annual_html,
            "tables.snapshot.quarterly": quarterly_html,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    }

    result = collection.update_one(filter_query, update_query, upsert=True)
    if result.upserted_id:
        print(f" -> MongoDB 신규 종목 문서 생성 완료 (키: {stock_code})")
    else:
        print(f" -> MongoDB 기존 종목 문서 표 갱신 완료 (키: {stock_code})")


def get_snapshot_annual_table(stock_code):
    """나중에 특정 종목의 snapshot > 연간 데이터 표만 추출하는 예시 함수"""
    client = MongoClient("mongodb://localhost:27017/")
    db = client["fnguide_db"]
    collection = db["stock_tables"]

    # _id로 직접 빠른 조회
    doc = collection.find_one({"_id": stock_code})
    if doc and "tables" in doc and "snapshot" in doc["tables"]:
        return doc["tables"]["snapshot"].get("annual")
    return None


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

    # 1. 연간(Y) 및 분기(Q) 표 추출
    print("'연간(Y)' 표 추출 중...")
    annual_html = change_and_extract(driver, "Y")

    print("'분기(Q)' 표 추출 중...")
    quarterly_html = change_and_extract(driver, "Q")

    # 2. MongoDB 저장 (Upsert)
    print("MongoDB 저장 중...")
    save_to_mongodb(stock_code, annual_html, quarterly_html)

    # 3. 추출 테스트 예시
    saved_annual_html = get_snapshot_annual_table(stock_code)
    if saved_annual_html:
        print(f" -> DB 추출 성공: {stock_code} 연간 표 HTML 길이는 {len(saved_annual_html)}자 입니다.")

except Exception as e:
    print(f"오류 발생: {e}")

finally:
    driver.quit()