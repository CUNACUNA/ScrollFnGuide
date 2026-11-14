# -*- coding: utf-8 -*-
import re
import 분기별자사주

def parse_val(val) -> float:
    """값이 비어있거나 특수문자일 경우 0.0으로 처리하고 숫자를 파싱"""
    if val is None:
        return 0.0
    v_str = str(val).strip()
    if v_str in ["", "-", "N/A", "nan", "NaN"]:
        return 0.0
    clean = re.sub(r"[^\d.-]", "", v_str)
    try:
        return float(clean) if clean else 0.0
    except ValueError:
        return 0.0

def calculate_owner_earnings(parsed: dict, current_price: float = None, mode="annual", debug: bool = False) -> tuple[float | None, float | None, str | None]:
    """
    [주당 주주수익 및 FCF PER 계산 모듈]
    - 반환값: (주당 주주수익, FCF PER, 기준일자)
    - 공식 1 (주당 주주수익) = [(영업활동현금흐름 총액(억) - CAPEX 총액(억)) * 100,000,000] ÷ 발행주식수
    - 공식 2 (FCF PER) = 현재주가 ÷ 주당 주주수익
    """
    if not isinstance(parsed, dict):
        return None, None, None

    freq_key = "annual" if mode == "annual" else "quarterly"
    
    # 1. 현금흐름표 데이터 탐색
    cash_flow_data = parsed.get("현금흐름표", {}).get(freq_key, {})
    if not cash_flow_data:
        cash_flow_data = parsed.get("cash", {}).get(freq_key, {})

    if not cash_flow_data or not isinstance(cash_flow_data, dict):
        return None, None, None

    cfo_total = 0.0
    capex_total = 0.0
    latest_date = None

    # 2. 현금흐름표 계정 탐색 및 합산 (CFO, CAPEX)
    for acc, vals in cash_flow_data.items():
        acc_clean = re.sub(r"\s+", "", str(acc))
        if isinstance(vals, dict) and vals:
            sorted_dates = sorted(vals.keys())
            if not sorted_dates:
                continue

            if mode == "annual":
                dec_dates = [d for d in sorted_dates if "12" in str(d) or not ("03" in str(d) or "06" in str(d) or "09" in str(d))]
                target_dates = [dec_dates[-1]] if dec_dates else [sorted_dates[-1]]
            else:
                target_dates = sorted_dates[-4:]
            
            latest_date = target_dates[-1]

            sub_cfo = 0.0
            sub_capex = 0.0
            for d in target_dates:
                val = parse_val(vals.get(d))
                
                # CFO 매칭
                if "영업활동으로인한현금흐름" in acc_clean or ("영업활동" in acc_clean and "현금흐름" in acc_clean and "변동" not in acc_clean):
                    sub_cfo += val
                
                # CAPEX 매칭
                elif (
                    ("취득" in acc_clean or "증가" in acc_clean or "지출" in acc_clean) and 
                    ("유형자산" in acc_clean or "설비" in acc_clean or "투자자산" in acc_clean or "무형자산" in acc_clean)
                ) or "CAPEX" in acc_clean or "자본적지출" in acc_clean:
                    sub_capex += abs(val)

            if sub_cfo != 0:
                cfo_total = sub_cfo
            if sub_capex != 0:
                capex_total = sub_capex

    # 기업 전체 주주수익 총액 (원화 환산)
    total_owner_earnings_원 = (cfo_total - capex_total) * 100_000_000

    # 3. 발행주식수 탐색
    shares = 0.0
    if mode == "quarterly":
        shares_dict = 분기별자사주.get_quarterly_shares(parsed, debug=False)
        if shares_dict:
            sorted_q_keys = sorted(shares_dict.keys())
            shares = shares_dict[sorted_q_keys[-1]]
    else:
        snp_annual = parsed.get("snapshot", {}).get("annual", {})
        if not snp_annual:
            snp_annual = parsed.get("parsed_data", {}).get("snapshot", {}).get("annual", {})
        
        for acc, vals in snp_annual.items():
            if "발행주식수" in str(acc) or "보통주주식수" in str(acc):
                if isinstance(vals, dict) and vals:
                    valid_shares = [parse_val(v) for d, v in vals.items() if v is not None and "(E)" not in str(d)]
                    if valid_shares:
                        shares = valid_shares[-1]
                break

    # 4. 주당 주주수익 계산
    per_share_owner_earnings = 0.0
    if shares > 0:
        per_share_owner_earnings = total_owner_earnings_원 / shares

    # 5. FCF PER (주주수익 배수) 계산: 현재주가 ÷ 주당 주주수익
    fcf_per = None
    if current_price and per_share_owner_earnings > 0:
        fcf_per = round(current_price / per_share_owner_earnings, 2)

    if debug:
        print(f"🎯 [주주수익 분석] 주당 주주수익: {per_share_owner_earnings:,.2f}원 | 현재가: {current_price}원 -> FCF PER: {fcf_per}배")

    return per_share_owner_earnings, fcf_per, latest_date