# -*- coding: utf-8 -*-

def calculate_rim_from_values(bps_val: float, roe_val: float, current_price: float = None, required_return: float = 0.08, debug: bool = False) -> tuple[float | None, float | None]:
    """
    [RIM (잔여소득모형) 적정주가 및 상승 여력 계산 모듈]
    - 이미 추출된 BPS와 ROE 값을 입력받아 S-RIM 공식으로 적정주가와 상승 여력(%)을 산출합니다.
    - 반환값: (RIM 적정주가, 상승 여력(%))
    """
    if bps_val is None or bps_val <= 0 or roe_val is None:
        return None, None

    # ROE 단위 보정 (퍼센트 숫자로 들어올 경우 소수점으로 변환, 예: 12.5 -> 0.125)
    roe_decimal = roe_val / 100.0 if roe_val > 1.0 else roe_val

    # S-RIM 영구형 공식: 적정주가 = BPS + (BPS * (ROE - 요구수익률)) / 요구수익률
    rim_price = bps_val + (bps_val * (roe_decimal - required_return)) / required_return
    rim_price = max(0.0, rim_price)  # 음수 방지

    # 현재가 대비 상승 여력 (%) 정규화 계산
    upside_pct = None
    if current_price and current_price > 0 and rim_price > 0:
        upside_pct = round(((rim_price - current_price) / current_price) * 100, 2)

    if debug:
        print(f"  📊 [RIM 계산 완료] BPS: {bps_val:,.2f}원 | ROE: {roe_val}% | 요구수익률: {required_return*100:.1f}%")
        print(f"     ➔ RIM 적정주가: {rim_price:,.2f}원 | 현재가: {current_price:,.0f}원 | 상승 여력: {upside_pct:+.2f}%")

    return round(rim_price, 2), upside_pct