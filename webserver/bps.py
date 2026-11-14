# -*- coding: utf-8 -*-
import re

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

def calculate_bps(parsed_data: dict, mode: str = "annual", debug: bool = False) -> tuple[float | None, str | None]:
    """
    [정석 BPS 계산 모듈]
    - annual: (E)/(P)가 없는 직전 연도 확정 연간 BPS (스냅샷 기반)
    - quarterly: 가장 최근 분기 BPS
    - consensus: 컨센서스(추정치) BPS
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

    _, vals = find_account_dict(snp_data, ["BPS", "주당순자산가치", "주당순자산"])
    if not vals:
        return None, None

    if mode == "consensus":
        consensus_dates = [d for d, v in vals.items() if v is not None and str(d).strip() not in ["", "-"] and ("(E)" in d or "(P)" in d)]
        actual_dates = [d for d, v in vals.items() if v is not None and str(d).strip() not in ["", "-"] and "(E)" not in d and "(P)" not in d]
        target_date = sorted(consensus_dates, reverse=True)[0] if consensus_dates else (sorted(actual_dates, reverse=True)[0] if actual_dates else None)
        if target_date:
            try:
                val = parse_val(vals[target_date])
                if debug:
                    print(f"[BPS Consensus] 기준일자: {target_date}, BPS: {val}")
                return val, target_date
            except (ValueError, TypeError):
                pass
        return None, None

    else: # annual or quarterly
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
                if not is_quarterly and year_val >= 2026:
                    continue
                if v is not None and str(v).strip() not in ["", "-", "N/A", "nan"]:
                    valid_dates.append((year_val, d_str))

        if valid_dates:
            valid_dates.sort(key=lambda x: x[0])
            _, target_date = valid_dates[-1]
            try:
                val = parse_val(vals[target_date])
                if debug:
                    print(f"[BPS {freq_key}] 기준일자: {target_date}, BPS: {val}")
                return val, target_date
            except (ValueError, TypeError):
                pass
        return None, None