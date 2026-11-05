"""Datasheet links from distributor search APIs: Mouser Search API and DigiKey Product Information v4.

Both need the user's own key, kept in the OS credential store (or an environment variable), like
the AI keys: ``MOUSER_API_KEY``; ``DIGIKEY_CLIENT_ID`` + ``DIGIKEY_CLIENT_SECRET``. What is sent is
the part number only. The answer is a list of candidates (datasheet URL, product page, lifecycle
status); nothing is downloaded here.

Request and response shapes follow the vendors' published specifications (read 2026-11-05):

* Mouser: ``POST https://api.mouser.com/api/v1/search/partnumber?apiKey=…`` with
  ``{"SearchByPartRequest": {"mouserPartNumber": "<part>", "partSearchOptions": "None"}}``; the answer is
  ``{"Errors": [...], "SearchResults": {"NumberOfResult": n, "Parts": [MouserPart]}}`` where a part
  has ``ManufacturerPartNumber``, ``Manufacturer``, ``DataSheetUrl``, ``ProductDetailUrl``,
  ``Description``, ``LifecycleStatus``.
* DigiKey: a 2-legged OAuth token from ``POST https://api.digikey.com/v1/oauth2/token``
  (form ``client_id``, ``client_secret``, ``grant_type=client_credentials``; tokens last 10 minutes), then
  ``POST https://api.digikey.com/products/v4/search/keyword`` with the headers ``X-DIGIKEY-Client-Id``
  and ``Authorization: Bearer`` and ``{"Keywords": "<part>", "Limit": 10}``; the answer has
  ``ExactMatches`` and ``Products`` with ``ManufacturerProductNumber``, ``Manufacturer.Name``,
  ``DatasheetUrl``, ``ProductUrl``, ``Description.ProductDescription``, ``ProductStatus.Status``.
"""

from __future__ import annotations

import threading
import time

import httpx

from circuit_core.datasheet_sources import DatasheetCandidate, is_public_url, make_candidate
from circuit_core.datasheets import normalize_part

from .datasheets import DatasheetError

MOUSER_BASE = "https://api.mouser.com"
DIGIKEY_BASE = "https://api.digikey.com"
KEY_NAMES = {"mouser": ("mouser",), "digikey": ("digikey-id", "digikey-secret")}
"""Names in the key store (and ``ENV_VARS`` in ``ai_engine.keystore``)."""
TIMEOUT_S = 20.0


