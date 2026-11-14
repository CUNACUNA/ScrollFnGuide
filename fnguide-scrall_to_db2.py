# -*- coding: utf-8 -*-
from io import StringIO
import re
import time
import traceback
from pathlib import Path
import pandas as pd
from bs4 import BeautifulSoup
from pymongo import MongoClient
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

COMPANY_LIST_FILE = "companylist.txt"


def parse_company_list(file_path: str | Path) -> list[tuple[str, str]]:
    """companylist.txt 파싱"""
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

            if code_pattern.fullmatch(parts[0]):
                code, name = parts[0], parts[1]
            elif code_pattern.fullmatch(parts[1]):
                name, code = parts[0], parts[1]
            else:
                continue

            companies.append((code, name))

    return companies


def chrome_service():
    """ChromeDriver 서비스 구동"""
    cached_drivers = list(
        Path.home().glob(
            ".wdm/drivers/chromedriver/win64/*/chromedriver-win64/chromedriver.exe"
        )
    )
    if cached_drivers:
        latest_driver = max(cached_drivers, key=lambda path: path.stat().st_mtime)
        return Service(str(latest_driver))
    return Service(ChromeDriverManager().install())


def remove_target_phrase(html_str: str) -> str:
    """
    HTML 원문 정제 (웹서버 렌더링용):
    1. 불필요한 토글/툴팁 버튼 완전 제거
    2. (E) 툴팁 설명 태그(<dl>, <dt>, <dd>) 완전히 삭제하여 잔상 텍스트 제거
    3. 표 헤더(th)의 <br> 및 문구 정리
    """
    if not html_str:
        return ""
    soup = BeautifulSoup(html_str, "html.parser")
    
    for dl in soup.find_all("dl"):
        dl.decompose()
    
    for btn in soup.find_all("button", class_=lambda c: c and "acd_btn" in c):
        btn.decompose()

    for btn in soup.find_all("button", class_=lambda c: c and "tip_in" in c):
        btn.replace_with(btn.get_text(strip=True))
        
    for tag in soup.find_all(True):
        if tag.has_attr("title") and "계산에 참여한 계정 펼치기" in tag["title"]:
            del tag["title"]
            
    for th in soup.find_all("th"):
        for br in th.find_all("br"):
            br.replace_with(" ")
        th_text = th.get_text(strip=True)
        if "(IFRS" in th_text or "Estimate" in th_text:
            clean_th_text = re.sub(r"\(IFRS.*?\)", "", th_text)
            clean_th_text = re.sub(r"\(E\)\s*:\s*Estimate.*?(?=\d{4}/)", "", clean_th_text).strip()
            th.string = clean_th_text if clean_th_text else th_text

    clean_html = str(soup).replace("계산에 참여한 계정 펼치기", "")
    return clean_html
    
    
def parse_html_table_to_dict(html_str: str) -> dict:
    """
    HTML 표 원문을 pandas/BeautifulSoup으로 정제하여
    계정과목별 날짜 수치 딕셔너리로 정확히 파싱한다.
    """
    if not html_str:
        return {}
    try:
        soup = BeautifulSoup(html_str, "html.parser")
        
        for br in soup.find_all("br"):
            br.replace_with(" ")

        for tag in soup.find_all(["script", "style", "dl"]):
            tag.decompose()
            
        for btn in soup.find_all("button"):
            btn.replace_with(btn.get_text(strip=True))

        dfs = pd.read_html(StringIO(str(soup)))
        if not dfs:
            return {}
        
        df = dfs[0]
        
        raw_cols = []
        if isinstance(df.columns, pd.MultiIndex):
            raw_cols = [str(col[-1]).strip() for col in df.columns]
        else:
            raw_cols = [str(col).strip() for col in df.columns]
            
        clean_cols = []
        for col in raw_cols:
            col_single = col.replace("\n", " ").replace("\r", "").strip()
            date_match = re.search(r"(\d{4}/\d{2}(?:\(E\))?)", col_single)
            if date_match:
                clean_cols.append(date_match.group(1))
            else:
                clean_name = re.sub(r"\(IFRS.*?\)", "", col_single).strip()
                clean_cols.append(clean_name if clean_name else col_single)
                
        df.columns = clean_cols
            
        first_col = df.columns[0]
        df[first_col] = df[first_col].astype(str).str.strip()
        df.set_index(first_col, inplace=True)
        
        parsed_dict = {}
        for row_label, row in df.iterrows():
            if not row_label or row_label == "nan":
                continue
            row_dict = {}
            for col_label, val in row.items():
                col_str = str(col_label).strip()
                val_str = re.sub(r"[^\d.-]", "", str(val))
                try:
                    num_val = float(val_str) if "." in val_str else int(val_str)
                    row_dict[col_str] = num_val
                except ValueError:
                    row_dict[col_str] = str(val) if pd.notna(val) else None
            parsed_dict[row_label] = row_dict
            
        return parsed_dict
    except Exception:
        return {}


