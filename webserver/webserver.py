"""Scrawl MongoDB 데이터 조회 단일 파일 웹서버 (계층형 Finance 6개 표 전체 출력 보완)."""

from __future__ import annotations

import argparse
import json
import os
import re
import textwrap
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse
from pymongo import MongoClient

# ==============================================================================
# 1. 테이블 및 상수 정의
# ==============================================================================
QUERYABLE_TABLES: tuple[str, ...] = (
    "ALL",
    "CURRENT-PRICE",
    "snapshot",
    "포괄손익계산서",
    "재무상태표",
    "현금흐름표",
    "기업개요",
    "재무비율-누적",
    "재무비율-3개월",
    "투자지표",
    "컨센서스",
)

EXAMPLE_CODE = "005930"
EXAMPLE_TABLE = "ALL"

HOST = "127.0.0.1"
PORT = 8770
DB_NAME = "fnguide_db"
CODE_PATTERN = re.compile(r"^\d{6}$")
DEFAULT_MONGODB_URI = "mongodb://127.0.0.1:27017"


# ==============================================================================
# 2. MongoReader 및 검증 헬퍼 로직
# ==============================================================================
def resolve_mongodb_uri(cli_uri: str | None = None) -> str:
    if cli_uri:
        return cli_uri
    return os.environ.get("MONGODB_URI", DEFAULT_MONGODB_URI)


def normalize_code(code: str) -> str:
    normalized = code.strip()
    if not CODE_PATTERN.fullmatch(normalized):
        raise ValueError(f"기업 코드는 6자리 숫자여야 합니다: {code!r}")
    return normalized


def normalize_table(table: str) -> str:
    normalized = table.strip()
    if not normalized:
        return "ALL"
    if normalized not in QUERYABLE_TABLES:
        allowed = ", ".join(QUERYABLE_TABLES)
        raise ValueError(f"알 수 없는 테이블입니다: {table!r} (가능: {allowed})")
    return normalized


def to_jsonable(value: Any) -> Any:
    """MongoDB 문서를 JSON 직렬화 가능한 형태로 변환한다."""
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if type(value).__name__ == "ObjectId":
        return str(value)
    return value


class MongoReader:
    """MongoDB에서 기업코드 및 특정/전체 테이블 데이터를 조건에 맞게 조회한다."""

    def __init__(self, db: Any) -> None:
        self._db = db

    @classmethod
    def from_uri(cls, uri: str) -> MongoReader:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        return cls(client[DB_NAME])

    def list_tables(self) -> list[str]:
        return list(QUERYABLE_TABLES)

    def find_data(self, code: str, table: str = "ALL") -> dict[str, Any] | None:
        normalized_code = normalize_code(code)
        target_table = normalize_table(table)

        combined_doc: dict[str, Any] = {"code": normalized_code, "tables": {}}
        found_any = False

        # 1. stock_tables 컬렉션 조회
        stock_doc = self._db["stock_tables"].find_one({"_id": normalized_code})
        if stock_doc:
            if "name" in stock_doc:
                combined_doc["name"] = stock_doc["name"]

            raw_tables = stock_doc.get("tables", {})
            if isinstance(raw_tables, dict):
                if target_table == "ALL":
                    combined_doc["tables"].update(raw_tables)
                    if raw_tables:
                        found_any = True
                else:
                    # 선택된 특정 테이블(예: snapshot, 포괄손익계산서 등) 필터링
                    for key, val in raw_tables.items():
                        if key == target_table or key.startswith(f"{target_table}_") or key.startswith(f"{target_table}>"):
                            combined_doc["tables"][key] = val
                            found_any = True

        # 2. ALL 또는 CURRENT-PRICE 처리
        if target_table in ("ALL", "CURRENT-PRICE"):
            price_doc = self._db["CURRENT-PRICE"].find_one(
                {"$or": [{"기업코드": normalized_code}, {"code": normalized_code}]}
            )
            if price_doc:
                found_any = True
                if "name" in price_doc and "name" not in combined_doc:
                    combined_doc["name"] = price_doc["name"]

                combined_doc["tables"]["CURRENT-PRICE"] = {
                    "현재주가": price_doc.get("current_price", "N/A"),
                    "최종업데이트": price_doc.get("updated_at", "-"),
                }

        # 3. 기타 개별 컬렉션 추가 체크
        if target_table not in ("ALL", "snapshot", "CURRENT-PRICE") and not found_any:
            item_doc = self._db[target_table].find_one(
                {"$or": [{"기업코드": normalized_code}, {"code": normalized_code}]}
            )
            if item_doc:
                found_any = True
                if "name" in item_doc and "name" not in combined_doc:
                    combined_doc["name"] = item_doc["name"]
                combined_doc["tables"][target_table] = item_doc

        elif target_table == "ALL":
            for table_name in QUERYABLE_TABLES:
                if table_name in ("ALL", "CURRENT-PRICE", "snapshot"):
                    continue
                item_doc = self._db[table_name].find_one(
                    {"$or": [{"기업코드": normalized_code}, {"code": normalized_code}]}
                )
                if item_doc:
                    found_any = True
                    combined_doc["tables"][table_name] = item_doc

        if not found_any or not combined_doc["tables"]:
            return None

        return to_jsonable(combined_doc)


