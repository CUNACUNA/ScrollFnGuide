# -*- coding: utf-8 -*-
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO
from pathlib import Path
import re
import threading
import time
import traceback
from bs4 import BeautifulSoup
import pandas as pd
from pymongo import MongoClient
from selenium import webdriver
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
import yaml

DEFAULT_CONFIG_FILE = "config.yaml"
DEFAULT_WORKERS = 10
DEFAULT_DRIVER_RESTART_INTERVAL = 100
MONGODB_URI = "mongodb://localhost:27017/"
DB_NAME = "fnguide_db"
COLLECTION_NAME = "stock_tables"

_print_lock = threading.Lock()
_driver_init_lock = threading.Lock()
_active_drivers_lock = threading.Lock()
_active_drivers: set[webdriver.Chrome] = set()
_stop_event = threading.Event()


def log(msg: str) -> None:
    """멀티스레드 환경에서 로그 출력이 섞이지 않도록 동기화"""
    with _print_lock:
        print(msg, flush=True)


def _sleep_or_stop(seconds: float) -> bool:
    """중단 신호가 오면 즉시 True를 반환하며 대기 종료"""
    return _stop_event.wait(timeout=max(0.0, seconds))


def _register_driver(driver: webdriver.Chrome) -> None:
    with _active_drivers_lock:
        _active_drivers.add(driver)


def _pop_driver(driver: webdriver.Chrome | None) -> bool:
    """등록된 드라이버 집합에서 제거하고, 처음 제거된 경우 True 반환(중복 quit 방지)"""
    if driver is None:
        return False
    with _active_drivers_lock:
        if driver in _active_drivers:
            _active_drivers.discard(driver)
            return True
        return False


def _safe_quit_driver(driver: webdriver.Chrome | None) -> None:
    if driver is None:
        return
    if not _pop_driver(driver):
        return
    try:
        driver.quit()
    except Exception:
        pass


def _cleanup_all_active_drivers(max_wait: float = 3.0) -> None:
    """Ctrl+C 등으로 중단 시 열려 있는 드라이버들을 백그라운드에서 최대한 정리"""
    with _active_drivers_lock:
        drivers = list(_active_drivers)
        _active_drivers.clear()

    if not drivers:
        return

    def _quit_detached(drv: webdriver.Chrome) -> None:
        try:
            drv.quit()
        except Exception:
            pass

    threads: list[threading.Thread] = []
    for drv in drivers:
        t = threading.Thread(target=_quit_detached, args=(drv,), daemon=True)
        t.start()
        threads.append(t)

    deadline = time.time() + max_wait
    for t in threads:
        remaining = deadline - time.time()
        if remaining > 0:
            t.join(timeout=remaining)


