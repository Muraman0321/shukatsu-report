"""大学の就職実績ページを1件ずつ調べ、「企業別の人数」が載っているかを機械判定する。

スクレイパーを書く前の調査。ここで粒度を確かめないと、動くけど何も取れないコードができる。

判定
----
- 企業名（companies.csv の27社＋主要事業会社）が本文に現れるか
- その企業名の近傍（前後30字以内）に人数らしき整数があるか
  → あれば「企業別人数あり」、企業名だけなら「社名のみ（人数なし）」

守ること
--------
- robots.txt を読み、禁止されていれば取得しない
- 1リクエスト/2秒以上、User-Agent に連絡先
- アクセスログを logs/university_access.log に残す

    python tools/probe_university.py 東京大学 https://example.ac.jp/career.pdf
    python tools/probe_university.py --list data/universities/candidates.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import os
import re
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import fitz
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "university_access.log"
OUT = ROOT / "data" / "universities" / "probe_results.csv"

INTERVAL_SEC = 2.0
_last = 0.0

# 判定に使う企業名。表記ゆれを含める
COMPANY_NAMES = [
    "三菱商事", "三井物産", "伊藤忠商事", "住友商事", "丸紅", "双日", "豊田通商",
    "三菱ＵＦＪ銀行", "三菱UFJ銀行", "三井住友銀行", "みずほ銀行",
    "東京海上日動", "トヨタ自動車", "ソニー", "日立製作所", "キーエンス",
    "三井不動産", "三菱地所", "ＫＤＤＩ", "KDDI", "野村證券", "アクセンチュア",
]
# 「三菱商事 12」「三菱商事(5)」「三菱商事…5名」を拾う
NUM_NEAR = re.compile(r"[（(\[]?\s*(\d{1,3})\s*[)）\]名人]?")


def _ua() -> str:
    load_dotenv(ROOT / ".env")
    contact = os.getenv("CONTACT_EMAIL", "").strip() or "no-contact-configured"
    return f"shukatsu-report-bot/0.1 (+research use; contact: {contact})"


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')}\t{msg}\n")


def _sleep() -> None:
    global _last
    wait = INTERVAL_SEC - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()


def robots_allows(url: str, ua: str) -> tuple[bool, str]:
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    _sleep()
    try:
        r = requests.get(urljoin(origin, "/robots.txt"), headers={"User-Agent": ua}, timeout=25)
    except Exception as e:  # noqa: BLE001
        return False, f"robots取得エラー: {e}"
    _log(f"GET\t{origin}/robots.txt\thttp={r.status_code}")
    if r.status_code == 403:
        # bot を名乗ると弾かれるサイト。UAを偽装して回避はしない
        return False, "robots.txtが403（botのアクセスを拒否）"
    if r.status_code != 200 or "text" not in r.headers.get("content-type", ""):
        return True, "robots.txtなし（明示的な拒否なし）"
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(r.text.splitlines())
    if not rp.can_fetch(ua, url):
        return False, "robots.txt が Disallow"
    delay = rp.crawl_delay(ua) or rp.crawl_delay("*")
    return True, f"許可（crawl-delay={delay or '未指定'}）"


def fetch_text(url: str, ua: str) -> tuple[str, str]:
    _sleep()
    r = requests.get(url, headers={"User-Agent": ua}, timeout=60)
    _log(f"GET\t{url}\thttp={r.status_code}\tbytes={len(r.content)}")
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        with fitz.open(stream=r.content, filetype="pdf") as d:
            return "".join(p.get_text() for p in d), "PDF"
    soup = BeautifulSoup(r.text, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n"), "HTML"


def classify(text: str) -> tuple[str, list[str]]:
    flat = re.sub(r"[ 　\t]+", "", text)
    found, with_num = [], []
    for name in COMPANY_NAMES:
        for m in re.finditer(re.escape(name), flat):
            found.append(name)
            tail = flat[m.end() : m.end() + 30]
            head = flat[max(0, m.start() - 12) : m.start()]
            if NUM_NEAR.match(tail.lstrip("\n")) or re.search(r"\d{1,3}[名人]$", head):
                with_num.append(name)
            break
    found = sorted(set(found))
    with_num = sorted(set(with_num))
    if not found:
        verdict = "企業名が出てこない（業種別集計のみ等）"
    elif len(with_num) >= 3:
        verdict = "企業別人数あり"
    elif with_num:
        verdict = "人数つきが少数（要目視）"
    else:
        verdict = "社名のみ（人数なし）"
    return verdict, with_num or found


def probe(university: str, url: str, ua: str) -> dict:
    row = {"university": university, "url": url, "checked_at": dt.date.today().isoformat()}
    ok, why = robots_allows(url, ua)
    row["robots"] = why
    if not ok:
        row["verdict"] = "取得しない（robots）"
        return row
    try:
        text, fmt = fetch_text(url, ua)
    except Exception as e:  # noqa: BLE001
        row["verdict"] = f"取得失敗: {str(e)[:60]}"
        return row
    row["format"] = fmt
    row["chars"] = len(text)
    row["verdict"], hits = classify(text)
    row["hits"] = ", ".join(hits[:8])
    return row


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("university", nargs="?")
    p.add_argument("url", nargs="?")
    p.add_argument("--list", help="university,url の2列CSV")
    args = p.parse_args()

    ua = _ua()
    targets = []
    if args.list:
        with open(args.list, encoding="utf-8", newline="") as f:
            targets = [(r["university"], r["url"]) for r in csv.DictReader(f)]
    elif args.university and args.url:
        targets = [(args.university, args.url)]
    else:
        raise SystemExit("引数か --list が必要")

    rows = []
    for uni, url in targets:
        row = probe(uni, url, ua)
        rows.append(row)
        print(f"{uni:12s} {row['verdict']:24s} {row.get('hits','')[:60]}")
        print(f"             robots: {row['robots']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["university", "url", "checked_at", "robots", "format", "chars", "verdict", "hits"]
    write_header = not OUT.exists()
    with OUT.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerows(rows)
    print(f"\n→ {OUT}")


if __name__ == "__main__":
    main()