def create_mongo_reader(uri: str | None = None) -> MongoReader:
    return MongoReader.from_uri(resolve_mongodb_uri(uri))


# ==============================================================================
# 3. HTTP 응답 및 HTML 렌더링 로직
# ==============================================================================
def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, status: int, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def query_data(reader: MongoReader, *, code: str, table: str) -> dict[str, Any]:
    normalized_code = normalize_code(code)
    normalized_table = normalize_table(table)
    document = reader.find_data(normalized_code, table=normalized_table)

    if document is None:
        return {
            "found": False,
            "기업코드": normalized_code,
            "table": normalized_table,
            "message": "해당 조건의 데이터가 없습니다.",
        }
    return {
        "found": True,
        "기업코드": normalized_code,
        "table": normalized_table,
        "data": document,
    }


def build_example_page(*, error: str | None = None) -> str:
    table_options = "\n".join(
        f'            <option value="{name}">{name}</option>' for name in QUERYABLE_TABLES
    )
    example_path = f"/?code={EXAMPLE_CODE}&table={quote(EXAMPLE_TABLE)}"
    example_api = f"/api/data?code={EXAMPLE_CODE}&table={quote(EXAMPLE_TABLE)}"
    error_block = f'<p class="error">⚠️ {error}</p>' if error else ""

    return textwrap.dedent(
        f"""
        <!doctype html>
        <html lang="ko">
        <head>
          <meta charset="utf-8" />
          <title>Scrawl 데이터 조회</title>
          <style>
            body {{ font-family: 'Malgun Gothic', sans-serif; margin: 2rem; max-width: 960px; line-height: 1.6; background: #f8f9fa; }}
            .container {{ background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            code, pre {{ background: #f4f4f4; padding: 0.2rem 0.4rem; border-radius: 4px; }}
            label {{ display: block; margin-bottom: 0.75rem; font-weight: bold; }}
            input, select {{ margin-left: 0.5rem; padding: 0.4rem 0.6rem; font-size: 1rem; border: 1px solid #ccc; border-radius: 4px; }}
            button {{ padding: 0.5rem 1rem; font-size: 1rem; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }}
            button:hover {{ background: #0056b3; }}
            .error {{ color: #b00020; font-weight: bold; }}
            section {{ margin-top: 1.5rem; }}
          </style>
        </head>
        <body>
          <div class="container">
            <h1>📊 Scrawl MongoDB 재무 데이터 조회</h1>
            <p>6자리 <strong>기업코드</strong>와 <strong>테이블명</strong>을 선택하여 조회합니다 (ALL 선택 시 전체 출력).</p>
            {error_block}

            <section>
              <h2>직접 조회</h2>
              <form method="get" action="/">
                <label>기업코드 (6자리)
                  <input name="code" maxlength="6" pattern="\\d{{6}}" placeholder="{EXAMPLE_CODE}" required />
                </label>
                <label>테이블 선택
                  <select name="table">
                    {table_options}
                  </select>
                </label>
                <button type="submit">조회하기</button>
              </form>
            </section>

            <section>
              <h2>조회 예시</h2>
              <ul>
                <li>전체(ALL) 조회: <a href="{example_path}"><code>{example_path}</code></a></li>
                <li>JSON API: <a href="{example_api}"><code>{example_api}</code></a></li>
              </ul>
            </section>
          </div>
        </body>
        </html>
        """
    )


