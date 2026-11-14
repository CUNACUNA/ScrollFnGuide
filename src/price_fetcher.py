"""네이버 금융 웹 API에서 현재 주가 조회."""

from __future__ import annotations

import os

import requests
import urllib3
from requests import Session

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
PRICE_UNAVAILABLE = "N/A"

REALTIME_API_URL = (
    "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
)


def resolve_verify_ssl(*, cli_secure: bool = False) -> bool:
    """SSL 검증 여부. 기본은 비활성(프록시 호환). --secure 또는 SCROLL_VERIFY_SSL=1 이면 활성화."""
    if cli_secure:
        return True
    env = os.environ.get("SCROLL_VERIFY_SSL", "0").strip().lower()
    return env in {"1", "true", "yes", "on"}


def _normalize_price(raw: str | int | float | None) -> str | None:
    if raw is None:
        return None

    text = str(raw).strip().replace(",", "")
    if not text or text in {"0", "-", "null"}:
        return None

    if text.isdigit():
        return f"{int(text):,}"

    try:
        value = float(text)
    except ValueError:
        return None

    if value <= 0:
        return None

    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _parse_realtime_payload(payload: dict) -> str | None:
    candidates: list[object] = []

    result = payload.get("result")
    if isinstance(result, dict):
        candidates.extend(
            [
                result.get("closePrice"),
                result.get("tradePrice"),
                result.get("now"),
            ]
        )

    datas = payload.get("datas")
    if isinstance(datas, list) and datas:
        first = datas[0]
        if isinstance(first, dict):
            candidates.extend(
                [
                    first.get("closePrice"),
                    first.get("tradePrice"),
                    first.get("now"),
                ]
            )

    for candidate in candidates:
        normalized = _normalize_price(candidate)
        if normalized:
            return normalized

    return None


def fetch_current_price(
    code: str,
    session: Session | None = None,
    *,
    verify_ssl: bool = False,
) -> str:
    """종목 코드의 현재 주가를 네이버 금융 실시간 API에서 조회한다."""
    http = session or requests.Session()
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    realtime_url = REALTIME_API_URL.format(code=code)

    try:
        response = http.get(realtime_url, headers=headers, timeout=10, verify=verify_ssl)
        if response.ok:
            payload = response.json()
            if isinstance(payload, dict):
                price = _parse_realtime_payload(payload)
                if price:
                    return price
    except (requests.RequestException, ValueError):
        pass

    return PRICE_UNAVAILABLE
