# -*- coding: utf-8 -*-
import re

# 기존에 잘 되던 분기별자사주 모듈 안전 호출 (에러 발생 시 무시하고 0 반환)
try:
    import 분기별주식수
except ImportError:
    분기별주식수 = None

def parse_val(val) -> float:
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
    - 연간 및 분기(TTM) 현금흐름표(CFO - CAPEX) 기반 주주수익 산출
    - 현재 주가 연동 FCF PER(주주수익 배수) 계산
    """
    if not isinstance(parsed, dict):
        if debug:
            print("❌ [Owner Earnings] parsed가 딕셔너리가 아닙니다.")
        return None, None, None

    if debug:
        print(f"\n================ [Owner Earnings 시작: mode={mode}, current_price={current_price}] ================")

    # 1. 현금흐름표 데이터 탐색
    cf_root = parsed.get("현금흐름표", {})
    if not cf_root:
        cf_root = parsed.get("cash", {})
    if not cf_root and "parsed_data" in parsed:
        cf_root = parsed.get("parsed_data", {}).get("현금흐름표", {})

    cash_flow_data = {}
    if mode == "annual":
        for k in ["annual", "연간", "year", "y"]:
            if k in cf_root and isinstance(cf_root[k], dict) and cf_root[k]:
                cash_flow_data = cf_root[k]
                break
        if not cash_flow_data and isinstance(cf_root, dict):
            cash_flow_data = cf_root
    else:
        for k in ["quarterly", "분기", "quarter", "q"]:
            if k in cf_root and isinstance(cf_root[k], dict) and cf_root[k]:
                cash_flow_data = cf_root[k]
                break
        if not cash_flow_data and isinstance(cf_root, dict):
            cash_flow_data = cf_root

    if not cash_flow_data or not isinstance(cash_flow_data, dict):
        if debug:
            print(f"❌ [Owner Earnings] '{mode}'에 해당하는 현금흐름표 데이터를 찾지 못했습니다.")
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
                    if debug:
                        print(f"  ✅ [CFO 매칭] 계정: {acc} | 기간: {d} | 값: {val:,.2f}")
                
                # CAPEX 매칭
                elif (
                    ("취득" in acc_clean or "증가" in acc_clean or "지출" in acc_clean) and 
                    ("유형자산" in acc_clean or "설비" in acc_clean or "투자자산" in acc_clean or "무형자산" in acc_clean)
                ) or "CAPEX" in acc_clean or "자본적지출" in acc_clean:
                    sub_capex += abs(val)
                    if debug:
                        print(f"  ✅ [CAPEX 매칭] 계정: {acc} | 기간: {d} | 원본값: {val:,.2f} -> 변환액(절대값): {abs(val):,.2f}")

            if sub_cfo != 0:
                cfo_total = sub_cfo
            if sub_capex != 0:
                capex_total = sub_capex

    if debug:
        print(f"  📊 [합산 결과] 총 CFO: {cfo_total:,.2f}억원 | 총 CAPEX: {capex_total:,.2f}억원")

    # 기업 전체 주주수익 총액 (원화 환산)
    total_owner_earnings_억원 = cfo_total - capex_total
    total_owner_earnings_원 = total_owner_earnings_억원 * 100_000_000

    if debug:
        print(f"  💰 [주주수익 총액] {total_owner_earnings_억원:,.2f}억원 ({total_owner_earnings_원:,.0f}원)")

    # 3. 발행주식수 탐색 (기존 분기별자사주 모듈 안전 활용)
    shares = 0.0
    shares_source = "None"
    try:
        if mode == "quarterly" and 분기별주식수 and hasattr(분기별주식수, "get_quarterly_shares"):
            shares_dict = 분기별주식수.get_quarterly_shares(parsed, debug=False)
            if shares_dict and isinstance(shares_dict, dict):
                sorted_q_keys = sorted(shares_dict.keys())
                if sorted_q_keys:
                    latest_q = sorted_q_keys[-1]
                    shares = shares_dict[latest_q]
                    shares_source = f"분기별주식수 모듈 ({latest_q})"
        
        if shares <= 0:
            snp_annual = parsed.get("snapshot", {}).get("annual", {})
            if not snp_annual:
                snp_annual = parsed.get("parsed_data", {}).get("snapshot", {}).get("annual", {})
            
            for acc, vals in snp_annual.items():
                if "발행주식수" in str(acc) or "보통주주식수" in str(acc):
                    if isinstance(vals, dict) and vals:
                        valid_shares = [(d, parse_val(v)) for d, v in vals.items() if v is not None and "(E)" not in str(d)]
                        if valid_shares:
                            latest_s_date, shares = valid_shares[-1]
                            shares_source = f"연간 Snapshot ({latest_s_date})"
                    break
    except Exception as e:
        if debug:
            print(f"  ❌ [주주수익 주식수 탐색 예외 발생]: {e}")

    if shares <= 0:
        shares = 1.0  # 0 나누기 방지
        shares_source = "기본값 대체 (1.0주)"

    if debug:
        print(f"  📈 [발행주식수 선택] 수량: {shares:,.0f}주 (출처: {shares_source})")

    # 4. 주당 주주수익 계산
    per_share_owner_earnings = total_owner_earnings_원 / shares

    # 5. FCF PER (주주수익 배수) 계산: 현재주가 ÷ 주당 주주수익
    fcf_per = None
    if current_price and per_share_owner_earnings > 0:
        fcf_per = round(current_price / per_share_owner_earnings, 2)

    if debug:
        print(f"  🎯 [최종 산출] 주당 주주수익: {per_share_owner_earnings:,.2f}원 | 현재가: {current_price:,.0f}원 -> FCF PER: {fcf_per}배 (기준일: {latest_date})")
        print(f"========================================================================\n")

    return per_share_owner_earnings, fcf_per, latest_date
