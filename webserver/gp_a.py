# -*- coding: utf-8 -*-
import re

def calculate_gp_a_ratio(parsed_data: dict, mode: str = "annual", debug: bool = False) -> float | None:
    """
    GP/A (매출총이익 / 총자산) 비율 계산
    - 선택된 칼럼 모드(annual, quarterly, consensus)에 해당하는 데이터만 일관되게 참조합니다.
    """
    company_name = parsed_data.get("name", "미상") if isinstance(parsed_data, dict) else "미상"

    if debug:
        print("\n" + "=" * 80)
        print(f"🔍 [GP/A 계산 로그] 대상 기업: {company_name} (선택 모드/칼럼: {mode})")
        print("=" * 80)

    if not isinstance(parsed_data, dict):
        return None

    tables = parsed_data.get("tables", parsed_data)
    
    # 모드에 따른 데이터 키 매핑 (annual, quarterly, consensus)
    freq_key = "quarterly" if mode == "quarterly" else "annual"
    
    fin_income = tables.get("포괄손익계산서", {}).get(freq_key, {})
    fin_balance = tables.get("재무상태표", {}).get(freq_key, {})

    if not fin_income or not fin_balance:
        return None

    def find_account_dict(data_dict: dict, keywords: list[str]):
        for acc, vals in data_dict.items():
            acc_clean = re.sub(r"\s+", "", str(acc))
            for kw in keywords:
                if kw in acc_clean:
                    if isinstance(vals, dict):
                        return acc, vals
        return None, {}

    gp_acc_name, gp_vals = find_account_dict(fin_income, ["매출총이익", "영업이익"])
    ta_acc_name, ta_vals = find_account_dict(fin_balance, ["자산총계", "총자산"])

    if not gp_vals or not ta_vals:
        return None

    gross_profit = None
    total_assets = None
    used_col = None

    # 1. 분기 모드: 2026/06 최근분기 포함 과거 4개 분기(TTM) 합산
    if mode == "quarterly":
        quarter_candidates = {}
        for col_str, val in gp_vals.items():
            col_str_clean = str(col_str).strip()
            if any(exc in col_str_clean for exc in ["전년동기대비", "전년동기", "%", "증감"]):
                continue
            if "(E)" in col_str_clean:
                continue
            
            match = re.search(r'(\d{4}[/\.]\d{2})', col_str_clean)
            if match:
                q_key = match.group(1).replace('.', '/')
                if val is not None and str(val).strip() not in ["", "-", "N/A", "nan"]:
                    if q_key not in quarter_candidates or "최근분기" in col_str_clean:
                        quarter_candidates[q_key] = (col_str_clean, val)

        sorted_q_keys = sorted(quarter_candidates.keys())
        if len(sorted_q_keys) >= 4:
            target_q_keys = sorted_q_keys[-4:]
            gp_sum = 0.0
            used_cols = []
            for qk in target_q_keys:
                col_key, raw_val = quarter_candidates[qk]
                used_cols.append(col_key)
                try:
                    clean_v = re.sub(r"[^\d.-]", "", str(raw_val))
                    gp_sum += float(clean_v)
                except (ValueError, TypeError):
                    pass
            gross_profit = gp_sum
            used_col = f"분기 TTM ({used_cols})"

        # 자산총계는 가장 최근 분기 잔액 참조
        ta_dates = []
        for d, v in ta_vals.items():
            d_str = str(d).strip()
            if not any(exc in d_str for exc in ["전년동기대비", "전년동기", "%", "증감"]):
                match = re.search(r'(\d{4}[/\.]\d{2})', d_str)
                if match and v is not None and str(v).strip() not in ["", "-", "N/A", "nan"]:
                    ta_dates.append((match.group(1), d_str))
        if ta_dates:
            ta_dates.sort(key=lambda x: x[0])
            ta_col = ta_dates[-1][1]
            try:
                total_assets = float(re.sub(r"[^\d.-]", "", str(ta_vals[ta_col])))
            except (ValueError, TypeError):
                pass

    # 2. 연간 또는 컨센서스 모드: 선택된 모드의 최신 칼럼 데이터 단독 참조
    else:
        valid_dates = []
        for d, v in gp_vals.items():
            d_str = str(d).strip()
            if any(exc in d_str for exc in ["전년동기대비", "전년동기", "%", "증감"]):
                continue
            if v is not None and str(v).strip() not in ["", "-", "N/A", "nan"]:
                if mode == "annual" and ("(E)" in d_str or "(P)" in d_str):
                    continue
                match = re.search(r'(\d{4}[/\.]\d{2})', d_str)
                if match:
                    valid_dates.append((match.group(1), d_str))

        if valid_dates:
            valid_dates.sort(key=lambda x: x[0])
            _, latest_col = valid_dates[-1]
            try:
                gross_profit = float(re.sub(r"[^\d.-]", "", str(gp_vals[latest_col])))
                used_col = latest_col
            except (ValueError, TypeError):
                pass

        # 자산총계도 동일한 기준의 최신 칼럼 참조
        ta_dates = []
        for d, v in ta_vals.items():
            d_str = str(d).strip()
            if not any(exc in d_str for exc in ["전년동기대비", "전년동기", "%", "증감"]):
                if mode == "annual" and ("(E)" in d_str or "(P)" in d_str):
                    continue
                match = re.search(r'(\d{4}[/\.]\d{2})', d_str)
                if match and v is not None and str(v).strip() not in ["", "-", "N/A", "nan"]:
                    ta_dates.append((match.group(1), d_str))
        if ta_dates:
            ta_dates.sort(key=lambda x: x[0])
            _, ta_col = ta_dates[-1]
            try:
                total_assets = float(re.sub(r"[^\d.-]", "", str(ta_vals[ta_col])))
            except (ValueError, TypeError):
                pass

    if debug:
        print(f"  [GP/A 모드 일관성 매칭] 모드: {mode} | 사용된 칼럼: {used_col}")
        print(f"  - 매출총이익: {gross_profit} | 자산총계: {total_assets}")

    if gross_profit is not None and total_assets and total_assets > 0:
        return round((gross_profit / total_assets) * 100, 2)
    
    return None