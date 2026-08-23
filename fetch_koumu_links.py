"""官公庁・政府系126機関（data/koumu/）の公式サイトから、ロゴ用アイコンと採用ページの
候補リンクを集める。

fetch_links.py の企業版と全く同じ考え方・同じ関数を使う。違うのは公式サイトURLの特定方法だけ：
企業側は法人番号でWikidataを突合して自動特定するが、省庁は法人番号を持たず、独立行政法人は
名称の表記ゆれ（略称・旧称・カッコ書き）が大きく自動突合は事故りやすいため、今回は
data/links/koumu_official.json（機関ごとに人手で確認したURL）を入力にする。

アイコン探索そのもの（robots.txt厳守・UA偽装なし・1ホスト1.2秒間隔・同一オリジンのみ・
SVG/apple-touch-icon優遇・実在と解像度の確認）は fetch_links.py の
robots_allows() / find_icon() をそのまま import して使う。ロジックの二重管理を避けるため。

採用ページは企業側のように pick_recruit_link() で1件に断定しない。省庁・独立行政法人の
サイトは「採用」の表記が企業サイトほど定型的でなく、正規表現の1位判定だけでは誤検出・
取りこぼしの懸念があるため、スコア>0の候補を最大10件そのまま残し（candidate_links()）、
最終判断はGemini（AI Studioへの手動バッチ）に委ねる。判断材料の抽出だけ、企業側と同じ
正規表現（HREF_HINT・RECRUIT_TEXT等）を再利用する。

使い方:
    python fetch_koumu_links.py            # data/links/koumu_official.json → data/links/koumu_icons.json
    python fetch_koumu_links.py --limit 10 # お試し実行
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

from fetch_links import (
    DATED_URL,
    HREF_HINT,
    NEGATIVE,
    POLITE_DELAY,
    RECRUIT_TEXT,
    STRONG_TEXT,
    TIMEOUT,
    UA,
    find_icon,
    log,
    robots_allows,
)

ROOT = Path(__file__).resolve().parent
OFFICIAL_JSON = ROOT / "data" / "links" / "koumu_official.json"
OUT_JSON = ROOT / "data" / "links" / "koumu_icons.json"

PERMANENT_FAIL = ("robots.txt により対象外",)


def candidate_links(html: str, base: str, limit: int = 10) -> list[dict]:
    """採用ページの候補を最大 limit 件、スコア降順で返す（Geminiに判断させるための下ごしらえ）。

    pick_recruit_link() と全く同じ採点ロジック（fetch_links.py の正規表現をそのまま使う）だが、
    1件に絞らず候補を残す。ここでは決めない。
    """
    scored: list[tuple[int, str, str]] = []
    for m in re.finditer(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html, re.S | re.I):
        href, inner = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        text = re.sub(r"\s+", "", inner)[:40]
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        url = urllib.parse.urljoin(base, href)
        if not url.startswith(("http://", "https://")):
            continue
        low = url.lower()

        text_score = 0
        for t in STRONG_TEXT:
            if t in text:
                text_score = max(text_score, 60 - STRONG_TEXT.index(t))
        if text_score == 0:
            for t in RECRUIT_TEXT:
                if t in text.lower():
                    text_score = max(text_score, 40 - RECRUIT_TEXT.index(t))
        href_score = 40 if HREF_HINT.search(low) else 0
        score = text_score + href_score
        if score == 0:
            continue
        if DATED_URL.search(low):
            score -= 35
        if any(f"/{n}" in low for n in NEGATIVE) and score < 80:
            continue
        scored.append((score, url, text or "採用情報"))

    seen: set[str] = set()
    out: list[dict] = []
    for score, url, text in sorted(scored, key=lambda x: -x[0]):
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "text": text})
        if len(out) >= limit:
            break
    return out


def _is_incomplete(rec: dict) -> bool:
    status = rec.get("status", "")
    if status in PERMANENT_FAIL or "HTTP 404" in status:
        return False
    return "icon" not in rec or "candidates" not in rec


def cmd_fetch(limit: int = 0) -> None:
    if not OFFICIAL_JSON.exists():
        raise SystemExit(f"{OFFICIAL_JSON} が無い。先に公式サイトURL一覧を用意してください。")
    official: dict[str, str | None] = json.loads(OFFICIAL_JSON.read_text(encoding="utf-8"))
    done = json.loads(OUT_JSON.read_text(encoding="utf-8")) if OUT_JSON.exists() else {}

    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    todo = [
        (slug, url) for slug, url in official.items()
        if url and (slug not in done or _is_incomplete(done[slug]))
    ]
    if limit:
        todo = todo[:limit]
    print(f"処理対象 {len(todo)} 機関（{POLITE_DELAY}秒間隔）")

    for i, (slug, url) in enumerate(todo, 1):
        origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(url))
        rec: dict = {"official": url}
        allowed = robots_allows(sess, origin, urllib.parse.urlsplit(url).path or "/")
        time.sleep(POLITE_DELAY)
        if allowed is None:
            rec["status"] = "robots.txt を読めないため取得しない"
        elif not allowed:
            rec["status"] = "robots.txt により対象外"
            log(f"koumu:{slug}\t{origin}\tDISALLOW")
        else:
            try:
                r = sess.get(url, timeout=TIMEOUT, allow_redirects=True)
                log(f"koumu:{slug}\t{url}\t{r.status_code}")
                if r.status_code >= 400:
                    rec["status"] = f"トップページが HTTP {r.status_code}"
                else:
                    r.encoding = r.apparent_encoding or r.encoding
                    time.sleep(POLITE_DELAY)
                    rec["icon"] = find_icon(sess, r.text, origin, r.url)
                    rec["candidates"] = candidate_links(r.text, r.url)
                    rec["status"] = "取得済み" if rec["icon"] else "アイコンが見つからない"
            except requests.RequestException as e:
                rec["status"] = f"取得できない（{type(e).__name__}）"
            time.sleep(POLITE_DELAY)

        done[slug] = rec
        if i % 20 == 0 or i == len(todo):
            OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
            OUT_JSON.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
            hit = sum(1 for v in done.values() if v.get("icon"))
            print(f"  {i}/{len(todo)}  アイコン判明 {hit} 機関")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    hit = sum(1 for v in done.values() if v.get("icon"))
    print(f"\n完了。アイコン判明 {hit} 機関 / 対象 {len(done)} 機関 → {OUT_JSON}")


if __name__ == "__main__":
    args = sys.argv[1:]
    lim = int(args[args.index("--limit") + 1]) if "--limit" in args else 0
    cmd_fetch(lim)
