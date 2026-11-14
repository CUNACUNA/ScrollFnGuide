"""경계 조건 스모크 테스트를 웹 브라우저에서 실행할 수 있는 로컬 서버."""

from __future__ import annotations

import json
import tempfile
import textwrap
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.company_list import load_company_list
from src.price_fetcher import PRICE_UNAVAILABLE, fetch_current_price, resolve_verify_ssl
from src.scroll import scroll

HOST = "127.0.0.1"
PORT = 8765


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _run_scroll_on_text(content: str, fetcher) -> list[str]:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    lines: list[str] = []

    def capture(line: str) -> None:
        lines.append(line.rstrip("\n"))

    scroll(tmp_path, output=capture, fetcher=fetcher, delay_seconds=0)
    Path(tmp_path).unlink(missing_ok=True)
    return lines


def run_boundary_smoke_tests() -> list[dict[str, Any]]:
    """모킹 fetcher로 경계 케이스를 검증한다."""
    cases = [
      {
          "name": "empty_file",
          "content": "",
          "fetcher": lambda _code: "1,000",
          "expect_lines": [],
      },
      {
          "name": "whitespace_only",
          "content": "   \n\n  \n",
          "fetcher": lambda _code: "1,000",
          "expect_lines": [],
      },
      {
          "name": "single_company",
          "content": "삼성전자,005930\n",
          "fetcher": lambda _code: "72,500",
          "expect_lines": ["[1/1] 삼성전자 005930 72,500"],
      },
      {
          "name": "invalid_code_skipped",
          "content": "잘못된행,ABC\n삼성전자,005930\n",
          "fetcher": lambda _code: "72,500",
          "expect_lines": ["[1/1] 삼성전자 005930 72,500"],
      },
      {
          "name": "unavailable_price",
          "content": "테스트,000001\n",
          "fetcher": lambda _code: PRICE_UNAVAILABLE,
          "expect_lines": ["[1/1] 테스트 000001 N/A"],
      },
      {
          "name": "sequential_multi",
          "content": "A,005930\nB,000660\nC,035420\n",
          "fetcher": lambda code: {"005930": "1", "000660": "2", "035420": "3"}[code],
          "expect_lines": [
              "[1/3] A 005930 1",
              "[2/3] B 000660 2",
              "[3/3] C 035420 3",
          ],
      },
    ]

    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            lines = _run_scroll_on_text(case["content"], case["fetcher"])
            passed = lines == case["expect_lines"]
            results.append(
                {
                    "name": case["name"],
                    "passed": passed,
                    "expected": case["expect_lines"],
                    "actual": lines,
                }
            )
        except Exception as exc:  # pragma: no cover - smoke diagnostics
            results.append(
                {
                    "name": case["name"],
                    "passed": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )

    return results


def run_live_smoke(code: str) -> dict[str, Any]:
    normalized = code.strip()
    if not normalized.isdigit() or len(normalized) != 6:
        return {
            "passed": False,
            "error": "종목 코드는 6자리 숫자여야 합니다.",
            "code": code,
        }

    try:
        price = fetch_current_price(normalized, verify_ssl=resolve_verify_ssl())
        passed = price != PRICE_UNAVAILABLE
        return {
            "passed": passed,
            "code": normalized,
            "price": price,
        }
    except Exception as exc:  # pragma: no cover - live network diagnostics
        return {
            "passed": False,
            "code": normalized,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }


INDEX_HTML = textwrap.dedent(
    """
    <!doctype html>
    <html lang="ko">
    <head>
      <meta charset="utf-8" />
      <title>scroll boundary smoke</title>
      <style>
        body { font-family: sans-serif; margin: 2rem; max-width: 960px; }
        button { margin-right: 0.5rem; margin-bottom: 0.5rem; }
        pre { background: #f4f4f4; padding: 1rem; overflow: auto; }
        .pass { color: #0a7a28; }
        .fail { color: #b00020; }
      </style>
    </head>
    <body>
      <h1>scroll 경계 스모크 테스트</h1>
      <p>모킹 기반 경계 테스트와 실제 네이버 금융 1건 조회를 웹에서 실행합니다.</p>

      <section>
        <h2>경계 케이스 (모킹)</h2>
        <button id="run-boundary">경계 테스트 실행</button>
        <pre id="boundary-output">대기 중...</pre>
      </section>

      <section>
        <h2>실시간 1건 조회</h2>
        <label>종목 코드 <input id="live-code" value="005930" maxlength="6" /></label>
        <button id="run-live">실시간 조회</button>
        <pre id="live-output">대기 중...</pre>
      </section>

      <script>
        async function fetchJson(url) {
          const response = await fetch(url);
          return response.json();
        }

        document.getElementById('run-boundary').addEventListener('click', async () => {
          const output = document.getElementById('boundary-output');
          output.textContent = '실행 중...';
          const data = await fetchJson('/api/smoke/boundary');
          output.textContent = JSON.stringify(data, null, 2);
        });

        document.getElementById('run-live').addEventListener('click', async () => {
          const code = document.getElementById('live-code').value;
          const output = document.getElementById('live-output');
          output.textContent = '조회 중...';
          const data = await fetchJson('/api/smoke/live?code=' + encodeURIComponent(code));
          output.textContent = JSON.stringify(data, null, 2);
        });
      </script>
    </body>
    </html>
    """
)


class SmokeHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            _html_response(self, INDEX_HTML)
            return

        if path == "/api/health":
            _json_response(self, 200, {"status": "ok"})
            return

        if path == "/api/smoke/boundary":
            results = run_boundary_smoke_tests()
            passed = all(item.get("passed") for item in results)
            _json_response(
                self,
                200,
                {
                    "passed": passed,
                    "total": len(results),
                    "results": results,
                },
            )
            return

        if path == "/api/smoke/live":
            code = query.get("code", ["005930"])[0]
            _json_response(self, 200, run_live_smoke(code))
            return

        if path == "/api/companylist/count":
            list_path = Path("companylist.txt")
            if not list_path.exists():
                _json_response(self, 404, {"error": "companylist.txt not found"})
                return
            companies = load_company_list(list_path)
            _json_response(self, 200, {"count": len(companies), "companies": [c.name for c in companies]})
            return

        _json_response(self, 404, {"error": "not found"})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), SmokeHandler)
    print(f"smoke web server: http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버를 종료합니다.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
