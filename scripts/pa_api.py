"""Amazon Product Advertising API v5 の最小クライアント（AWS SigV4 署名つき）。

アソシエイト審査通過＋直近180日で3件の売上が必要なため、
キーが取得できるまでは使えない。取得後に環境変数を設定すれば動く。

  AMAZON_ACCESS_KEY / AMAZON_SECRET_KEY / AMAZON_ASSOCIATE_TAG
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os

import requests

HOST = "webservices.amazon.co.jp"
REGION = "us-west-2"          # 日本のマーケットプレイスでも us-west-2 を使う
SERVICE = "ProductAdvertisingAPI"
MARKETPLACE = "www.amazon.co.jp"


class PaApiError(RuntimeError):
    pass


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str) -> bytes:
    k = _sign(("AWS4" + secret).encode("utf-8"), date_stamp)
    k = _sign(k, REGION)
    k = _sign(k, SERVICE)
    return _sign(k, "aws4_request")


def _credentials() -> tuple[str, str, str]:
    access = os.environ.get("AMAZON_ACCESS_KEY", "").strip()
    secret = os.environ.get("AMAZON_SECRET_KEY", "").strip()
    tag = os.environ.get("AMAZON_ASSOCIATE_TAG", "").strip()
    if not (access and secret and tag):
        raise PaApiError(
            "PA-API の認証情報がありません。"
            "AMAZON_ACCESS_KEY / AMAZON_SECRET_KEY / AMAZON_ASSOCIATE_TAG を設定してください。"
        )
    return access, secret, tag


def call(operation: str, payload: dict, now: dt.datetime | None = None) -> dict:
    """GetItems / SearchItems などを呼ぶ。"""
    access, secret, tag = _credentials()
    payload = {**payload, "PartnerTag": tag, "PartnerType": "Associates", "Marketplace": MARKETPLACE}
    body = json.dumps(payload)

    now = now or dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    path = f"/paapi5/{operation.lower()}"
    target = f"com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{operation}"

    headers = {
        "content-encoding": "amz-1.0",
        "content-type": "application/json; charset=utf-8",
        "host": HOST,
        "x-amz-date": amz_date,
        "x-amz-target": target,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_request = "\n".join([
        "POST", path, "", canonical_headers, signed_headers,
        hashlib.sha256(body.encode("utf-8")).hexdigest(),
    ])

    scope = f"{date_stamp}/{REGION}/{SERVICE}/aws4_request"
    to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    signature = hmac.new(
        _signing_key(secret, date_stamp), to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    response = requests.post(f"https://{HOST}{path}", data=body, headers=headers, timeout=30)
    if response.status_code != 200:
        raise PaApiError(f"PA-API {response.status_code}: {response.text[:500]}")
    return response.json()


DEFAULT_RESOURCES = [
    "ItemInfo.Title",
    "ItemInfo.ByLineInfo",
    "ItemInfo.Features",
    "Images.Primary.Large",
    "Offers.Listings.Price",
    "Offers.Listings.Availability.Message",
]


def get_items(asins: list[str], resources: list[str] | None = None) -> dict[str, dict]:
    """ASIN のリストから商品情報を取る。1回のリクエストにつき最大10件。"""
    out: dict[str, dict] = {}
    for i in range(0, len(asins), 10):
        chunk = asins[i:i + 10]
        data = call("GetItems", {
            "ItemIds": chunk,
            "Resources": resources or DEFAULT_RESOURCES,
        })
        for item in (data.get("ItemsResult") or {}).get("Items", []):
            out[item["ASIN"]] = _normalize(item)
    return out


def search_items(keywords: str, count: int = 10, **extra) -> list[dict]:
    """キーワード検索。新しい商品の仕込みに使う。"""
    data = call("SearchItems", {
        "Keywords": keywords,
        "ItemCount": min(count, 10),
        "Resources": DEFAULT_RESOURCES,
        **extra,
    })
    return [_normalize(i) for i in (data.get("SearchResult") or {}).get("Items", [])]


def _normalize(item: dict) -> dict:
    info = item.get("ItemInfo") or {}
    listing = ((item.get("Offers") or {}).get("Listings") or [{}])[0]
    price = (listing.get("Price") or {})
    return {
        "asin": item.get("ASIN", ""),
        "title": ((info.get("Title") or {}).get("DisplayValue") or ""),
        "brand": (((info.get("ByLineInfo") or {}).get("Brand") or {}).get("DisplayValue") or ""),
        "features": ((info.get("Features") or {}).get("DisplayValues") or []),
        "image": (((item.get("Images") or {}).get("Primary") or {}).get("Large") or {}).get("URL", ""),
        "price_amount": price.get("Amount"),
        "price_display": price.get("DisplayAmount", ""),
        "availability": ((listing.get("Availability") or {}).get("Message") or ""),
        "detail_url": item.get("DetailPageURL", ""),
    }
