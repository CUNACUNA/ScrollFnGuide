# -*- coding: utf-8 -*-
import re
from pymongo import MongoClient
import bps
import eps
import fscore  # 피오트로스키 F-스코어 모듈
import gp_a    # GP/A (매출총이익 / 총자산) 모듈
import per     # 신규 PER 계산 모듈 임포트
import roe
import 버핏주주수익PER
import 배당수익률
import 상승여력
import 적정주가_RIM
import 주주환원PER
import 주주환원율


def parse_val(val):
    if val is None:
        return 0.0
    clean = re.sub(r"[^\d.-]", "", str(val))
    try:
        return float(clean) if clean else 0.0
    except ValueError:
        return 0.0


def parse_price(val: str | int | float | None) -> float | None:
    if val is None:
        return None
    clean_val = re.sub(r"[^\d.-]", "", str(val))
    try:
        return float(clean_val) if clean_val else None
    except ValueError:
        return None


def generate_indicator_matrix_html(
    freq="annual",
    db_name="fnguide_db",
    stock_col="stock_tables",
    price_col="CURRENT-PRICE",
) -> str:
    client = MongoClient("mongodb://127.0.0.1:27017/")
    try:
        db = client[db_name]
        stock_collection = db[stock_col]
        price_collection = db[price_col]

        price_docs = list(price_collection.find({}))
        price_map = {}
        for p_doc in price_docs:
            code = p_doc.get("code") or p_doc.get("기업코드")
            raw_p = p_doc.get("current_price")
            if code and raw_p:
                price_map[str(code)] = parse_price(raw_p)

        docs = list(stock_collection.find({}))
    finally:
        client.close()

    if not docs:
        return "<div class='alert alert-warning'>stock_tables DB에 저장된 종목 데이터가 없습니다.</div>"

    if freq == "consensus":
        freq_key = "annual"
        freq_title = "🔮 컨센서스 (추정치/잠정치 포함)"
    elif freq == "quarterly":
        freq_key = "quarterly"
        freq_title = "📊 확정 분기별 (과거 실적)"
    else:
        freq_key = "annual"
        freq_title = "📅 확정 연도별 (과거 실적)"

    rows_html = []

    for doc in docs:
        code = str(doc.get("code") or doc.get("_id", ""))
        name = doc.get("name", "")
        parsed = doc.get("parsed_data", {})

        current_price = price_map.get(code)

        # --- [1] EPS 호출 (eps.py API 연동 - 연간 vs 분기 TTM) ---
        eps_val, eps_date = eps.calculate_eps(parsed, mode=freq, debug=False)

        # --- [2] PER 호출 (per.py API 연동) ---
        per_val, per_date = per.calculate_per(
            parsed, current_price=current_price, mode=freq, debug=False
        )

        # --- [3] ROE 호출 (roe.py API 연동 - 연간 확정 vs 분기 TTM) ---
        roe_val, roe_date = roe.calculate_roe(parsed, mode=freq, debug=False)

        # --- [4] 배당수익률 호출 ---
        dividend_yield = 배당수익률.calculate_dividend_yield(
            parsed, current_price=current_price, mode=freq, debug=False
        )

        # --- [5] 총주주환원수익률(분기) 호출 ---
        q_yield = 주주환원율.calculate_quarterly_shareholder_return_rate(
            parsed, current_price=current_price, debug=False
        )

        # 주식수 감소율 호출
        _, reduction_pct = 주주환원PER.calculate_treasury_reduction_pct(
            parsed, mode=freq, debug=False
        )
        reduction_str = f"{reduction_pct:+.2f}%" if reduction_pct is not None else "-"

        # --- 주주환원 PER 호출 ---
        sh_per_val, _ = 주주환원PER.calculate_shareholder_return_per(
            parsed, current_price=current_price, mode=freq, debug=True
        )
        sh_per_str = f"{sh_per_val:.2f}" if sh_per_val is not None else "-"

        # --- 워런 버핏 주주수익 호출 ---
        _, fcf_per_val, _ = 버핏주주수익PER.calculate_owner_earnings(
            doc, current_price=current_price, mode=freq, debug=False
        )
        fcf_per_str = f"{fcf_per_val:.2f}" if fcf_per_val is not None else "-"

        # --- 컨센서스 상승여력 호출 ---
        upside_val, _ = 상승여력.calculate_target_upside(
            doc, current_price=current_price, debug=False
        )
        upside_str = f"{upside_val:.2f}%" if upside_val is not None else "-"

        # --- RIM 적정주가 상승 여력 호출 ---
        bps_val, _ = bps.calculate_bps(parsed, mode=freq, debug=False)
        _, rim_upside_pct = 적정주가_RIM.calculate_rim_from_values(
            bps_val=bps_val,
            roe_val=roe_val,
            current_price=current_price,
            required_return=0.08,
            debug=False,
        )
        rim_upside_pct_str = (
            f"{rim_upside_pct:+.2f}%" if rim_upside_pct is not None else "-"
        )

        # --- [6] GP/A 계산 호출 (매출총이익 / 총자산) ---
        gp_a_val = gp_a.calculate_gp_a_ratio(parsed, mode=freq, debug=False)

        # --- [7] 피오트로스키 F-Score 계산 (0~9점) ---
        f_score_val = fscore.calculate_f_score(parsed, mode=freq, debug=False)

        target_date = per_date or roe_date or eps_date or "-"
        price_str = f"{int(current_price):,}원" if current_price is not None else "N/A"
        eps_str = f"{int(eps_val):,}원" if eps_val is not None else "-"
        per_str = f"{per_val:.2f}" if per_val is not None else "-"
        roe_str = f"{roe_val:.2f}%" if roe_val is not None else "-"
        div_yield_str = f"{dividend_yield:.2f}%" if dividend_yield is not None else "-"
        q_yield_str = f"{q_yield:.2f}%" if q_yield is not None else "-"
        gp_a_str = f"{gp_a_val:.2f}%" if gp_a_val is not None else "0.00%"
        f_score_str = f"{f_score_val} 점" if f_score_val is not None else "-"

        date_badge_cls = (
            "badge badge-info" if "(E)" in target_date or "(P)" in target_date else ""
        )

        rows_html.append(f"""
            <tr>
                <td style="text-align: center; font-weight: bold;">{code}</td>
                <td style="text-align: center;">{name}</td>
                <td style="text-align: center; font-size: 0.9em; color: #6c757d;">
                    <span class="{date_badge_cls}">{target_date}</span>
                </td>
                <td style="text-align: right; font-weight: bold; color: #212529;">{price_str}</td>
                <td style="text-align: right;">{eps_str}</td>
                <td style="text-align: right; color: #0d6efd; font-weight: bold;">{per_str}</td>
                <td style="text-align: right; color: #dc3545; font-weight: bold;">{roe_str}</td>
                <td style="text-align: right; color: #198754; font-weight: bold;">{div_yield_str}</td>
                <td style="text-align: right; color: #6f42c1; font-weight: bold;">{q_yield_str}</td>
                <td style="text-align: right; color: #20c997; font-weight: bold;">{sh_per_str}</td>
                <td style="text-align: right; color: #d63384; font-weight: bold;">{reduction_str}</td>
                <td style="text-align: right; color: #20c997; font-weight: bold;">{fcf_per_str}</td>
                <td style="text-align: right; color: #20c997; font-weight: bold;">{upside_str}</td>
                <td style="text-align: right; color: #20c997; font-weight: bold;">{rim_upside_pct_str}</td>
                <td style="text-align: right; color: #e65c00; font-weight: bold;">{gp_a_str}</td>
                <td style="text-align: center; color: #fd7e14; font-weight: bold;">{f_score_str}</td>
            </tr>
        """)

    table_content = "\n".join(rows_html)
    full_html = f"""
    <div class="table-responsive">
        <h5 class="mb-3 font-weight-bold text-secondary">📌 {freq_title} 재무 지표 및 F-Score 스코어링 비교</h5>
        <table class="matrix-table">
            <thead>
                <tr>
                    <th style="width: 7%;">종목코드</th>
                    <th style="width: 11%;">기업명</th>
                    <th style="width: 9%;">기준일자</th>
                    <th style="width: 10%;">현재가</th>
                    <th style="width: 9%;">EPS</th>
                    <th style="width: 8%;">PER</th>
                    <th style="width: 8%;">ROE</th>
                    <th style="width: 9%;">배당수익률</th>
                    <th style="width: 11%;">순이익주주환원율</th>
                    <th style="width: 11%;">주주환원PER</th>
                    <th style="width: 10%;">자사주감소율(4분기)</th>
                    <th style="width: 11%;">버펏주주수익률</th>
                    <th style="width: 11%;">컨센서스</th>
                    <th style="width: 11%;">적정주가-RIM</th>
                    <th style="width: 9%;">GP/A</th>
                    <th style="width: 9%;">F-Score</th>
                </tr>
            </thead>
            <tbody>
                {table_content}
            </tbody>
        </table>
    </div>
    """
    return full_html