from io import StringIO
from pathlib import Path
import time

import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


def expand_all_table_details(driver, max_rounds=20):
    """표 안에서 아직 펼쳐지지 않은 '+' 토글을 모두 한 번씩 클릭한다.

    FnGuide 화면은 항목을 펼칠 때마다 하위 행과 새로운 '+' 버튼을 동적으로
    만들 수 있으므로, 한 번 찾고 끝내지 않고 더 이상 새 버튼이 없을 때까지
    반복한다. 이미 처리한 버튼에는 data-scraper-expanded 속성을 붙여 같은
    버튼을 다시 클릭해서 접히는 일을 막는다.
    """
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

        // 아이콘(span/i) 자체가 아니라 실제 이벤트가 걸린 조상을 클릭한다.
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
            control.click();
        }
        return uniqueControls.length;
    """

    total_clicks = 0
    for round_number in range(1, max_rounds + 1):
        clicked = driver.execute_script(click_script)
        total_clicks += clicked
        print(f"  - 펼치기 {round_number}회차: {clicked}개 클릭")
        if clicked == 0:
            break
        # 클릭 후 추가 행/토글이 DOM에 반영될 시간을 준다.
        time.sleep(0.7)
    else:
        print(f"경고: {max_rounds}회까지 펼치기를 반복했습니다. 남은 버튼이 있는지 확인하세요.")

    # 저장 파일에서 data-* 표식을 남기지 않는다.
    driver.execute_script(
        "document.querySelectorAll('[data-scraper-expanded]').forEach("
        "el => el.removeAttribute('data-scraper-expanded'));"
    )
    return total_clicks


def read_data_tables(html):
    """표시용 템플릿(캡션이 '캡션')을 제외하고 실제 표만 DataFrame으로 읽는다."""
    soup = BeautifulSoup(html, "html.parser")
    dataframes = []

    for table in soup.find_all("table"):
        caption = table.find("caption")
        caption_text = caption.get_text(" ", strip=True) if caption else ""
        if caption_text == "캡션":
            continue

        try:
            # 표 단위로 읽어야 제외한 템플릿 표가 결과 목록에 섞이지 않는다.
            dataframes.extend(pd.read_html(StringIO(str(table))))
        except ValueError:
            # 행이 없는 레이아웃용 표는 데이터 표가 아니므로 건너뛴다.
            continue

    return dataframes


def select_financial_scope(driver, scope_name):
    """'연결' 또는 '별도' 탭을 현재 페이지에서 실제로 클릭한다."""
    clicked = driver.execute_script(
        """
        const expected = arguments[0];
        const visible = (el) => {
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden'
                && el.getClientRects().length > 0;
        };
        const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
        const candidates = [...document.querySelectorAll(
            'a, button, [role="tab"], [role="button"], label, span'
        )].filter((el) => visible(el) && text(el) === expected);
        if (!candidates.length) return false;

        // span 안에 글자가 있는 경우에도 실제 탭/버튼 조상을 눌러야 한다.
        const target = candidates[0].closest(
            'a, button, [role="tab"], [role="button"], label'
        ) || candidates[0];
        target.scrollIntoView({block: 'center'});
        target.click();
        return true;
        """,
        scope_name,
    )
    if not clicked:
        raise RuntimeError(f"'{scope_name}' 탭을 찾지 못했습니다.")

    # 탭 전환 뒤 Ajax로 교체되는 테이블이 채워질 시간을 보장한다.
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, "//table//td[normalize-space()]"))
    )
    time.sleep(2)


def select_financial_frequency(driver, frequency_code, frequency_name):
    """손익·재무상태·현금흐름표의 연간/분기 버튼을 모두 전환한다."""
    button_count = driver.execute_script(
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
    if button_count != 3:
        raise RuntimeError(
            f"'{frequency_name}' 버튼을 3개 모두 찾지 못했습니다. (발견: {button_count}개)"
        )

    # 세 표의 Ajax 데이터가 교체되고, 버튼 선택 상태가 반영될 때까지 기다린다.
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
    time.sleep(2)


def chrome_service():
    """이미 내려받은 ChromeDriver가 있으면 그것을 우선 사용한다.

    Windows에서는 webdriver-manager가 캐시된 exe에 chmod를 시도하면서 권한
    오류를 낼 수 있다. 캐시가 있으면 경로를 Service에 직접 전달하면 된다.
    """
    cached_drivers = list(
        Path.home().glob(
            ".wdm/drivers/chromedriver/win64/*/chromedriver-win64/chromedriver.exe"
        )
    )
    if cached_drivers:
        latest_driver = max(cached_drivers, key=lambda path: path.stat().st_mtime)
        return Service(str(latest_driver))
    return Service(ChromeDriverManager().install())


options = webdriver.ChromeOptions()
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--window-size=1920,1080")
options.add_argument(
    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

print("가상 브라우저를 구동하는 중입니다...")
driver = webdriver.Chrome(service=chrome_service(), options=options)

try:
    target_url = (
        "https://wcomp.fnguide.com/CompanyInfo/Finance?"
        "c_id=AA&menu_type=01&cmp_cd=005930"
#        "https://wcomp.fnguide.com/CompanyInfo/FinanceRatio?"
#        "c_id=AA&menu_type=01&cmp_cd=005930"
    )
    driver.get(target_url)

    print("자바스크립트 재무 데이터가 표에 완전히 채워질 때까지 대기 중...")
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, "//table//td[normalize-space()]"))
    )
    time.sleep(2)

    # 오프라인 HTML에서는 '연결/별도', '연간/분기' 탭의 Ajax 요청을 다시 실행할 수 없다.
    # 따라서 웹페이지에서 두 종류의 탭을 직접 누른 상태를 각각 저장한다.
    scraped_tables = []
    for scope_name in ("연결", "별도"):
        print(f"'{scope_name}' 탭으로 전환하는 중...")
        select_financial_scope(driver, scope_name)

        for frequency_code, frequency_name in (("Y", "년간"), ("Q", "분기")):
            print(f"  '{frequency_name}' 표로 전환하는 중...")
            select_financial_frequency(driver, frequency_code, frequency_name)

            print(f"  '{scope_name} {frequency_name}' 표의 모든 '+' 상세 항목을 펼치는 중...")
            click_count = expand_all_table_details(driver)
            print(f"    총 {click_count}개의 '+' 항목을 펼쳤습니다.")
            time.sleep(1)

            # page_source는 클릭 후 DOM 상태를 가져오므로, 저장본에는 펼쳐진 하위 행이 포함된다.
            complete_html = driver.page_source
            html_filename = f"005930_{scope_name}__{frequency_name}_전체펼침.html"
            with open(html_filename, "w", encoding="utf-8") as file:
                file.write(complete_html)
            print(f"    펼쳐진 데이터를 포함한 HTML을 '{html_filename}'로 저장했습니다!")

            dataframes = read_data_tables(complete_html)
            print(f"    총 {len(dataframes)}개의 테이블을 읽었습니다.")
            scraped_tables.append((scope_name, frequency_name, dataframes))

    output_filename = "삼성전자_wcomp_재무제표_연결_별도_년간_분기_전체펼침.xlsx"
    with pd.ExcelWriter(output_filename, engine="openpyxl") as writer:
        saved_sheets = 0
        for scope_name, frequency_name, dataframes in scraped_tables:
            for index, dataframe in enumerate(dataframes, start=1):
                if dataframe.empty or len(dataframe.columns) < 2:
                    continue
                dataframe.to_excel(
                    writer,
                    sheet_name=f"{scope_name}_{frequency_name}_{index}",
                    index=False,
                )
                saved_sheets += 1

    print(f"'{output_filename}'에 {saved_sheets}개 테이블 저장이 완료되었습니다!")

except Exception as error:
    print(f"데이터 추출 실패: {error}")

finally:
    driver.quit()