def change_snapshot_extract(driver, freq_code):
    """Snapshot 표 outerHTML 추출 후 HTML 정제 적용"""
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
    time.sleep(2)

    try:
        snp_element = driver.find_element(By.ID, "snpFinancial")
        html_val = snp_element.get_attribute("outerHTML")
        return remove_target_phrase(html_val) if html_val else ""
    except Exception:
        return ""


def extract_consensus_target_price(driver):
    """
    Snapshot 페이지 내 '투자의견 컨센서스' 또는 '목표주가' 관련 표(Table) 영역을 추출.
    FnGuide Snapshot 페이지 구조상 컨센서스 테이블 ID 또는 클래스 명을 탐색하여 가져옴.
    """
    try:
        # FnGuide Snapshot 내 컨센서스/목표주가 관련 컨테이너나 테이블 탐색
        # 보통 id="consensus" 혹은 관련 class명으로 존재함. 없으면 테이블 태그 중 '목표주가' 텍스트 포함된 테이블 탐색
        consensus_element = None
        
        # 1. 일반적인 컨센서스 영역 ID 시도 (예: divConsensus 등, 페이지 구조에 맞춰 수정 가능)
        selectors = ["#consensus", ".section.consensus", "div[id*='Consensus']", "div[id*='consensus']"]
        for sel in selectors:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                if elems:
                    consensus_element = elems[0]
                    break
            except:
                continue
                
        # 2. 만약 특정 ID를 못 찾았다면 '목표주가'나 '컨센서스' 텍스트가 포함된 테이블을 탐색
        if not consensus_element:
            tables = driver.find_elements(By.TAG_NAME, "table")
            for t in tables:
                t_text = t.text
                if "목표주가" in t_text or "컨센서스" in t_text or " 투자의견 " in t_text:
                    consensus_element = t
                    break

        if consensus_element:
            html_val = consensus_element.get_attribute("outerHTML")
            return remove_target_phrase(html_val) if html_val else ""
        
        return ""
    except Exception as e:
        print(f" -> 목표주가 컨센서스 추출 중 예외 발생: {e}")
        return ""


def select_financial_frequency(driver, frequency_code, frequency_name):
    """연간/분기 버튼 전환 및 대기"""
    driver.execute_script(
        """
        const expected = arguments[0];
        const buttons = [...document.querySelectorAll(
            `.freqTypBtn button[value="${expected}"]`
        )];
        for (const button of buttons) {
            if (!button.classList.contains('ac')) button.click();
        }
        return buttons.length;
        """,
        frequency_code,
    )

    WebDriverWait(driver, 20).until(
        lambda current_driver: current_driver.execute_script(
            """
            const expected = arguments[0];
            const buttons = [...document.querySelectorAll('.freqTypBtn button.ac')];
            return buttons.length === 3 && buttons.every(button => button.value === expected);
            """,
            frequency_code,
        )
    )
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, "//table//td[normalize-space()]"))
    )
    time.sleep(1.5)


