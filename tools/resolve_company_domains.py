"""data/companies/*.json の企業名から Wikidata で公式サイトのドメイン(P856)を引き、
data/company_domains.json に書き出す。generate.py がこれを読んでロゴを表示する。

    python tools/resolve_company_domains.py          # 未解決の企業だけ検索
    python tools/resolve_company_domains.py --force   # 全件やり直す

DuckDuckGo検索のスクレイピングはブロック時に無関係な結果を誤って返すことがあり
信頼できなかったため、Wikidataの正規APIを使う（他プロジェクトで検証済み）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "companies"
OUT = ROOT / "data" / "company_domains.json"

HEADERS = {"User-Agent": "shukatsu-report/0.1 (company logo resolution; contact via repository)"}


def short_name(name: str) -> str:
    for x in ("株式会社", "(株)", "（株）"):
        name = name.replace(x, "")
    return name.strip()


JAPAN_QID = "Q17"


def _entity_is_japan_with_site(qid: str) -> str | None:
    """P17(国)が日本のentityだけを本物として扱い、公式サイト(P856)のドメインを返す。

    「カルビー」→イタリアの都市Carpi、「テルモ」→ギリシャの自治体、のように
    短い企業名は無関係なentityとラベルが被ることがあり、そちらに先にP856が
    付いているとそのまま誤って採用してしまう。国籍チェックで弾く。
    """
    er = requests.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json", headers=HEADERS, timeout=10)
    er.raise_for_status()
    claims = er.json()["entities"][qid].get("claims", {})
    p17 = claims.get("P17") or []
    is_japan = any(
        c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id") == JAPAN_QID for c in p17
    )
    if not is_japan:
        return None
    p856 = claims.get("P856")
    if not p856:
        return None
    url = p856[0]["mainsnak"]["datavalue"]["value"]
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


# descriptionにこれらの語を含む候補は会社そのものではない可能性が高いので弾く。
# 「日本郵船株式会社」→「日本郵船株式会社史料」（大学図書館のアーカイブentity）に
# 誤ってマッチした事例で発覚。P17（国）だけでは同じ日本のentityなら通ってしまう。
NON_COMPANY_HINTS = (
    "archive", "museum", "wikinews", "document", "given name", "family name",
    "novel", "album", "video game", "dialog by", "tragedy by", "comune", "municipality",
    "sports team", "baseball", "railway station",
)


def search_domain(query: str) -> str | None:
    r = requests.get(
        "https://www.wikidata.org/w/api.php",
        params={
            "action": "wbsearchentities", "search": query, "language": "ja",
            "type": "item", "limit": 5, "format": "json",
        },
        headers=HEADERS, timeout=10,
    )
    r.raise_for_status()
    for hit in r.json().get("search", []):
        qid = hit.get("id")
        if not qid:
            continue
        desc = (hit.get("description") or "").lower()
        if any(h in desc for h in NON_COMPANY_HINTS):
            continue
        try:
            domain = _entity_is_japan_with_site(qid)
        except Exception:  # noqa: BLE001  (1候補の取得失敗で他候補まで諦めない)
            continue
        if domain:
            return domain
    return None


def main() -> None:
    force = "--force" in sys.argv
    existing: dict[str, str | None] = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    companies = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(DATA.glob("*.json"))]

    for c in companies:
        code = c["edinet_code"]
        # null（未解決）は既存キーがあっても再挑戦する。レート制限等の一時的な
        # 失敗で null のまま固定されるのを防ぐため。既に解決済みのものだけ飛ばす。
        if not force and existing.get(code):
            continue
        name = c["name"]
        short = short_name(name)
        domain = None
        # 正式名称（「株式会社」つき）を先に試す。短縮名は同名の別entity
        # （例：「テルモ」→ギリシャの自治体、「日本ハム」→プロ野球チーム）に
        # 誤ってマッチし、無関係なP856を拾うことがあったため。
        for query in (name, short):
            try:
                domain = search_domain(query)
            except Exception as ex:  # noqa: BLE001
                print(f"  ! {short}: エラー ({ex})")
            if domain:
                break
            time.sleep(0.5)
        existing[code] = domain
        print(f"{'OK' if domain else '--'}  {short:28s} -> {domain}")
        OUT.write_text(json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        time.sleep(0.3)

    missing = [c["name"] for c in companies if not existing.get(c["edinet_code"])]
    print(f"\n{len(companies)}社中 {len(companies) - len(missing)}社でドメインを解決。未解決 {len(missing)}社:")
    for n in missing:
        print("  -", n)


if __name__ == "__main__":
    main()