def parse_args() -> argparse.Namespace:
    """CLI 인자(--config) 파싱"""
    parser = argparse.ArgumentParser(
        description="FnGuide 재무제표 및 컨센서스 스크래퍼 (MongoDB 저장)"
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

    raw_workers = data.get("workers", DEFAULT_WORKERS)
    try:
        workers = int(raw_workers)
    except (TypeError, ValueError):
        raise ValueError(f"설정 파일 '{config_path}'의 'workers' 값은 정수여야 합니다.")

    if workers < 1:
        raise ValueError(f"설정 파일 '{config_path}'의 'workers' 값은 1 이상이어야 합니다.")

    raw_interval = data.get("driver_restart_interval", DEFAULT_DRIVER_RESTART_INTERVAL)
    try:
        restart_interval = int(raw_interval)
    except (TypeError, ValueError):
        raise ValueError(
            f"설정 파일 '{config_path}'의 'driver_restart_interval' 값은 정수여야 합니다."
        )

    if restart_interval < 1:
        raise ValueError(
            f"설정 파일 '{config_path}'의 'driver_restart_interval' 값은 1 이상이어야 합니다."
        )

    data["workers"] = workers
    data["driver_restart_interval"] = restart_interval
    return data


def parse_company_list(file_path: str | Path) -> list[dict]:
    """
    고정 컬럼 포맷([구분, 종목명, 종목코드, (선택)본주코드])의 종목 파일을 파싱한다.
    - 구분자: 탭('\t'), 쉼표(','), 슬래시('/'), 공백(' ') 지원
    - 6자리 숫자 및 영문/숫자 혼용 종목코드(예: 00104K, 0015G0) 지원
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
                log(f" ⚠️ [{file_path}:{line_no}] 컬럼 수 부족으로 건너뜀: {raw_line.strip()}")
                continue

            stock_type = parts[0] or "Normal"
            name = parts[1]
            code = parts[2].upper()
            scrap_code = (parts[3].upper() if len(parts) >= 4 and parts[3] else code)

            if not code_pattern.match(code):
                log(f" ⚠️ [{file_path}:{line_no}] 유효하지 않은 종목코드 '{code}'로 건너뜀")
                continue

            if scrap_code and not code_pattern.match(scrap_code):
                log(
                    f" ⚠️ [{file_path}:{line_no}] 유효하지 않은 본주코드 '{scrap_code}' "
                    f"-> 종목코드 '{code}'로 대체"
                )
                scrap_code = code

            if not name:
                log(f" ⚠️ [{file_path}:{line_no}] 종목명이 비어 있어 건너뜀")
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


def build_chrome_options() -> webdriver.ChromeOptions:
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-extensions")
    options.add_argument("--metrics-recording-only")
    options.add_argument("--mute-audio")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    return options


def create_driver(options: webdriver.ChromeOptions) -> webdriver.Chrome:
    """스레드 간 드라이버 초기화 경합을 막기 위해 Lock으로 보호"""
    if _stop_event.is_set():
        raise KeyboardInterrupt("중단 요청으로 브라우저 생성을 건너뜁니다.")
    with _driver_init_lock:
        if _stop_event.is_set():
            raise KeyboardInterrupt("중단 요청으로 브라우저 생성을 건너뜁니다.")
        service = chrome_service()
        driver = webdriver.Chrome(service=service, options=options)
        _register_driver(driver)
        return driver


def _restart_driver(
    current_driver: webdriver.Chrome | None,
    options: webdriver.ChromeOptions,
) -> webdriver.Chrome:
    """기존 브라우저 세션을 안전하게 종료하고 새 Chrome 인스턴스를 생성"""
    _safe_quit_driver(current_driver)
    if _stop_event.is_set():
        raise KeyboardInterrupt("중단 요청으로 웹엔진 재생성을 중단합니다.")
    return create_driver(options)


def _first_line(exc: Exception) -> str:
    msg = str(exc).strip()
    return msg.splitlines()[0] if msg else exc.__class__.__name__


def remove_target_phrase(html_str: str) -> str:
    """HTML 원문 정제 (웹서버 렌더링용)"""
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
    """HTML 표 원문을 pandas/BeautifulSoup으로 정제하여 딕셔너리로 파싱"""
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
    if _sleep_or_stop(2):
        return ""

    try:
        snp_element = driver.find_element(By.ID, "snpFinancial")
        html_val = snp_element.get_attribute("outerHTML")
        return remove_target_phrase(html_val) if html_val else ""
    except Exception:
        return ""


def extract_consensus_target_price(driver):
    """Snapshot 페이지 내 목표주가 컨센서스 영역 추출"""
    try:
        consensus_element = None
        selectors = ["#consensus", ".section.consensus", "div[id*='Consensus']", "div[id*='consensus']"]
        for sel in selectors:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                if elems:
                    consensus_element = elems[0]
                    break
            except Exception:
                continue

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
        log(f" -> 목표주가 컨센서스 추출 중 예외 발생: {_first_line(e)}")
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
    _sleep_or_stop(1.5)


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
        if _stop_event.is_set():
            break
        clicked = driver.execute_script(click_script)
        total_clicked += clicked
        if clicked == 0:
            break
        if _sleep_or_stop(0.7):
            break

    if not _stop_event.is_set():
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


def save_to_mongodb(
    collection,
    stock_code,
    company_name,
    snapshot_data,
    consensus_data,
    finance_annual_html,
    finance_quarterly_html,
    finance_annual_parsed,
    finance_quarterly_parsed,
):
    """MongoDB stock_tables 저장 (우선주 코드 기준으로 저장하되, 재무데이터는 본주 데이터를 매핑)"""
    filter_query = {"_id": stock_code}

    update_data = {
        "code": stock_code,
        "name": company_name,
        "tables.snapshot.annual": snapshot_data.get("annual", ""),
        "tables.snapshot.quarterly": snapshot_data.get("quarterly", ""),
        "tables.consensus.target_price": consensus_data.get("html", ""),
        "tables.포괄손익계산서.annual": finance_annual_html.get("income", ""),
        "tables.포괄손익계산서.quarterly": finance_quarterly_html.get("income", ""),
        "tables.재무상태표.annual": finance_annual_html.get("balance", ""),
        "tables.재무상태표.quarterly": finance_quarterly_html.get("balance", ""),
        "tables.현금흐름표.annual": finance_annual_html.get("cash", ""),
        "tables.현금흐름표.quarterly": finance_quarterly_html.get("cash", ""),
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
    log(f" -> MongoDB 저장 완료 (우선주 매핑): [{stock_code}] {company_name}")


def _run_company_on_driver(
    driver: webdriver.Chrome,
    collection,
    stock_code: str,
    scrap_code: str,
    company_name: str,
) -> None:
    """이미 생성된 driver 세션으로 단일 기업 수집 및 MongoDB 저장"""
    if _stop_event.is_set():
        return

    # 1. Snapshot 및 컨센서스(목표주가) 수집 (scrap_code 사용)
    snapshot_url = (
        f"https://wcomp.fnguide.com/CompanyInfo/Snapshot"
        f"?c_id=AA&menu_type=01&cmp_cd={scrap_code}"
    )
    driver.get(snapshot_url)
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "snpFinancial")))
    if _sleep_or_stop(1.5):
        return

    snapshot_annual_html = change_snapshot_extract(driver, "Y")
    if _stop_event.is_set():
        return
    snapshot_quarterly_html = change_snapshot_extract(driver, "Q")
    if _stop_event.is_set():
        return

    snapshot_annual_parsed = parse_html_table_to_dict(snapshot_annual_html)
    snapshot_quarterly_parsed = parse_html_table_to_dict(snapshot_quarterly_html)

    snapshot_data = {
        "annual": snapshot_annual_html,
        "quarterly": snapshot_quarterly_html,
        "annual_parsed": snapshot_annual_parsed,
        "quarterly_parsed": snapshot_quarterly_parsed,
    }

    # 1-1. 목표주가 컨센서스 추출 및 파싱
    consensus_html = extract_consensus_target_price(driver)
    consensus_parsed = parse_html_table_to_dict(consensus_html)

    consensus_data = {
        "html": consensus_html,
        "parsed": consensus_parsed,
    }

    if _stop_event.is_set():
        return

    # 2. Finance 수집 및 파싱 (scrap_code 사용)
    finance_url = (
        f"https://wcomp.fnguide.com/CompanyInfo/Finance"
        f"?c_id=AA&menu_type=01&cmp_cd={scrap_code}"
    )
    driver.get(finance_url)
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, "//table//td[normalize-space()]"))
    )
    if _sleep_or_stop(1.5):
        return

    finance_annual_html, finance_annual_parsed = extract_finance_3_tables(driver, "Y", "년간")
    if _stop_event.is_set():
        return
    finance_quarterly_html, finance_quarterly_parsed = extract_finance_3_tables(driver, "Q", "분기")
    if _stop_event.is_set():
        return

    # 3. MongoDB 저장 (식별자는 우선주 코드, 재무데이터는 본주 내용 매핑)
    save_to_mongodb(
        collection,
        stock_code,
        company_name,
        snapshot_data,
        consensus_data,
        finance_annual_html,
        finance_quarterly_html,
        finance_annual_parsed,
        finance_quarterly_parsed,
    )


def scrape_worker_chunk(
    chunk_items: list[tuple[int, dict]],
    total: int,
    options: webdriver.ChromeOptions,
    restart_interval: int,
    max_retries: int = 1,
) -> int:
    """워커 스레드 1개가 할당된 기업 묶음을 처리하며, restart_interval마다 웹엔진을 초기화한다."""
    driver: webdriver.Chrome | None = None
    mongo_client = MongoClient(MONGODB_URI)
    collection = mongo_client[DB_NAME][COLLECTION_NAME]
    processed_in_session = 0
    success_count = 0

    try:
        if _stop_event.is_set():
            return 0
        driver = create_driver(options)

        for idx, company in chunk_items:
            if _stop_event.is_set():
                break

            stock_code = company["code"]       # MongoDB 저장 식별자 (우선주인 경우 우선주 코드)
            scrap_code = company["scrap_code"] # FnGuide 스크랩 대상 코드 (우선주인 경우 본주 코드)
            company_name = company["name"]

            if processed_in_session >= restart_interval:
                log(
                    f" 🔄 [{stock_code}] {company_name} 누적 {processed_in_session}건 처리로 "
                    f"웹엔진 초기화..."
                )
                driver = _restart_driver(driver, options)
                processed_in_session = 0

            if _stop_event.is_set():
                break

            log(f"[{idx}/{total}] [{stock_code}] {company_name} (Scrap 기준: {scrap_code}) 크롤링 시작...")

            for attempt in range(max_retries + 1):
                if _stop_event.is_set():
                    break
                try:
                    _run_company_on_driver(driver, collection, stock_code, scrap_code, company_name)
                    if _stop_event.is_set():
                        break
                    processed_in_session += 1
                    success_count += 1
                    break
                except KeyboardInterrupt:
                    _stop_event.set()
                    break
                except InvalidSessionIdException as session_err:
                    if _stop_event.is_set():
                        break
                    log(
                        f" ⚠️ [{stock_code}] {company_name} 세션 무효화 감지, 웹엔진 재생성 "
                        f"(시도 {attempt + 1}/{max_retries + 1}): {_first_line(session_err)}"
                    )
                    try:
                        driver = _restart_driver(driver, options)
                    except KeyboardInterrupt:
                        _stop_event.set()
                        break
                    processed_in_session = 0
                    if attempt == max_retries:
                        log(f" ❌ [{stock_code}] {company_name} 세션 오류로 재시도 후 실패")
                except WebDriverException as wd_err:
                    if _stop_event.is_set():
                        break
                    log(
                        f" ⚠️ [{stock_code}] {company_name} WebDriver 오류로 웹엔진 재생성 "
                        f"(시도 {attempt + 1}/{max_retries + 1}): {_first_line(wd_err)}"
                    )
                    try:
                        driver = _restart_driver(driver, options)
                    except KeyboardInterrupt:
                        _stop_event.set()
                        break
                    processed_in_session = 0
                    if attempt == max_retries:
                        log(f" ❌ [{stock_code}] {company_name} 수집 중 WebDriver 오류로 실패")
                except Exception:
                    if _stop_event.is_set():
                        break
                    if attempt < max_retries:
                        log(
                            f" ↻ [{stock_code}] {company_name} 일시 오류로 재시도 "
                            f"({attempt + 1}/{max_retries})..."
                        )
                        if _sleep_or_stop(1.5):
                            break
                    else:
                        log(
                            f" ❌ [{stock_code}] {company_name} 수집 중 오류 발생:\n"
                            f"{traceback.format_exc()}"
                        )

    except KeyboardInterrupt:
        _stop_event.set()
    finally:
        _safe_quit_driver(driver)
        try:
            mongo_client.close()
        except Exception:
            pass

    return success_count


# ------------------------------------------------------------------------------
# 메인 실행 로직
# ------------------------------------------------------------------------------
def main() -> None:
    start_time = time.time()
    args = parse_args()
    config = load_config(args.config)
    company_list_file = str(config["companylist_filename"]).strip()
    workers = config["workers"]
    restart_interval = config["driver_restart_interval"]

    companies = parse_company_list(company_list_file)
    log(
        f"[{args.config}] 설정에서 [{company_list_file}] 파일을 읽어 "
        f"총 {len(companies)}개 기업을 불러왔습니다. "
        f"(병렬 워커: {workers}개, 웹엔진 초기화 주기: {restart_interval}건)"
    )

    if not companies:
        log("수집할 대상 기업이 없습니다.")
        return

    init_client = MongoClient(MONGODB_URI)
    try:
        init_client[DB_NAME][COLLECTION_NAME].drop()
        log(f"🧹 기존 {COLLECTION_NAME} DB 컬렉션을 성공적으로 초기화했습니다.\n")
    finally:
        init_client.close()

    options = build_chrome_options()
    success_count = 0

    chunks: list[list[tuple[int, dict]]] = [[] for _ in range(workers)]
    for i, company in enumerate(companies, 1):
        chunks[(i - 1) % workers].append((i, company))

    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [
            executor.submit(
                scrape_worker_chunk,
                chunk,
                len(companies),
                options,
                restart_interval,
            )
            for chunk in chunks
            if chunk
        ]

        for future in as_completed(futures):
            if _stop_event.is_set():
                break
            success_count += future.result()

    except KeyboardInterrupt:
        _stop_event.set()
        log("\n 🛑 Ctrl+C 감지! 진행 중인 작업을 중단하고 정리 중입니다...")
        executor.shutdown(wait=False, cancel_futures=True)
        _cleanup_all_active_drivers(max_wait=3.0)
    else:
        executor.shutdown(wait=True)

    elapsed = time.time() - start_time
    status_label = "중단됨" if _stop_event.is_set() else "완료"
    log(
        f"\n{status_label}: 총 {len(companies)}개 중 {success_count}개 기업 수집 및 DB 저장 완료 "
        f"(소요 시간: {elapsed:.1f}초)"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _stop_event.set()
        _cleanup_all_active_drivers(max_wait=2.0)
        log("\n 🛑 프로그램이 사용자에 의해 중단되었습니다.")
    except Exception as e:
        log(f"전체 프로세스 오류 발생: {e}")