def expand_all_table_details(driver, max_rounds=20):
    """'+' 버튼 재귀적 펼치기"""
    click_script = """
        const plusPattern = /(^|[-_\\s])(ico[-_]?|btn[-_]?)?(plus|expand|open)([-_\\s]|$)/i;
        const expandWords = /(펼치기|상세보기|확장|expand|open)/i;
        const isVisible = (el) => {
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden'
                && el.getClientRects().length > 0;
        };
        const isPlusControl = (el) => {
            const classAndId = `${el.className || ''} ${el.id || ''}`;
            const labels = [
                el.getAttribute('title') || '',
                el.getAttribute('aria-label') || '',
                el.getAttribute('data-original-title') || '',
                el.textContent || ''
            ].join(' ');
            return plusPattern.test(classAndId)
                || expandWords.test(labels)
                || (el.textContent || '').trim() === '+';
        };

        const controls = [...document.querySelectorAll(
            'table a, table button, table [role="button"], table span, table i, table em'
        )].filter((el) => {
            if (!isVisible(el) || !isPlusControl(el)) return false;
            const control = el.closest('a, button, [role="button"], td, th') || el;
            return !control.dataset.scraperExpanded
                && control.getAttribute('aria-expanded') !== 'true';
        }).map((el) => el.closest('a, button, [role="button"], td, th') || el);

        const uniqueControls = [...new Set(controls)];
        for (const control of uniqueControls) {
            control.dataset.scraperExpanded = 'true';
            control.scrollIntoView({block: 'center'});
            try { control.click(); } catch(e) {}
        }
        return uniqueControls.length;
    """

    total_clicked = 0
    for _ in range(max_rounds):
        clicked = driver.execute_script(click_script)
        total_clicked += clicked
        if clicked == 0:
            break
        time.sleep(0.7)

    driver.execute_script(
        "document.querySelectorAll('[data-scraper-expanded]').forEach("
        "el => el.removeAttribute('data-scraper-expanded'));"
    )
    return total_clicked


def extract_finance_3_tables(driver, freq_code, freq_name):
    """Finance 3개 표 HTML 및 구조화 딕셔너리 동시 생성"""
    select_financial_frequency(driver, freq_code, freq_name)
    expand_all_table_details(driver)

    soup = BeautifulSoup(driver.page_source, "html.parser")
    html_tables = {}
    parsed_tables = {}

    div_map = [
        ("income", "divSonikTB", "포괄손익계산서"),
        ("balance", "divDaejoTB", "재무상태표"),
        ("cash", "divCashTB", "현금흐름표"),
    ]

    for key, div_id, title in div_map:
        target_el = soup.find(id=div_id)
        if target_el:
            html_val = str(target_el)
        else:
            tables = soup.find_all("table")
            valid_tables = [t for t in tables if not (t.find("caption") and "캡션" in t.find("caption").get_text())]
            idx = 0 if key == "income" else (1 if key == "balance" else 2)
            html_val = str(valid_tables[idx]) if len(valid_tables) > idx else ""

        if html_val:
            cleaned_html = remove_target_phrase(html_val)
            html_tables[key] = cleaned_html
            parsed_tables[key] = parse_html_table_to_dict(cleaned_html)
        else:
            html_tables[key] = ""
            parsed_tables[key] = {}

    return html_tables, parsed_tables


