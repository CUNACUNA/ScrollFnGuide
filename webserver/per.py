# -*- coding: utf-8 -*-
import re
import eps

def calculate_per(parsed: dict, current_price: float = None, mode: str = "annual", debug: bool = False) -> tuple[float | None, str | None]:
    """
    [PER 계산 모듈]
    - eps.py의 calculate_eps를 호출하여 모드별(연간/분기 TTM) EPS를 가져온 후 PER 산출
    - 중복 연산 방지 및 일관성 확보
    """
    if not current_price or current_price <= 0:
        return None, None

    # eps.py를 통해 통일된 EPS 및 기준일자 호출
    eps_val, eps_date = eps.calculate_eps(parsed, mode=mode, debug=debug)

    if eps_val is not None and eps_val > 0:
        per = round(current_price / eps_val, 2)
        if debug:
            print(f"[PER Calculation] Price: {current_price} / EPS ({eps_val}) = PER: {per}")
        return per, eps_date

    if debug:
        print(f"[PER Skip] 유효하지 않은 EPS 값 (EPS: {eps_val})")
    return None, None