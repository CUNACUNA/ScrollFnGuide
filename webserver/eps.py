# -*- coding: utf-8 -*-
import re

def parse_val(val) -> float:
    """값이 None이거나 비어있음, '-', 'N/A', 'nan'인 경우 0.0으로 간주하고 숫자를 파싱"""
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

def calculate_eps(parsed_data: dict, mode: str = "annual", debug: bool = False) -> tuple[float | None, str | None]:
    """
    Snapshot 테이블에서 EPS를 가져온다.
    - annual: (E)나 (P)가 없는 확정 연도 컬럼 중 가장 최신 데이터
    - quarterly: 최근 분기 포함 과거 4개 분기(TTM) EPS 합산 ((E)/(P) 차단 및 0원 인정)
    - consensus: 컨센서스(추정치) EPS
    """
    if not isinstance(parsed_data, dict):
        return None, None

    tables = parsed_data.get("tables", parsed_data)
    is_quarterly = (mode == "quarterly")
    freq_key = "quarterly" if is_quarterly else "annual"
    
    snp_data = tables.get("snapshot", {}).get(freq_key, {})
    if not snp_data:
        snp_data = parsed_data.get("snapshot", {}).get(freq_key, {})

    def find_account_dict(data_dict: dict, keywords: list[str]):
        for acc, vals in data_dict.items():
            acc_clean = re.sub(r"\s+", "", str(acc))
            for kw in keywords:
                if kw in acc_clean:
                    if isinstance(vals, dict):
                        return acc, vals
        return None, {}

    acc_name, vals = find_account_dict(snp_data, ["EPS", "수정EPS"])
    if not vals:
        return None, None

    if mode == "quarterly":
        # TTM sum of last 4 quarters (확정 분기 기준, 빈 값은 0으로 처리)
        quarter_candidates = {}
        for col_str, val in vals.items():
            col_str_clean = str(col_str).strip()
            upper_d = col_str_clean.upper()
            
            # 🛑 [핵심 수정] (E) 또는 (P) 마크가 포함된 컬럼은 엄격하게 배제
            if "(E)" in upper_d or "(P)" in upper_d:
                continue
            
            match = re.search(r'(\d{4})[/\.](\d{2})', col_str_clean)
            if match:
                year = int(match.group(1))
                month = int(match.group(2))
                q_key = f"{year:04d}/{month:02d}"
                
                # 물리적 미래 분기 원천 차단 (2026년 6월 이후 확정될 수 없는 데이터 방어)
                if year > 2026 or (year == 2026 and month > 6):
                    continue

                num_val = parse_val(val)  # 빈 값은 0.0으로 변환되어 정상 집계
                if num_val >= 0:
                    if q_key not in quarter_candidates or "최근분기" in col_str_clean:
                        quarter_candidates[q_key] = (col_str_clean, num_val)

        sorted_q_keys = sorted(quarter_candidates.keys())
        if len(sorted_q_keys) >= 4:
            target_q_keys = sorted_q_keys[-4:]
            
            used_details = []
            eps_sum = 0.0
            for qk in target_q_keys:
                col_name, val_num = quarter_candidates[qk]
                eps_sum += val_num
                used_details.append(f"{col_name}: {val_num}")

            if debug:
                print(f"[EPS TTM Detail] 사용된 4개 분기 및 값: {used_details} | 총 합산 EPS: {eps_sum}")
            return round(eps_sum, 2), target_q_keys[-1]
        else:
            if debug:
                print(f"[EPS TTM Skip] 유효 확정 분기 4개 미만 (발견된 분기 수: {len(sorted_q_keys)})")
        return None, None

    elif mode == "consensus":
        consensus_dates = [d for d in vals.keys() if "(E)" in d or "(P)" in d]
        actual_dates = [d for d in vals.keys() if "(E)" not in d and "(P)" not in d]
        target_date = sorted(consensus_dates, reverse=True)[0] if consensus_dates else (sorted(actual_dates, reverse=True)[0] if actual_dates else None)
        if target_date:
            val = parse_val(vals[target_date])
            if debug:
                print(f"[EPS Consensus] 기준일자: {target_date}, EPS: {val}")
            return val, target_date
        return None, None

    else: # annual (확정 연도별)
        valid_dates = []
        for d, v in vals.items():
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
            _, target_date, target_eps = valid_dates[-1]
            if debug:
                print(f"[EPS Annual] 직전 확정 연도: {target_date}, EPS: {target_eps}")
            return target_eps, target_date
        return None, None