"""companylist.txt 파일 읽기 및 파싱."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Company:
    name: str
    code: str


def load_company_list(path: str | Path) -> list[Company]:
    """companylist.txt를 읽어 Company 목록을 반환한다. EOF까지 읽는다."""
    companies: list[Company] = []
    file_path = Path(path)

    with file_path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = [part.strip() for part in line.split(",", 1)]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                continue

            name, code = parts
            if not re.fullmatch(r"\d{6}", code):
                continue

            companies.append(Company(name=name, code=code))

    return companies
