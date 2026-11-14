# -*- coding: utf-8 -*-
import re

def parse_val(val):
    if val is None:
        return 0.0
    clean = re.sub(r"[^\d.-]", "", str(val))
    try:
        return float(clean) if clean else 0.0
    except ValueError:
        return 0.0

def calculate_f_score(parsed: dict, mode: str = "annual", debug: bool = True) -> int:
    """
    [피오트로스키 F-스코어 (0~9점) 계산 및 항목별 디버깅 모듈]
    """
    if not isinstance(parsed, dict):
        return 0

    freq_key = "annual" if mode == "annual" else "quarterly"
    
    fin_income = parsed.get("포괄손익계산서", {}).get(freq_key, {})
    fin_balance = parsed.get("재무상태표", {}).get(freq_key, {})
    fin_cash = parsed.get("현금흐름표", {}).get(freq_key, {})
    snapshot_data = parsed.get("snapshot", {}).get(freq_key, {})
    
    def get_sorted_dates(data_dict):
        if not isinstance(data_dict, dict):
            return []
        all_keys = set()
        for acc_vals in data_dict.values():
            if isinstance(acc_vals, dict):
                for d in acc_vals.keys():
                    d_str = str(d).strip()
                    if any(x in d_str.upper() for x in ["E", "P", "%", "대비"]):
                        continue
                    if mode == "annual":
                        if "/12" in d_str or (len(d_str) == 4 and d_str.isdigit()):
                            all_keys.add(d_str)
                    else:
                        if any(m in d_str for m in ["/03", "/06", "/09", "/12"]):
                            all_keys.add(d_str)
        return sorted(list(all_keys), reverse=True)

    # 데이터 소스 전반에서 날짜 추출 시도
    dates = (
        get_sorted_dates(snapshot_data) or 
        get_sorted_dates(fin_income) or 
        get_sorted_dates(fin_balance) or 
        get_sorted_dates(fin_cash)
    )
    
    if len(dates) < 2:
        if debug:
            print(f"[F-Score Debug] ⚠️ 비교할 기간 데이터가 부족합니다. (확인된 기간 수: {len(dates)})")
        return 0

    current_date = dates[0]
    previous_date = dates[1]

    if debug:
        print(f"\n================ [F-Score 디버깅 상세 내역] ================")
        print(f" - 비교 기준 기간: 당해 [{current_date}] vs 직전 [{previous_date}]")

    def get_val_by_keywords(data_dict, keywords, date_str):
        if not isinstance(data_dict, dict):
            return 0.0
        for acc_name, acc_vals in data_dict.items():
            clean_name = str(acc_name).replace(" ", "").lower()
            for kw in keywords:
                clean_kw = kw.replace(" ", "").lower()
                if clean_kw in clean_name:
                    if isinstance(acc_vals, dict):
                        # 정확히 일치하는 날짜 찾기
                        if date_str in acc_vals:
                            val = parse_val(acc_vals[date_str])
                            if val != 0.0:
                                return val
                        # 부분 일치하는 날짜 키 찾기 (예: '2026/06 (최근분기)' 등 대응)
                        for d_key, d_val in acc_vals.items():
                            if date_str in str(d_key):
                                val = parse_val(d_val)
                                if val != 0.0:
                                    return val
        return 0.0

    score = 0

    # 💡 데이터 누락 방지를 위해 Snapshot 데이터와 재무제표를 복합적으로 탐색
    net_income_curr = (
        get_val_by_keywords(snapshot_data, ["당기순이익(지배)", "당기순이익", "순이익"], current_date) or
        get_val_by_keywords(fin_income, ["지배주주순이익", "당기순이익", "순이익"], current_date)
    )
    net_income_prev = (
        get_val_by_keywords(snapshot_data, ["당기순이익(지배)", "당기순이익", "순이익"], previous_date) or
        get_val_by_keywords(fin_income, ["지배주주순이익", "당기순이익", "순이익"], previous_date)
    )
    
    total_assets_curr = (
        get_val_by_keywords(snapshot_data, ["자산총계", "총자산"], current_date) or
        get_val_by_keywords(fin_balance, ["자산총계", "총자산"], current_date)
    )
    total_assets_prev = (
        get_val_by_keywords(snapshot_data, ["자산총계", "총자산"], previous_date) or
        get_val_by_keywords(fin_balance, ["자산총계", "총자산"], previous_date)
    )
    
    op_cash_curr = get_val_by_keywords(fin_cash, ["영업활동으로인한현금흐름", "영업활동현금흐름", "영업현금흐름", "*영업에서창출된현금흐름"], current_date)

    # 1. 수익성 관련 (4점)
    cond1 = net_income_curr > 0
    if cond1: score += 1

    cond2 = op_cash_curr > 0
    if cond2: score += 1

    roa_curr = (net_income_curr / total_assets_curr) if total_assets_curr > 0 else 0
    roa_prev = (net_income_prev / total_assets_prev) if total_assets_prev > 0 else 0
    cond3 = roa_curr > roa_prev
    if cond3: score += 1

    cond4 = op_cash_curr > net_income_curr
    if cond4: score += 1

    # 2. 레버리지 및 유동성 관련 (3점)
    lt_debt_curr = get_val_by_keywords(fin_balance, ["비유동부채", "장기차입금", "비유동금융부채"], current_date)
    lt_debt_prev = get_val_by_keywords(fin_balance, ["비유동부채", "장기차입금", "비유동금융부채"], previous_date)
    lt_ratio_curr = (lt_debt_curr / total_assets_curr) if total_assets_curr > 0 else 0
    lt_ratio_prev = (lt_debt_prev / total_assets_prev) if total_assets_prev > 0 else 0
    cond5 = lt_ratio_curr < lt_ratio_prev
    if cond5: score += 1

    ca_curr = get_val_by_keywords(fin_balance, ["유동자산"], current_date)
    cl_curr = get_val_by_keywords(fin_balance, ["유동부채"], current_date)
    ca_prev = get_val_by_keywords(fin_balance, ["유동자산"], previous_date)
    cl_prev = get_val_by_keywords(fin_balance, ["유동부채"], previous_date)
    current_ratio_curr = (ca_curr / cl_curr) if cl_curr > 0 else 0
    current_ratio_prev = (ca_prev / cl_prev) if cl_prev > 0 else 0
    cond6 = current_ratio_curr > current_ratio_prev
    if cond6: score += 1

    shares_curr = get_val_by_keywords(snapshot_data, ["발행주식수(보통주)", "발행주식수", "보통주발행주식수"], current_date)
    shares_prev = get_val_by_keywords(snapshot_data, ["발행주식수(보통주)", "발행주식수", "보통주발행주식수"], previous_date)
    if shares_curr > 0 and shares_prev > 0:
        cond7 = shares_curr <= shares_prev
    else:
        cond7 = True  
    if cond7: score += 1

    # 3. 운영 효율성 관련 (2점)
    revenue_curr = (
        get_val_by_keywords(snapshot_data, ["매출액", "수익(매출액)"], current_date) or
        get_val_by_keywords(fin_income, ["매출액", "수익(매출액)"], current_date)
    )
    gp_curr = get_val_by_keywords(fin_income, ["매출총이익", "영업이익"], current_date)
    revenue_prev = (
        get_val_by_keywords(snapshot_data, ["매출액", "수익(매출액)"], previous_date) or
        get_val_by_keywords(fin_income, ["매출액", "수익(매출액)"], previous_date)
    )
    gp_prev = get_val_by_keywords(fin_income, ["매출총이익", "영업이익"], previous_date)
    
    gpm_curr = (gp_curr / revenue_curr) if revenue_curr > 0 else 0
    gpm_prev = (gp_prev / revenue_prev) if revenue_prev > 0 else 0
    cond8 = gpm_curr >= gpm_prev
    if cond8: score += 1

    at_curr = (revenue_curr / total_assets_curr) if total_assets_curr > 0 else 0
    at_prev = (revenue_prev / total_assets_prev) if total_assets_prev > 0 else 0
    cond9 = at_curr >= at_prev
    if cond9: score += 1

    if debug:
        print(f" [1] 순이익 흑자 (>0): {net_income_curr:,.0f}원 -> {'✅ (+1)' if cond1 else '❌ (0)'}")
        print(f" [2] 영업현금흐름 양수 (>0): {op_cash_curr:,.0f}원 -> {'✅ (+1)' if cond2 else '❌ (0)'}")
        print(f" [3] ROA 개선 ({roa_curr:.4f} > {roa_prev:.4f}) -> {'✅ (+1)' if cond3 else '❌ (0)'}")
        print(f" [4] 이익의 질 (영업현금 > 순이익): {op_cash_curr:,.0f} > {net_income_curr:,.0f} -> {'✅ (+1)' if cond4 else '❌ (0)'}")
        print(f" [5] 장기부채비율 감소 ({lt_ratio_curr:.4f} < {lt_ratio_prev:.4f}) -> {'✅ (+1)' if cond5 else '❌ (0)'}")
        print(f" [6] 유동비율 개선 ({current_ratio_curr:.4f} > {current_ratio_prev:.4f}) -> {'✅ (+1)' if cond6 else '❌ (0)'}")
        print(f" [7] 신주발행 부재 ({shares_curr} <= {shares_prev}) -> {'✅ (+1)' if cond7 else '❌ (0)'}")
        print(f" [8] 매출총이익률 개선 ({gpm_curr:.4f} >= {gpm_prev:.4f}) -> {'✅ (+1)' if cond8 else '❌ (0)'}")
        print(f" [9] 자산회전율 개선 ({at_curr:.4f} >= {at_prev:.4f}) -> {'✅ (+1)' if cond9 else '❌ (0)'}")
        print(f" 🎯 최종 F-Score 총점: {score} / 9 점")
        print(f"==========================================================\n")

    return int(score)