def extract_html_tables_from_dict(data_dict: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    """계층형 딕셔너리를 탐색하며 모든 HTML 표 및 CURRENT-PRICE 카드를 추출하도록 보완"""
    extracted = []
    for key, val in data_dict.items():
        if key in ("_id", "code", "기업코드"):
            continue
        category_name = f"{prefix} > {key}" if prefix else key
        
        if key == "CURRENT-PRICE" and isinstance(val, dict):
            extracted.append((category_name, val))
        elif isinstance(val, dict):
            extracted.extend(extract_html_tables_from_dict(val, category_name))
        elif isinstance(val, str) and len(val.strip()) > 0:
            # HTML 태그 존재 여부 및 유효한 표 내용 검증
            if "<table" in val.lower() or "<div" in val.lower() or "<td" in val.lower():
                extracted.append((category_name, val))
    return extracted


def build_result_page(payload: dict[str, Any]) -> str:
    code = payload.get("기업코드", "")
    table = payload.get("table", "ALL")
    back_link = f"/?code={code}&table={quote(str(table))}"
    api_link = f"/api/data?code={code}&table={quote(str(table))}"

    table_options_html = "\n".join(
        f'<option value="{name}" {"selected" if name == table else ""}>{name}</option>'
        for name in QUERYABLE_TABLES
    )

    if not payload.get("found"):
        content_html = f"""
        <div class="error-box">
            ⚠️ <strong>{code}</strong> 종목의 <strong>{table}</strong> 테이블 데이터가 DB에 존재하지 않습니다.
        </div>
        """
    else:
        doc = payload.get("data", {})
        company_name = doc.get("name", "")
        extracted_tables = extract_html_tables_from_dict(doc)

        if extracted_tables:
            table_cards = []
            for category, content in extracted_tables:
                if isinstance(content, dict):
                    items_html = "".join(
                        f"<li><strong>{k}</strong>: {v}</li>" for k, v in content.items()
                    )
                    card = f"""
                    <div class="table-card price-card">
                        <div class="table-title">💵 {category.upper()}</div>
                        <ul style="font-size: 1.1rem; list-style: none; padding-left: 0;">{items_html}</ul>
                    </div>
                    """
                else:
                    card = f"""
                    <div class="table-card">
                        <div class="table-title">📌 {category.upper()}</div>
                        <div class="table-wrapper">{content}</div>
                    </div>
                    """
                table_cards.append(card)
            content_html = "\n".join(table_cards)
        else:
            pretty_json = json.dumps(doc, ensure_ascii=False, indent=2)
            content_html = f"<pre>{pretty_json}</pre>"

    return textwrap.dedent(
        f"""
        <!doctype html>
        <html lang="ko">
        <head>
          <meta charset="utf-8" />
          <title>[{code}] {table} 조회 결과</title>
          <style>
            body {{ font-family: 'Malgun Gothic', sans-serif; margin: 2rem; background: #f8f9fa; line-height: 1.5; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            .header {{ background: white; padding: 1.5rem; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 1.5rem; }}
            .nav-links {{ margin-bottom: 1rem; }}
            .nav-links a {{ margin-right: 1rem; color: #007bff; text-decoration: none; font-weight: bold; }}
            .filter-form {{ display: inline-block; margin-left: 1rem; }}
            .filter-form select {{ padding: 0.3rem 0.6rem; font-size: 0.95rem; }}
            .filter-form button {{ padding: 0.3rem 0.8rem; font-size: 0.95rem; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }}
            .table-card {{ background: white; padding: 1.5rem; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 2rem; overflow-x: auto; }}
            .price-card {{ background: #eef6ff; border: 1px solid #b6d4fe; }}
            .table-title {{ font-size: 1.25rem; font-weight: bold; color: #007bff; border-bottom: 2px solid #007bff; padding-bottom: 0.5rem; margin-bottom: 1rem; }}
            .error-box {{ background: white; padding: 2rem; border-radius: 8px; color: #dc3545; font-size: 1.1rem; border: 1px solid #f5c6cb; }}
            pre {{ background: #ffffff; padding: 1rem; border-radius: 4px; border: 1px solid #ddd; overflow: auto; }}
            
            table {{ border-collapse: collapse; width: 100%; margin-top: 0.5rem; }}
            th, td {{ border: 1px solid #e0e0e0; padding: 8px 12px; font-size: 0.9rem; text-align: right; }}
            th {{ background-color: #f2f4f7; text-align: center; font-weight: bold; color: #333; }}
            td:first-child {{ text-align: left; font-weight: bold; }}
          </style>
        </head>
        <body>
          <div class="container">
            <div class="header">
              <div class="nav-links">
                <a href="/">← 검색 홈으로</a>
                <a href="{api_link}" target="_blank">🔗 JSON API 보기</a>
                
                <form class="filter-form" method="get" action="/">
                  <input type="hidden" name="code" value="{code}" />
                  <label for="table"><strong>테이블 변경:</strong></label>
                  <select name="table" id="table" onchange="this.form.submit()">
                    {table_options_html}
                  </select>
                </form>
              </div>
              <h2>기업코드: <code>{code}</code> {f"({company_name})" if company_name else ""} | 필터: <code>{table}</code></h2>
            </div>

            {content_html}
          </div>
        </body>
        </html>
        """
    )


# ==============================================================================
# 4. 서버 핸들러 및 메인 실행부
# ==============================================================================
def create_handler(reader_factory: Callable[[], MongoReader] | None = None) -> type[BaseHTTPRequestHandler]:
    def get_reader() -> MongoReader:
        if reader_factory is not None:
            return reader_factory()
        return create_mongo_reader()

    class ScrawlHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            code = query.get("code", [""])[0].strip()
            table = query.get("table", ["ALL"])[0].strip()

            if path == "/api/health":
                _json_response(self, 200, {"status": "ok"})
                return

            if path == "/api/tables":
                try:
                    reader = get_reader()
                    _json_response(self, 200, {"tables": reader.list_tables()})
                except Exception as exc:
                    _json_response(self, 503, {"error": str(exc)})
                return

            if path in {"/api/data", "/"}:
                if not code:
                    if path == "/api/data":
                        _json_response(
                            self,
                            400,
                            {
                                "error": "기업코드(code)가 필요합니다.",
                                "example": {
                                    "code": EXAMPLE_CODE,
                                    "url": f"/api/data?code={EXAMPLE_CODE}&table=ALL",
                                },
                            },
                        )
                        return
                    _html_response(self, 200, build_example_page())
                    return

                try:
                    reader = get_reader()
                    payload = query_data(reader, code=code, table=table)
                except ValueError as exc:
                    if path == "/api/data":
                        _json_response(self, 400, {"error": str(exc)})
                        return
                    _html_response(self, 400, build_example_page(error=str(exc)))
                    return
                except Exception as exc:
                    if path == "/api/data":
                        _json_response(self, 503, {"error": str(exc)})
                        return
                    _html_response(self, 503, build_example_page(error=str(exc)))
                    return

                if path == "/api/data":
                    status = 200 if payload["found"] else 404
                    _json_response(self, status, payload)
                    return

                _html_response(self, 200, build_result_page(payload))
                return

            _json_response(self, 404, {"error": "not found"})

    return ScrawlHandler


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrawl MongoDB 데이터 조회 웹서버")
    parser.add_argument("--host", default=HOST, help=f"바인드 호스트 (기본: {HOST})")
    parser.add_argument("--port", type=int, default=PORT, help=f"포트 (기본: {PORT})")
    parser.add_argument("--mongo-uri", default=None, help="MongoDB 연결 URI")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    reader_factory = (lambda: create_mongo_reader(args.mongo_uri)) if args.mongo_uri else None
    handler = create_handler(reader_factory=reader_factory)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Scrawl web server running at: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버를 종료합니다.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()