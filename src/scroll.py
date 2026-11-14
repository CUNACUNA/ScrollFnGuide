"""기업 목록 순차 크롤링 오케스트레이션 및 CLI."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TextIO

from src.company_list import Company, load_company_list
from src.price_fetcher import fetch_current_price, resolve_verify_ssl


@dataclass(frozen=True)
class ScrollResult:
    index: int
    total: int
    company: Company
    price: str

    def to_line(self) -> str:
        return format_line(self.index, self.total, self.company, self.price)


def format_line(index: int, total: int, company: Company, price: str) -> str:
    """[현재번호/전체번호] 기업명 코드 현재주가 형식의 한 줄을 만든다."""
    return f"[{index}/{total}] {company.name} {company.code} {price}"


def scroll(
    company_list_path: str | Path,
    output: Callable[[str], None] | TextIO = print,
    fetcher: Callable[[str], str] | None = None,
    *,
    delay_seconds: float = 0.3,
    verify_ssl: bool = False,
    price_saver: Callable[[Company, str], None] | None = None,
) -> list[ScrollResult]:
    """기업 목록을 순차적으로 크롤링하고 결과를 출력한다."""
    companies = load_company_list(company_list_path)
    total = len(companies)
    if fetcher is None:
        price_fetcher = lambda code: fetch_current_price(code, verify_ssl=verify_ssl)
    else:
        price_fetcher = fetcher

    if callable(output):
        write = output
    else:
        write = output.write

    results: list[ScrollResult] = []

    for index, company in enumerate(companies, start=1):
        price = price_fetcher(company.code)
        result = ScrollResult(index=index, total=total, company=company, price=price)
        write(result.to_line() + "\n")
        if price_saver is not None:
            price_saver(company, price)
        results.append(result)

        if index < total and delay_seconds > 0:
            time.sleep(delay_seconds)

    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="기업 목록의 현재 주가를 순차 조회한다.")
    parser.add_argument(
        "--input",
        default="companylist.txt",
        help="기업 목록 파일 경로 (기본: companylist.txt)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.3,
        help="요청 간 대기 시간(초)",
    )
    parser.add_argument(
        "--secure",
        action="store_true",
        help="SSL 인증서 검증 활성화 (기본: 비활성)",
    )
    parser.add_argument(
        "--mongo",
        action="store_true",
        help="MongoDB에 현재 주가 저장",
    )
    parser.add_argument(
        "--mongo-uri",
        default=None,
        help="MongoDB 연결 URI (기본: mongodb://127.0.0.1:27017)",
    )
    return parser


def _resolve_price_saver(
    *,
    use_mongo: bool,
    mongo_uri: str | None,
) -> Callable[[Company, str], None] | None:
    if not use_mongo:
        return None

    from src.mongo_store import create_mongo_store, resolve_mongodb_uri

    uri = resolve_mongodb_uri(mongo_uri)
    try:
        store = create_mongo_store(uri)
    except Exception as exc:
        print(f"MongoDB 연결 실패, 저장 없이 계속합니다: {exc}", file=sys.stderr)
        return None

    def saver(company: Company, price: str) -> None:
        try:
            store.save_current_price(company, price)
        except Exception as exc:
            print(
                f"MongoDB 저장 실패 ({company.code}): {exc}",
                file=sys.stderr,
            )

    return saver


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"입력 파일을 찾을 수 없습니다: {input_path}", file=sys.stderr)
        return 1

    scroll(
        input_path,
        delay_seconds=args.delay,
        verify_ssl=resolve_verify_ssl(cli_secure=args.secure),
        price_saver=_resolve_price_saver(use_mongo=args.mongo, mongo_uri=args.mongo_uri),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
