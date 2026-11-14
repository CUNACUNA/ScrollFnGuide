# -*- coding: utf-8 -*-
import re

def parse_val(val) -> float:
    """
    값이 None이거나 비어있음, '-', 'N/A', 'nan'인 경우 0.0으로 간주하고,
    숫자 형태의 문자열이 있으면 정밀하게 파싱합니다.
    """
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

def calculate_dividend_yield(parsed: dict, current_price: float = None, mode: str = "annual", debug: bool = False) -> float | None:
    """
    [배당수익률 계산 모듈]
    - annual: (E)나 (P)가 없는 직전 연도 확정 DPS 기준 배당수익률 (0원 배당 포함 인정)
    - quarterly: 최근 확정된 4개 분기 TTM DPS 합산 기준 ((E)/(P) 마크 엄격 차단)
    - consensus: 추정치(E)가 포함된 연도/분기 기준 배당수익률
    """
    if not isinstance(parsed, dict) or not current_price or current_price <= 0:
        return None

    tables = parsed.get("tables", parsed)
    is_quarterly = (mode == "quarterly")
    freq_key = "quarterly" if is_quarterly else "annual"
    
    snp_data = tables.get("snapshot", {}).get(freq_key, {})
    if not snp_data:
        snp_data = parsed.get("snapshot", {}).get(freq_key, {})

    # 현금DPS 계정 찾기
    dps_key = None
    for acc_name in snp_data.keys():
        clean_name = str(acc_name).replace(" ", "")
        if "현금DPS" in clean_name or "DPS" in clean_name:
            dps_key = acc_name
            break

    if not dps_key:
        if debug:
            print(f"[Dividend] 현금DPS 계정을 찾지 못했습니다.")
        return None

    dps_vals = snp_data[dps_key]
    if not isinstance(dps_vals, dict):
        return None

    target_dps = None

    if is_quarterly:
        # 분기별 TTM (최근 4개 분기 확정 DPS 합산)
        quarter_candidates = {}
        for col_str, val in dps_vals.items():
            col_str_clean = str(col_str).strip()
            upper_d = col_str_clean.upper()
            
            # (E) 또는 (P) 마크가 포함된 경우 엄격하게 배제
            if "(E)" in upper_d or "(P)" in upper_d:
                continue
            
            # 🛑 [핵심 수정] 연도와 월을 각각 정확히 분리해서 매칭
            match = re.search(r'(\d{4})[/\.](\d{2})', col_str_clean)
            if match:
                year = int(match.group(1))
                month = int(match.group(2))
                q_key = f"{year:04d}/{month:02d}"
                
                # 물리적 미래 분기 원천 차단 (예: 2026년 6월 이후 데이터는 확정치가 될 수 없음)
                if year > 2026 or (year == 2026 and month > 6):
                    continue

                num_val = parse_val(val)
                if num_val >= 0:
                    if q_key not in quarter_candidates or "최근분기" in col_str_clean:
                        quarter_candidates[q_key] = (col_str_clean, num_val)

        sorted_q_keys = sorted(quarter_candidates.keys())
        if len(sorted_q_keys) >= 4:
            # 과거 순서대로 정렬된 키 중 가장 최근 4개 분기 선택
            target_q_keys = sorted_q_keys[-4:]
            
            used_details = []
            target_dps = 0.0
            for qk in target_q_keys:
                col_name, val_num = quarter_candidates[qk]
                target_dps += val_num
                used_details.append(f"{col_name}: {val_num}")

            if debug:
                print(f"[Dividend TTM Detail] 사용된 4개 분기 및 값: {used_details} | 총 합산 DPS: {target_dps}")
        else:
            if debug:
                print(f"[Dividend TTM Skip] 유효 확정 분기 4개 미만 (발견된 분기 수: {len(sorted_q_keys)}, 목록: {sorted_q_keys})")
            return None

    elif mode == "consensus":
        consensus_dates = [d for d in dps_vals.keys() if "(E)" in d or "(P)" in d]
        actual_dates = [d for d in dps_vals.keys() if "(E)" not in d and "(P)" not in d]
        target_date = sorted(consensus_dates, reverse=True)[0] if consensus_dates else (sorted(actual_dates, reverse=True)[0] if actual_dates else None)
        if target_date:
            target_dps = parse_val(dps_vals[target_date])
            if debug:
                print(f"[Dividend Consensus] 기준일자: {target_date}, DPS: {target_dps}")

    else: # annual (확정 연도별 - 직전 연도)
        valid_dates = []
        for d, v in dps_vals.items():
            d_str = str(d).strip()
            if any(exc in d_str for exc in ["전년동기대비", "전년동기", "%", "증감"]):
                continue
            if "(E)" in d_str or "(P)" in d_str:
                continue
            
            match = re.search(r'(\d{4})', d_str)
            if match:
                year_val = int(match.group(1))
                if year_val >= 2026:
                    continue
                num_val = parse_val(v)
                if num_val >= 0:
                    valid_dates.append((str(year_val), d_str, num_val))

        if valid_dates:
            valid_dates.sort(key=lambda x: x[0])
            _, target_date, target_dps = valid_dates[-1]
            if debug:
                print(f"[Dividend Annual] 확정 연도: {target_date}, DPS: {target_dps}")

    if target_dps is not None:
        dividend_yield = round((target_dps / current_price) * 100, 2)
        if debug:
            print(f"  => [배당수익률 계산 공식] ({target_dps} / {current_price}) * 100 = {dividend_yield}%")
        return dividend_yield

    return None