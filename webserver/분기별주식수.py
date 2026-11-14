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

def get_quarterly_shares(parsed_data: dict, debug: bool = False) -> dict:
    """
    [분기별 발행주식수 조회 모듈]
    - Snapshot 테이블의 재무요약 항목 중 '발행주식수(보통주)' 데이터를 분기별로 추출합니다.
    - debug=True인 경우, 연/월과 해당 주식수를 로그로 출력합니다.
    - 반환 형식: { 'YYYY/MM': float(주식수), ... }
    """
    if not isinstance(parsed_data, dict):
        return {}

    tables = parsed_data.get("tables", parsed_data)
    
    # tables나 snapshot이 dict가 아닐 경우 안전하게 방어
    snapshot_root = tables.get("snapshot", {})
    if not isinstance(snapshot_root, dict):
        snapshot_root = parsed_data.get("snapshot", {})
    if not isinstance(snapshot_root, dict):
        return {}

    snp_data = snapshot_root.get("quarterly", {})
    
    # 👇 [핵심 방어 코드] snp_data가 딕셔너리가 아닐 경우(문자열 등일 때) 에러 방지
    if not isinstance(snp_data, dict):
        if debug:
            print(f"⚠️ [Quarterly Shares] quarterly 데이터가 dict가 아닙니다 (타입: {type(snp_data)})")
        return {}

    shares_dict = {}
    
    # '발행주식수' 또는 '보통주주식수' 관련 계정 탐색
    target_account = None
    for acc_name in snp_data.keys():
        acc_clean = re.sub(r"\s+", "", str(acc_name))
        if "발행주식수" in acc_clean or "보통주주식수" in acc_clean:
            target_account = acc_name
            break

    if not target_account:
        if debug:
            print(f"[Quarterly Shares] '발행주식수' 계정을 찾지 못했습니다.")
        return {}

    raw_vals = snp_data[target_account]
    if not isinstance(raw_vals, dict):
        return {}

    for col_str, val in raw_vals.items():
        col_str_clean = str(col_str).strip()
        upper_d = col_str_clean.upper()
        
        # (E) 추정치 또는 (P) 잠정치 마크 및 미래 분기 원천 차단
        if "(E)" in upper_d or "(P)" in upper_d:
            continue
            
        match = re.search(r'(\d{4})[/\.](\d{2})', col_str_clean)
        if match:
            year = int(match.group(1))
            month = int(match.group(2))
            q_key = f"{year:04d}/{month:02d}"
            
            # 물리적 미래 분기 방어 (2026년 6월 이후 데이터 차단)
            if year > 2026 or (year == 2026 and month > 6):
                continue

            shares_val = parse_val(val)
            if shares_val > 0:
                shares_dict[q_key] = shares_val
                if debug:
                    print(f"[Quarterly Shares Log] 연/월: {q_key}, 발행주식수(보통주): {shares_val:,.0f}주")

    return shares_dict