def _https(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    return url if is_public_url(url) else None


def _matches(part: str | None, wanted: str) -> int:
    """2 = same part number, 1 = the answer is a longer orderable number of the one asked, 0 = other part."""
    got, want = normalize_part(part), normalize_part(wanted)
    if not got or not want:
        return 0
    if got == want:
        return 2
    return 1 if got.startswith(want) else 0


def _http_error(name: str, status: int, detail: str) -> DatasheetError:
    if status in (401, 403):
        return DatasheetError("SEARCH_AUTH_FAILED", f"{name} 키가 거부되었습니다 ({status}): {detail}",
                              "설정 › 데이터시트 검색에서 키를 확인하세요.", status=502)
    if status == 429:
        return DatasheetError("SEARCH_QUOTA", f"{name} 검색 한도를 넘었습니다: {detail}",
                              "잠시 후 다시 시도하세요. 하루 호출 수 제한이 있을 수 있습니다.", status=502)
    return DatasheetError("SEARCH_FAILED", f"{name} 검색이 실패했습니다 ({status}): {detail}",
                          "잠시 후 다시 시도하세요.", status=502)


class MouserSearch:
    name = "Mouser"

    def __init__(self, api_key: str, *, base: str = MOUSER_BASE, http: httpx.Client | None = None):
        self.api_key, self.base = api_key, base.rstrip("/")
        self.http = http or httpx.Client(timeout=TIMEOUT_S)

    def search(self, part: str) -> list[DatasheetCandidate]:
        try:
            r = self.http.post(f"{self.base}/api/v1/search/partnumber", params={"apiKey": self.api_key},
                               json={"SearchByPartRequest": {"mouserPartNumber": part, "partSearchOptions": "None"}})
        except httpx.HTTPError as exc:
            raise DatasheetError("SEARCH_UNREACHABLE", f"Mouser에 연결하지 못했습니다: {exc}",
                                 "인터넷 연결이나 회사 방화벽을 확인하세요.", status=502) from exc
        if r.status_code != 200:
            raise _http_error(self.name, r.status_code, r.text[:200])
        body = r.json()
        errors = body.get("Errors") or []
        if errors:
            text = "; ".join(str(e.get("Message") or e.get("Code") or e) for e in errors)[:200]
            # The spec gives no error codes; a bad key is recognised by its words (the key is a GUID, so .NET
            # answers "unique identifier"). Anything else is reported as a failed search.
            words = " ".join(f"{e.get('Code', '')} {e.get('Message', '')}" for e in errors).lower()
            status = 401 if any(w in words for w in ("key", "identifier", "unauthorized", "forbidden")) else 400
            raise _http_error(self.name, status, text)
        parts = ((body.get("SearchResults") or {}).get("Parts")) or []
        out = []
        for p in sorted(parts, key=lambda p: -_matches(p.get("ManufacturerPartNumber"), part)):
            if not _matches(p.get("ManufacturerPartNumber"), part):
                continue
            url = _https(p.get("DataSheetUrl"))
            if not url:
                continue
            out.append(make_candidate(
                "mouser", "Mouser 검색", [url], manufacturer=p.get("Manufacturer"),
                part_number=p.get("ManufacturerPartNumber"), description=p.get("Description"),
                product_url=_https(p.get("ProductDetailUrl")), status=p.get("LifecycleStatus") or None))
        return _unique(out)


class DigiKeySearch:
    name = "DigiKey"

    def __init__(self, client_id: str, client_secret: str, *, base: str = DIGIKEY_BASE,
                 http: httpx.Client | None = None):
        self.client_id, self.client_secret, self.base = client_id, client_secret, base.rstrip("/")
        self.http = http or httpx.Client(timeout=TIMEOUT_S)
        self._token: tuple[str, float] | None = None
        self._lock = threading.Lock()

    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token[1]:
                return self._token[0]
            try:
                r = self.http.post(f"{self.base}/v1/oauth2/token", data={
                    "client_id": self.client_id, "client_secret": self.client_secret,
                    "grant_type": "client_credentials"})
            except httpx.HTTPError as exc:
                raise DatasheetError("SEARCH_UNREACHABLE", f"DigiKey에 연결하지 못했습니다: {exc}",
                                     "인터넷 연결이나 회사 방화벽을 확인하세요.", status=502) from exc
            if r.status_code != 200:
                raise _http_error(self.name, 401 if r.status_code in (400, 401) else r.status_code, r.text[:200])
            body = r.json()
            self._token = (body["access_token"], time.time() + max(int(body.get("expires_in") or 600) - 30, 30))
            return self._token[0]

    def search(self, part: str) -> list[DatasheetCandidate]:
        headers = {"X-DIGIKEY-Client-Id": self.client_id, "Authorization": f"Bearer {self._access_token()}",
                   "X-DIGIKEY-Locale-Language": "en", "X-DIGIKEY-Locale-Currency": "USD",
                   "X-DIGIKEY-Locale-Site": "US"}
        try:
            r = self.http.post(f"{self.base}/products/v4/search/keyword", headers=headers,
                               json={"Keywords": part, "Limit": 10})
        except httpx.HTTPError as exc:
            raise DatasheetError("SEARCH_UNREACHABLE", f"DigiKey에 연결하지 못했습니다: {exc}",
                                 "인터넷 연결이나 회사 방화벽을 확인하세요.", status=502) from exc
        if r.status_code == 401:
            with self._lock:
                self._token = None
        if r.status_code != 200:
            detail = r.text[:200]
            try:
                problem = r.json()
                detail = problem.get("detail") or problem.get("title") or detail
            except ValueError:
                pass
            raise _http_error(self.name, r.status_code, str(detail))
        body = r.json()
        products = list(body.get("ExactMatches") or []) + list(body.get("Products") or [])
        out = []
        for p in sorted(products, key=lambda p: -_matches(p.get("ManufacturerProductNumber"), part)):
            if not _matches(p.get("ManufacturerProductNumber"), part):
                continue
            url = _https(p.get("DatasheetUrl"))
            if not url:
                continue
            status = (p.get("ProductStatus") or {}).get("Status")
            if p.get("Discontinued") and not status:
                status = "Discontinued"
            out.append(make_candidate(
                "digikey", "DigiKey 검색", [url], manufacturer=(p.get("Manufacturer") or {}).get("Name"),
                part_number=p.get("ManufacturerProductNumber"),
                description=(p.get("Description") or {}).get("ProductDescription"),
                product_url=_https(p.get("ProductUrl")), status=status))
        return _unique(out)


def _unique(cands: list[DatasheetCandidate], limit: int = 3) -> list[DatasheetCandidate]:
    """One candidate per datasheet URL, best match first, at most ``limit``."""
    seen, out = set(), []
    for c in cands:
        if c.urls[0] in seen:
            continue
        seen.add(c.urls[0])
        out.append(c)
    return out[:limit]


def key_status(keystore) -> dict:
    """Which distributors have keys (values are never returned)."""
    out = {}
    for name, keys in KEY_NAMES.items():
        found = [keystore.get(k) for k in keys]
        sources = {s for _, s in found if s}
        out[name] = {"configured": all(v for v, _ in found), "source": sources.pop() if len(sources) == 1 else
                     ("mixed" if sources else None)}
    return {**out, "keyring_available": keystore.available}


def searchers(keystore, *, mouser_base: str = MOUSER_BASE, digikey_base: str = DIGIKEY_BASE,
              http: httpx.Client | None = None) -> list:
    out = []
    mouser, _ = keystore.get("mouser")
    if mouser:
        out.append(MouserSearch(mouser, base=mouser_base, http=http))
    dk_id, _ = keystore.get("digikey-id")
    dk_secret, _ = keystore.get("digikey-secret")
    if dk_id and dk_secret:
        out.append(DigiKeySearch(dk_id, dk_secret, base=digikey_base, http=http))
    return out
