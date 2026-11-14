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

def calculate_target_upside(doc: dict, current_price: float = None, debug: bool = False) -> tuple[float | None, str | None]:
    """
    [컨센서스 상승여력 계산 모듈]
    - 공식: 상승여력(%) = (목표주가 ÷ 현재가) * 100
    - '4.0' 같은 투자의견 행 내부의 '목표주가' 컬럼 데이터를 정확히 추출합니다.
    """
    if not current_price or current_price <= 0 or not isinstance(doc, dict):
        return None, None

    # parsed_data.consensus.target_price 경로 탐색
    parsed_data = doc.get("parsed_data", {})
    consensus_data = parsed_data.get("consensus", {}).get("target_price", {})
    
    if not consensus_data and "consensus" in doc:
        consensus_data = doc.get("consensus", {}).get("target_price", {})

    if not consensus_data or not isinstance(consensus_data, dict):
        return None, None

    target_price = 0.0
    target_date = "컨센서스"

    # 구조 순회: 각 행의 딕셔너리 내부에서 '목표주가' 컬럼 탐색
    for row_key, row_val in consensus_data.items():
        if isinstance(row_val, dict):
            for col_key, val in row_val.items():
                col_clean = re.sub(r"\s+", "", str(col_key))
                if "목표주가" in col_clean:
                    target_price = parse_val(val)
                    break
        if target_price > 0:
            break

    # 컬럼명 매칭 실패 시 두 번째 값(목표주가 위치) 차선책 활용
    if target_price <= 0:
        for row_key, row_val in consensus_data.items():
            if isinstance(row_val, dict) and len(row_val) >= 2:
                col_keys = list(row_val.keys())
                second_val = row_val[col_keys[1]]
                parsed_p = parse_val(second_val)
                if parsed_p > 1000:
                    target_price = parsed_p
                    break

    if target_price <= 0:
        return None, None

    # 상승여력 퍼센트 계산 ((목표주가 / 현재가) * 100)
    upside_percent = round((target_price / current_price) * 100, 2)

    if debug:
        print(f"🎯 [Upside 성공] 목표주가: {target_price} / 현재가: {current_price} = 상승여력: {upside_percent}%")

    return upside_percent, target_date