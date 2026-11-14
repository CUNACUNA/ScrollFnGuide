"""MongoDB 현재 주가 저장."""

from __future__ import annotations

import os
import re
from typing import Any, Protocol

from src.company_list import Company

DB_NAME = "Scrawl"
COLLECTION_NAME = "CURRENT-PRICE"
DEFAULT_MONGODB_URI = "mongodb://127.0.0.1:27017"

# 6자리 종목 코드 — 컬렉션 간 조회용 공통 키
CODE_KEY = "기업코드"
NAME_KEY = "기업명"
PRICE_KEY = "현재주가"
CODE_PATTERN = r"^\d{6}$"

COLLECTION_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [CODE_KEY, NAME_KEY, PRICE_KEY],
        "additionalProperties": False,
        "properties": {
            "_id": {"bsonType": "objectId"},
            CODE_KEY: {
                "bsonType": "string",
                "description": "6자리 종목 코드 (조회 키)",
                "pattern": CODE_PATTERN,
            },
            NAME_KEY: {
                "bsonType": "string",
                "description": "기업명",
            },
            PRICE_KEY: {
                "bsonType": "string",
                "description": "천 단위 콤마 포함 주가 문자열 또는 N/A",
            },
        },
    }
}


def resolve_mongodb_uri(cli_uri: str | None = None) -> str:
    if cli_uri:
        return cli_uri
    return os.environ.get("MONGODB_URI", DEFAULT_MONGODB_URI)


def normalize_code(code: str) -> str:
    """6자리 종목 코드를 정규화한다."""
    normalized = code.strip()
    if not re.fullmatch(CODE_PATTERN, normalized):
        raise ValueError(f"종목 코드는 6자리 숫자여야 합니다: {code!r}")
    return normalized


def code_filter(code: str) -> dict[str, str]:
    """컬렉션 조회용 필터. 기업코드(6자리)가 공통 키."""
    return {CODE_KEY: normalize_code(code)}


def build_price_document(company: Company, price: str) -> dict[str, str]:
    return {
        CODE_KEY: normalize_code(company.code),
        NAME_KEY: company.name,
        PRICE_KEY: price,
    }


class PriceCollection(Protocol):
    def find_one(self, filter: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any] | None: ...

    def update_one(
        self,
        filter: dict[str, Any],
        update: dict[str, Any],
        upsert: bool = False,
    ) -> Any: ...


class MongoStore:
    def __init__(self, collection: PriceCollection) -> None:
        self._collection = collection

    @classmethod
    def from_client(cls, client: Any) -> MongoStore:
        collection = client[DB_NAME][COLLECTION_NAME]
        return cls(collection)

    def find_by_code(self, code: str) -> dict[str, Any] | None:
        """기업코드(6자리)로 CURRENT-PRICE 문서를 조회한다."""
        return self._collection.find_one(code_filter(code))

    def save_current_price(self, company: Company, price: str) -> None:
        document = build_price_document(company, price)
        self._collection.update_one(
            code_filter(company.code),
            {"$set": document},
            upsert=True,
        )


def create_mongo_store(uri: str) -> MongoStore:
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    return MongoStore.from_client(client)


def ensure_collection(db: Any) -> None:
    """컬렉션·validator·기업코드 unique index를 보장한다."""
    if COLLECTION_NAME not in db.list_collection_names():
        db.create_collection(COLLECTION_NAME, validator=COLLECTION_VALIDATOR)
    else:
        db.command(
            "collMod",
            COLLECTION_NAME,
            validator=COLLECTION_VALIDATOR,
            validationLevel="moderate",
        )

    db[COLLECTION_NAME].create_index(
        CODE_KEY,
        unique=True,
        name="idx_기업코드",
    )
