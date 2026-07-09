"""各大学サイトの robots.txt を読み、就職実績ページを取得してよいか判定する。

1ドメインにつき robots.txt を1回だけ取得する。2秒間隔。
結果は data/universities/robots_check.csv に残す（アクセスログの一部）。

判定に使う User-Agent は本番のクローラと同一でなければ意味がない。
"""

from __future__ import annotations

import csv
import datetime as dt
import sys
import time
import urllib.robotparser
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "universities" / "robots_check.csv"

# 本番クローラと同じ名乗り。連絡先は .env の CONTACT_EMAIL で差し替える
USER_AGENT = "shukatsu-report-bot/0.1 (+research use; contact: CONTACT_EMAIL)"

INTERVAL_SEC = 2.0

UNIVERSITIES = [
    ("東京大学", "https://www.u-tokyo.ac.jp"),
    ("京都大学", "https://www.kyoto-u.ac.jp"),
    ("一橋大学", "https://www.hit-u.ac.jp"),
    ("東京科学大学", "https://www.isct.ac.jp"),  # 旧 東京工業大学（2024年10月統合）
    ("大阪大学", "https://www.osaka-u.ac.jp"),
    ("名古屋大学", "https://www.nagoya-u.ac.jp"),
    ("東北大学", "https://www.tohoku.ac.jp"),
    ("九州大学", "https://www.kyushu-u.ac.jp"),
    ("北海道大学", "https://www.hokudai.ac.jp"),
    ("神戸大学", "https://www.kobe-u.ac.jp"),
    # 早稲田大学 (www.waseda.jp) は除外。
    #   Cloudflare が /robots.txt に対し bot・ブラウザ双方のUAで 403 を返す＝
    #   プログラムからのアクセスを拒否している。回避せず対象外とする。
    # 同志社大学 (www.doshisha.ac.jp) は除外。
    #   WAF(awselb) が bot を名乗るUAにのみ 403 を返す（ブラウザUAでは404）。
    #   ブラウザを偽装すれば通るが、それはアクセス制御の回避にあたる。名乗りは偽らない。
    # いずれも、許諾を得られれば復帰させる。
    ("慶應義塾大学", "https://www.keio.ac.jp"),
    ("上智大学", "https://www.sophia.ac.jp"),
    ("明治大学", "https://www.meiji.ac.jp"),
    ("青山学院大学", "https://www.aoyama.ac.jp"),
    ("立教大学", "https://www.rikkyo.ac.jp"),
    ("中央大学", "https://www.chuo-u.ac.jp"),
    ("法政大学", "https://www.hosei.ac.jp"),
    ("立命館大学", "https://www.ritsumei.ac.jp"),
    ("関西学院大学", "https://www.kwansei.ac.jp"),
    ("関西大学", "https://www.kansai-u.ac.jp"),
]

# 就職実績ページが置かれていそうなパス（robots.txt の判定用。実在確認はしない）
PROBE_PATHS = ["/", "/career/", "/about/disclosure/", "/nyushi/", "/campus/career/"]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, (name, origin) in enumerate(UNIVERSITIES):
        if i:
            time.sleep(INTERVAL_SEC)
        robots_url = f"{origin}/robots.txt"
        row = {"university": name, "origin": origin, "checked_at": dt.date.today().isoformat()}
        try:
            r = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=30)
            row["http_status"] = r.status_code
            if r.status_code == 200 and "text" in r.headers.get("content-type", ""):
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(r.text.splitlines())
                row["crawl_delay"] = rp.crawl_delay(USER_AGENT) or rp.crawl_delay("*") or ""
                allowed = {p: rp.can_fetch(USER_AGENT, origin + p) for p in PROBE_PATHS}
                row["allowed_root"] = allowed["/"]
                row["disallowed_paths"] = ",".join(p for p, ok in allowed.items() if not ok)
                row["has_sitemap"] = "Sitemap" in r.text
                row["robots_bytes"] = len(r.text)
            else:
                # robots.txt が無い（404）＝ 明示的な拒否がない
                row["crawl_delay"] = ""
                row["allowed_root"] = r.status_code == 404
                row["disallowed_paths"] = ""
                row["has_sitemap"] = False
                row["robots_bytes"] = 0
        except Exception as e:  # noqa: BLE001
            row["http_status"] = "ERROR"
            row["error"] = str(e)[:120]
            row["allowed_root"] = False
        rows.append(row)
        print(
            f"{name:8s} http={row.get('http_status')!s:>5}  "
            f"root_allowed={row.get('allowed_root')}  "
            f"crawl_delay={row.get('crawl_delay') or '-'}  "
            f"disallow={row.get('disallowed_paths') or '-'}",
            flush=True,
        )

    fields = [
        "university", "origin", "checked_at", "http_status", "allowed_root",
        "crawl_delay", "disallowed_paths", "has_sitemap", "robots_bytes", "error",
    ]
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\n→ {OUT}")

    blocked = [r["university"] for r in rows if not r.get("allowed_root")]
    if blocked:
        print(f"\nルートが不許可またはエラー（対象から外す候補）: {', '.join(blocked)}")


if __name__ == "__main__":
    sys.exit(main())