def save_to_mongodb(stock_code, company_name, snapshot_data, consensus_data, finance_annual_html, finance_quarterly_html, finance_annual_parsed, finance_quarterly_parsed):
    """MongoDB stock_tables 저장 (HTML + Snapshot + 목표주가 컨센서스 포함 Parsed JSON)"""
    client = MongoClient("mongodb://localhost:27017/")
    db = client["fnguide_db"]
    collection = db["stock_tables"]

    filter_query = {"_id": stock_code}

    update_data = {
        "code": stock_code,
        "name": company_name,
        
        # 1. 웹서버 카드 렌더링용 HTML (목표주가 컨센서스 포함)
        "tables.snapshot.annual": snapshot_data.get("annual", ""),
        "tables.snapshot.quarterly": snapshot_data.get("quarterly", ""),
        "tables.consensus.target_price": consensus_data.get("html", ""),
        "tables.포괄손익계산서.annual": finance_annual_html.get("income", ""),
        "tables.포괄손익계산서.quarterly": finance_quarterly_html.get("income", ""),
        "tables.재무상태표.annual": finance_annual_html.get("balance", ""),
        "tables.재무상태표.quarterly": finance_quarterly_html.get("balance", ""),
        "tables.현금흐름표.annual": finance_annual_html.get("cash", ""),
        "tables.현금흐름표.quarterly": finance_quarterly_html.get("cash", ""),
        
        # 2. 빠른 지표 계산용 JSON 딕셔너리 (목표주가 컨센서스 Parsed 포함)
        "parsed_data.snapshot.annual": snapshot_data.get("annual_parsed", {}),
        "parsed_data.snapshot.quarterly": snapshot_data.get("quarterly_parsed", {}),
        "parsed_data.consensus.target_price": consensus_data.get("parsed", {}),
        "parsed_data.포괄손익계산서.annual": finance_annual_parsed.get("income", {}),
        "parsed_data.포괄손익계산서.quarterly": finance_quarterly_parsed.get("income", {}),
        "parsed_data.재무상태표.annual": finance_annual_parsed.get("balance", {}),
        "parsed_data.재무상태표.quarterly": finance_quarterly_parsed.get("balance", {}),
        "parsed_data.현금흐름표.annual": finance_annual_parsed.get("cash", {}),
        "parsed_data.현금흐름표.quarterly": finance_quarterly_parsed.get("cash", {}),
        
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    collection.update_one(filter_query, {"$set": update_data}, upsert=True)
    print(f" -> MongoDB 저장 완료 (컨센서스 목표주가 포함): [{stock_code}] {company_name}")


# ------------------------------------------------------------------------------
# 메인 실행 로직
# ------------------------------------------------------------------------------
options = webdriver.ChromeOptions()
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu")
options.add_argument("--window-size=1920,1080")
options.add_argument(
    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

try:
    companies = parse_company_list(COMPANY_LIST_FILE)
    print(f"[{COMPANY_LIST_FILE}] 파일에서 총 {len(companies)}개 기업을 불러왔습니다.")

    client = MongoClient("mongodb://localhost:27017/")
    client["fnguide_db"]["stock_tables"].drop()
    print("🧹 기존 stock_tables DB 컬렉션을 성공적으로 초기화했습니다.\n")

    print("가상 브라우저 구동 중...")
    driver = webdriver.Chrome(service=chrome_service(), options=options)

    for idx, (stock_code, company_name) in enumerate(companies, 1):
        print(f"[{idx}/{len(companies)}] [{stock_code}] {company_name} 크롤링 시작...")

        try:
            # 1. Snapshot 및 컨센서스(목표주가) 수집
            snapshot_url = f"https://wcomp.fnguide.com/CompanyInfo/Snapshot?c_id=AA&menu_type=01&cmp_cd={stock_code}"
            driver.get(snapshot_url)
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "snpFinancial")))
            time.sleep(1.5)

            snapshot_annual_html = change_snapshot_extract(driver, "Y")
            snapshot_quarterly_html = change_snapshot_extract(driver, "Q")
            
            snapshot_annual_parsed = parse_html_table_to_dict(snapshot_annual_html)
            snapshot_quarterly_parsed = parse_html_table_to_dict(snapshot_quarterly_html)

            snapshot_data = {
                "annual": snapshot_annual_html,
                "quarterly": snapshot_quarterly_html,
                "annual_parsed": snapshot_annual_parsed,
                "quarterly_parsed": snapshot_quarterly_parsed,
            }

            # 1-1. 목표주가 컨센서스 추출 및 파싱
            print(" -> '투자의견 컨센서스 / 목표주가' 영역 추출 중...")
            consensus_html = extract_consensus_target_price(driver)
            consensus_parsed = parse_html_table_to_dict(consensus_html)
            
            consensus_data = {
                "html": consensus_html,
                "parsed": consensus_parsed
            }

            # 2. Finance 수집 및 파싱
            finance_url = f"https://wcomp.fnguide.com/CompanyInfo/Finance?c_id=AA&menu_type=01&cmp_cd={stock_code}"
            driver.get(finance_url)
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//table//td[normalize-space()]"))
            )
            time.sleep(1.5)

            print(" -> Finance 연간(Y) 3개 표 추출 및 JSON 파싱 중...")
            finance_annual_html, finance_annual_parsed = extract_finance_3_tables(driver, "Y", "년간")

            print(" -> Finance 분기(Q) 3개 표 추출 및 JSON 파싱 중...")
            finance_quarterly_html, finance_quarterly_parsed = extract_finance_3_tables(driver, "Q", "분기")

            # 3. MongoDB 저장 (컨센서스 데이터 추가 전달)
            save_to_mongodb(
                stock_code, company_name, snapshot_data, consensus_data,
                finance_annual_html, finance_quarterly_html,
                finance_annual_parsed, finance_quarterly_parsed
            )

        except Exception as item_error:
            print(f" ❌ [{stock_code}] {company_name} 수집 중 오류 발생:")
            print(traceback.format_exc())

        print("-" * 65)

    print("\n모든 기업의 표 및 컨센서스 목표주가 수집과 DB 저장이 완료되었습니다!")

except Exception as e:
    print(f"전체 프로세스 오류 발생: {e}")

finally:
    if 'driver' in locals():
        driver.quit()