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

def calculate_roe(parsed_data: dict, mode: str = "annual", debug: bool = False) -> tuple[float | None, str | None]:
    """
    [정석 ROE 계산 모듈]
    - annual: (E)/(P)가 없는 직전 연도 확정 연간 ROE (스냅샷 또는 재무제표 기반)
    - quarterly: 최근 4개 분기 순이익 TTM 합계 ÷ 가장 최근 분기 자본총계 × 100
    - consensus: 컨센서스(추정치) ROE
    """
    if not isinstance(parsed_data, dict):
        return None, None

    tables = parsed_data.get("tables", parsed_data)
    is_quarterly = (mode == "quarterly")
    freq_key = "quarterly" if is_quarterly else "annual"
    
    # 🛡️ [방어 코드] tables 및 snapshot 데이터 타입 검증
    if not isinstance(tables, dict):
        tables = {}

    snapshot_root = tables.get("snapshot", {})
    if not isinstance(snapshot_root, dict):
        snapshot_root = parsed_data.get("snapshot", {})
    if not isinstance(snapshot_root, dict):
        snapshot_root = {}

    snp_data = snapshot_root.get(freq_key, {})
    if not isinstance(snp_data, dict):
        snp_data = {}

    def find_account_dict(data_dict: dict, keywords: list[str]):
        # 🛡️ [핵심 방어 코드] data_dict가 dict가 아닐 경우 에러 방지
        if not isinstance(data_dict, dict):
            return None, {}
        for acc, vals in data_dict.items():
            acc_clean = re.sub(r"\s+", "", str(acc))
            for kw in keywords:
                if kw in acc_clean:
                    if isinstance(vals, dict):
                        return acc, vals
        return None, {}

    if mode == "quarterly":
        # 1. 포괄손익계산서에서 최근 4개 분기 당기순이익(또는 지배주주순이익) TTM 합산
        fin_income = tables.get("포괄손익계산서", {}).get("quarterly", {})
        _, income_vals = find_account_dict(fin_income, ["지배주주순이익", "당기순이익"])
        
        quarter_net_income = {}
        if income_vals:
            for col_str, val in income_vals.items():
                col_str_clean = str(col_str).strip()
                upper_d = col_str_clean.upper()
                
                # (E), (P) 마크 및 미래 분기 원천 차단
                if "(E)" in upper_d or "(P)" in upper_d:
                    continue
                
                match = re.search(r'(\d{4})[/\.](\d{2})', col_str_clean)
                if match:
                    year = int(match.group(1))
                    month = int(match.group(2))
                    q_key = f"{year:04d}/{month:02d}"
                    
                    if year > 2026 or (year == 2026 and month > 6):
                        continue
                    
                    net_val = parse_val(val)
                    quarter_net_income[q_key] = (col_str_clean, net_val)

        sorted_q_keys = sorted(quarter_net_income.keys())
        if len(sorted_q_keys) < 4:
            if debug:
                print(f"[ROE Quarterly Skip] 유효 순이익 분기 4개 미만 (발견된 수: {len(sorted_q_keys)})")
            return None, None

        target_q_keys = sorted_q_keys[-4:]
        net_income_details = []
        total_net_income = 0.0
        for qk in target_q_keys:
            col_name, val_num = quarter_net_income[qk]
            total_net_income += val_num
            net_income_details.append(f"{col_name}: {val_num}")

        # 2. 재무상태표에서 가장 최근 분기 자본총계(또는 지배주주지분) 가져오기
        fin_balance = tables.get("재무상태표", {}).get("quarterly", {})
        _, equity_vals = find_account_dict(fin_balance, ["자본총계", "지배주주지분"])
        
        latest_equity = None
        equity_col_name = None
        if equity_vals:
            equity_candidates = {}
            for col_str, val in equity_vals.items():
                col_str_clean = str(col_str).strip()
                upper_d = col_str_clean.upper()
                if "(E)" in upper_d or "(P)" in upper_d:
                    continue
                match = re.search(r'(\d{4})[/\.](\d{2})', col_str_clean)
                if match:
                    year = int(match.group(1))
                    month = int(match.group(2))
                    if year > 2026 or (year == 2026 and month > 6):
                        continue
                    equity_candidates[f"{year:04d}/{month:02d}"] = (col_str_clean, parse_val(val))
            
            if equity_candidates:
                latest_eq_key = sorted(equity_candidates.keys())[-1]
                equity_col_name, latest_equity = equity_candidates[latest_eq_key]

        if not latest_equity or latest_equity <= 0:
            if debug:
                print(f"[ROE Quarterly Skip] 유효한 자본총계 데이터를 찾을 수 없습니다.")
            return None, None

        # 3. 정석 TTM ROE 산식 적용: (순이익 TTM 합계 ÷ 자본총계) × 100
        roe_val = round((total_net_income / latest_equity) * 100, 2)
        target_date = target_q_keys[-1]

        if debug:
            print(f"[ROE Quarterly TTM Detail]")
            print(f"  - 사용된 순이익 4개 분기: {net_income_details} | 순이익 합계: {total_net_income}")
            print(f"  - 기준 자본총계 분기: {equity_col_name} | 자본총계: {latest_equity}")
            print(f"  - [계산 공식] ({total_net_income} / {latest_equity}) * 100 = {roe_val}%")

        return roe_val, target_date

    elif mode == "consensus":
        _, vals = find_account_dict(snp_data, ["ROE"])
        if not vals:
            return None, None
        consensus_dates = [d for d, v in vals.items() if v is not None and str(d).strip() not in ["", "-"] and ("(E)" in d or "(P)" in d)]
        actual_dates = [d for d, v in vals.items() if v is not None and str(d).strip() not in ["", "-"] and "(E)" not in d and "(P)" not in d]
        target_date = sorted(consensus_dates, reverse=True)[0] if consensus_dates else (sorted(actual_dates, reverse=True)[0] if actual_dates else None)
        if target_date:
            try:
                val = float(re.sub(r"[^\d.-]", "", str(vals[target_date])))
                if debug:
                    print(f"[ROE Consensus] 기준일자: {target_date}, ROE: {val}%")
                return val, target_date
            except (ValueError, TypeError):
                pass
        return None, None

    else: # annual (확정 연도별)
        _, vals = find_account_dict(snp_data, ["ROE"])
        if not vals:
            return None, None
            
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
                if v is not None and str(v).strip() not in ["", "-", "N/A", "nan"]:
                    valid_dates.append((str(year_val), d_str))

        if valid_dates:
            valid_dates.sort(key=lambda x: x[0])
            _, target_date = valid_dates[-1]
            try:
                val = float(re.sub(r"[^\d.-]", "", str(vals[target_date])))
                if debug:
                    print(f"[ROE Annual] 직전 확정 연도: {target_date}, ROE: {val}%")
                return val, target_date
            except (ValueError, TypeError):
                pass
        return None, None