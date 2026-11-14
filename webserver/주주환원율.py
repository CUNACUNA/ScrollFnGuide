# -*- coding: utf-8 -*-
import re

def parse_val(val):
    """문자열이나 숫자를 안전하게 float으로 변환"""
    if val is None:
        return 0.0
    clean = re.sub(r"[^\d.-]", "", str(val))
    try:
        return float(clean) if clean else 0.0
    except ValueError:
        return 0.0

def calculate_quarterly_shareholder_return_rate(parsed: dict, current_price: float = None, debug: bool = True) -> float | None:
    """
    [삼성전자 Snapshot 분기 전용 계산 로직]
    1. 오직 parsed["snapshot"]["quarterly"] 데이터만 탐색합니다.
    2. 'P', 'E'가 포함되거나 2026년 9월 이후의 미래/잠정 분기는 원천 차단합니다.
    3. 순수 확정 최근 4개 분기('2026/06', '2026/03', '2025/12', '2025/09')만 선정합니다.
    4. Snapshot 내의 현금DPS, 발행주식수, 순이익 데이터를 이용해 환원율을 산출합니다.
    """
    if not isinstance(parsed, dict):
        if debug:
            print("[DEBUG] ❌ parsed 데이터가 dict 형식이 아닙니다.")
        return None

    # 💡 오직 snapshot의 분기 데이터만 추출
    snapshot_q = parsed.get("snapshot", {}).get("quarterly", {})

    if debug:
        print(f"\n================ [주주환원율 디버깅 (Snapshot 분기 전용)] ================")
        print(f"[DEBUG] 전달받은 현재가(current_price): {current_price}")

    if not snapshot_q:
        if debug:
            print("[DEBUG] ❌ snapshot 분기 데이터를 찾을 수 없습니다.")
        return None

    all_quarters = set()

    def is_valid_actual_quarter(d_str: str) -> bool:
        upper_d = d_str.upper()
        
        # 1. (P), (E) 마크가 포함된 경우 차단
        if "P" in upper_d or "E" in upper_d:
            return False
            
        # 2. 날짜 형식 파싱 (YYYY/MM)
        match = re.search(r"(\d{4})[/-](\d{2})", upper_d)
        if match:
            year = int(match.group(1))
            month = int(match.group(2))
            
            # 💡 2026년 9월(2026/09) 포함 그 이후의 분기는 잠정/미래 데이터이므로 강제 제외
            if year > 2026 or (year == 2026 and month >= 9):
                return False
                
            return True
            
        return False

    # snapshot_q 내의 모든 계정에서 유효한 확정 분기 키 수집
    for acc_name, acc_vals in snapshot_q.items():
        if isinstance(acc_vals, dict):
            for d in acc_vals.keys():
                d_str = str(d).strip()
                if d_str:
                    if is_valid_actual_quarter(d_str):
                        all_quarters.add(d_str)

    unique_quarters = sorted(list(all_quarters), reverse=True)
    target_4_quarters = unique_quarters[:4]

    if debug:
        print(f"[DEBUG] [Snapshot 전용] 최종 선정된 최근 확정 4개 분기: {target_4_quarters}")

    if len(target_4_quarters) < 4:
        if debug:
            print(f"[DEBUG] ⚠️ 확정된 최근 4분기 데이터가 부족합니다. (확인된 분기 수: {len(target_4_quarters)})")
        if not target_4_quarters:
            return None

    # Snapshot 내부에서 필요한 키 동적 탐색
    shares_key = None
    dps_key = None
    net_income_key = None

    for acc_name in snapshot_q.keys():
        clean_name = str(acc_name).replace(" ", "")
        if "발행주식수" in clean_name and ("보통" in clean_name or shares_key is None):
            shares_key = acc_name
        elif "현금DPS" in clean_name or "DPS" in clean_name:
            dps_key = acc_name
        elif "순이익" in clean_name or "당기순이익" in clean_name or "지배주주순이익" in clean_name:
            if net_income_key is None:
                net_income_key = acc_name

    if debug:
        print(f"[DEBUG] 매칭된 Snapshot 발행주식수 계정: {shares_key}")
        print(f"[DEBUG] 매칭된 Snapshot 현금DPS 계정: {dps_key}")
        print(f"[DEBUG] 매칭된 Snapshot 순이익 계정: {net_income_key}")

    shares_data = snapshot_q.get(shares_key, {}) if shares_key else {}
    dps_data = snapshot_q.get(dps_key, {}) if dps_key else {}
    ni_data = snapshot_q.get(net_income_key, {}) if net_income_key else {}

    # (3) 분기별 배당금 및 주식수 감소분(소각 추정) 계산
    sorted_q_chronological = sorted(target_4_quarters)  # 과거 -> 최신 순 정렬
    total_dividends = 0.0
    total_reduced_shares = 0.0
    prev_shares = None

    if debug:
        print(f"[DEBUG] --- [Snapshot 전용] 분기별 배당금 및 주식수 변화 내역 ---")

    for q in sorted_q_chronological:
        q_dps = parse_val(dps_data.get(q))
        q_shares = parse_val(shares_data.get(q))
        
        q_div = q_dps * q_shares  # 원 단위
        total_dividends += q_div

        if debug:
            print(f"  - [확정 분기 {q}] 현금DPS: {q_dps:,.2f}원, 발행주식수: {q_shares:,.0f}주 => 배당금: {q_div:,.0f}원")

        if q_shares > 0:
            if prev_shares is not None:
                diff = prev_shares - q_shares
                if diff > 0:
                    total_reduced_shares += diff
                    if debug:
                        print(f"    ↳ [주식수 감소 감지] 이전 분기 대비 {diff:,.0f}주 감소")
            prev_shares = q_shares

    if debug:
        print(f"[DEBUG] 최근 4개 확정 분기 총 배당금 합계: {total_dividends:,.0f}원")
        print(f"[DEBUG] 총 줄어든 발행주식수: {total_reduced_shares:,.0f}주")

    # (4) Snapshot 내 순이익 합산 및 단위 조정 (억 -> 원)
    total_net_income_won = 0.0
    if debug:
        print(f"[DEBUG] --- [Snapshot 전용] 확정 분기별 순이익 내역 ---")

    for q in target_4_quarters:
        raw_ni = parse_val(ni_data.get(q))
        ni_won = raw_ni * 100_000_000  # 억 원 -> 원 단위 환산
        total_net_income_won += ni_won
        if debug:
            print(f"  - [확정 분기 {q}] 순이익(Snapshot): {raw_ni:,.2f}억 (환산: {ni_won:,.0f}원)")

    if debug:
        print(f"[DEBUG] 최근 4개 확정 분기 총 순이익 합계: {total_net_income_won:,.0f}원")

    if total_net_income_won <= 0:
        if debug:
            print(f"[DEBUG] ❌ 순이익이 0 이하이거나 데이터가 없어 환원율 계산 불가")
        return 0.0

    # (5) 자사주 환원액 계산 (줄어든 주식수 × 현재가) -> 단위: 원
    cp = current_price if current_price is not None else 0.0
    buyback_return_amount = total_reduced_shares * cp

    # (6) 총 주주환원율 산출
    total_shareholder_return_amount = total_dividends + buyback_return_amount
    payout_ratio = (total_shareholder_return_amount / total_net_income_won) * 100

    if debug:
        print(f"[DEBUG] 자사주 환원액 (수량 {total_reduced_shares:,.0f}주 × 현재가 {cp:,.0f}원): {buyback_return_amount:,.0f}원")
        print(f"[DEBUG] 총 주주환원액: {total_shareholder_return_amount:,.0f}원")
        print(f"[DEBUG] 최종 계산된 총주주환원율(Snapshot 전용): {payout_ratio:.2f}%")
        print(f"==================================================================\n")

    return round(payout_ratio, 2)