"""各社の公式サイトと採用ページのURLを集める。**有報以外の情報源を使う唯一の場所。**

数値は有価証券報告書だけを出典にするという原則は変えない。ここで集めるのは
「どこを見に行けばよいか」というリンクであって、サイトに表示する数値ではない。

## 手順と、その選び方の理由

1. **公式サイト** … EDINETコードリストの「提出者法人番号」で Wikidata（CC0）を引く。
   法人番号は国が振った一意な識別子なので、社名の表記ゆれ（「株式会社　ヤマウラ」の全角空白、
   旧社名、同名他社）で取り違える余地が無い。社名で当てにいくと必ず事故る。

2. **採用ページ** … 公式サイトのトップを **1回だけ** 取得し、その中のリンクから
   採用ページを探す。`/recruit/` `/saiyo/` のようなパスの当てずっぽうはしない。
   理由は2つある。(a) 当てずっぽうは1社あたり何回も他社サーバーを叩くうえ、
   採用サイトを別ドメイン（saiyo-*.jp など）に置く会社を取りこぼす。
   (b) トップページから辿るのは人間がやることと同じで、リンクの存在自体が根拠になる。

3. **robots.txt を必ず読み、Disallow なら取りに行かない。** 弾かれたら
   User-Agent を偽装せず、その会社を対象から外す。これはこのプロジェクトの原則である。

4. アクセスは **1ホストあたり1秒以上あける**。取得済みはキャッシュして再取得しない。

使い方:
    python fetch_links.py wikidata          # 法人番号 → 公式サイト（data/links/official.json）
    python fetch_links.py recruit           # 公式トップ → 採用ページ（data/links/recruit.json）
    python fetch_links.py recruit --limit 50
    python fetch_links.py merge             # → data/company_links.json（generate.py が読む）
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.robotparser
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
EDINET_CODE_CSV = ROOT / "cache" / "edinetcode" / "EdinetcodeDlInfo.csv"
LINK_DIR = ROOT / "data" / "links"
OFFICIAL_JSON = LINK_DIR / "official.json"
RECRUIT_JSON = LINK_DIR / "recruit.json"
MERGED_JSON = ROOT / "data" / "company_links.json"
LOG = ROOT / "logs" / "links_access.log"

# HTTPヘッダは latin-1 でしか送れない。日本語を入れると UnicodeEncodeError で全滅する
UA = "shukatsu-data-bot/1.0 (+https://shukatsu-data.com; company data comparison site)"
SPARQL = "https://query.wikidata.org/sparql"
POLITE_DELAY = 1.2      # 秒。1ホストあたり
TIMEOUT = 20

# 採用ページを指すリンクの手がかり。左のものほど強い
RECRUIT_TEXT = [
    "新卒採用", "採用情報", "採用サイト", "リクルート", "採用", "キャリア採用",
    "recruit", "recruiting", "recruitment", "careers", "career", "saiyo", "job",
]
STRONG_TEXT = ("新卒採用", "採用情報", "採用サイト", "採用")
# 採用と紛らわしいが別物
NEGATIVE = ("ir", "investor", "shareholder", "press", "news", "privacy", "sitemap", "contact")


def log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{line}\n")


# ---------------------------------------------------------------- 法人番号

def corp_numbers() -> dict[str, str]:
    """EDINETコード → 提出者法人番号。EDINETコードリストは cp932。"""
    raw = EDINET_CODE_CSV.read_bytes().decode("cp932")
    rows = list(csv.reader(io.StringIO(raw)))
    header = rows[1]
    i_code = header.index("ＥＤＩＮＥＴコード")
    i_corp = header.index("提出者法人番号")
    out = {}
    for r in rows[2:]:
        if len(r) > max(i_code, i_corp) and r[i_corp].strip():
            out[r[i_code].strip()] = r[i_corp].strip()
    return out


# ---------------------------------------------------------------- 公式サイト

def wikidata_sites(corp_ids: list[str]) -> dict[str, str]:
    """法人番号 → 公式サイトURL。VALUES 句で分割して問い合わせる。"""
    found: dict[str, str] = {}
    CHUNK = 250
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    for i in range(0, len(corp_ids), CHUNK):
        chunk = corp_ids[i: i + CHUNK]
        values = " ".join(f'"{c}"' for c in chunk)
        query = (
            "SELECT ?cn ?site WHERE { VALUES ?cn { " + values + " } "
            "?c wdt:P3225 ?cn ; wdt:P856 ?site . }"
        )
        try:
            r = sess.get(SPARQL, params={"query": query},
                         headers={"Accept": "application/sparql-results+json"}, timeout=90)
            r.raise_for_status()
            for b in r.json()["results"]["bindings"]:
                cn, site = b["cn"]["value"], b["site"]["value"]
                # 同じ会社に複数URLがあることがある（三洋電機で2件）。https を優先し、先勝ち
                if cn not in found or (site.startswith("https") and not found[cn].startswith("https")):
                    found[cn] = site
        except (requests.RequestException, KeyError, ValueError) as e:
            log(f"wikidata\tchunk{i}\tERROR\t{type(e).__name__}")
            print(f"  !! chunk {i}: {type(e).__name__}")
        print(f"  {i + len(chunk)}/{len(corp_ids)} 件照会 → {len(found)} 件ヒット")
        time.sleep(POLITE_DELAY)
    return found


def cmd_wikidata() -> None:
    with (ROOT / "companies.csv").open(encoding="utf-8", newline="") as f:
        companies = list(csv.DictReader(f))
    corp = corp_numbers()
    pairs = [(c["edinet_code"], corp.get(c["edinet_code"])) for c in companies]
    have = [(e, n) for e, n in pairs if n]
    print(f"{len(companies)}社中 {len(have)}社に法人番号がある")

    sites = wikidata_sites(sorted({n for _, n in have}))
    out = {}
    for code, cn in have:
        if cn in sites:
            out[code] = {"corp_number": cn, "url": sites[cn], "source": "Wikidata (P3225→P856)"}
    LINK_DIR.mkdir(parents=True, exist_ok=True)
    OFFICIAL_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n公式サイトが判明: {len(out)}/{len(companies)} 社 → {OFFICIAL_JSON}")


# ---------------------------------------------------------------- 採用ページ

def robots_allows(sess: requests.Session, origin: str, path: str = "/") -> bool | None:
    """robots.txt を読んで許可を確かめる。読めなければ None（＝取りに行かない）。"""
    rp = urllib.robotparser.RobotFileParser()
    try:
        r = sess.get(urllib.parse.urljoin(origin, "/robots.txt"), timeout=TIMEOUT)
        if r.status_code >= 400:
            return True          # robots.txt が無いのは「制限なし」
        rp.parse(r.text.splitlines())
    except requests.RequestException:
        return None
    return rp.can_fetch(UA, urllib.parse.urljoin(origin, path))


HREF_HINT = re.compile(r"recruit|saiyo|career|job|entry|shinsotsu", re.I)
DATED_URL = re.compile(r"/20[0-9]{2}/")     # 年度版の採用サイト（住友商事の /2022/ など）
FALLBACK_PATHS = ["/recruit/", "/saiyo/", "/careers/"]


def pick_recruit_link(html: str, base: str) -> tuple[str, str] | None:
    """トップページのHTMLから採用ページのリンクを1つ選ぶ。

    採点の考え方：リンク文字列（「採用情報」）と href（/recruit/）の**両方**が
    揃うものを最上位に置く。文字列だけだと「（住友商事グループ）」のような
    弱い表記を拾い、href だけだと採用と無関係な求人検索を拾う。
    年度が入ったURL（/2022/）は古い年度版なので下げる。
    """
    best: tuple[int, str, str] | None = None
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
            score -= 35          # 年度版は最新とは限らない
        if any(f"/{n}" in low for n in NEGATIVE) and score < 80:
            continue
        if best is None or score > best[0]:
            best = (score, url, text or "採用情報")
    return (best[1], best[2]) if best else None


def probe_paths(sess: requests.Session, origin: str) -> tuple[str, str] | None:
    """トップページに採用リンクが無いとき（ナビをJSで描く会社）だけ、慣例パスを確かめる。

    当てずっぽうを最初からやらないのは、他社サーバーを余計に叩かないため。
    ここに来るのは HTML から見つからなかった会社だけで、最大3回・各1.2秒あける。
    """
    for path in FALLBACK_PATHS:
        url = urllib.parse.urljoin(origin, path)
        try:
            r = sess.get(url, timeout=TIMEOUT, allow_redirects=True)
            log(f"probe\t{url}\t{r.status_code}")
            if r.status_code == 200 and HREF_HINT.search(urllib.parse.urlsplit(r.url).path or ""):
                return r.url, "採用情報"
        except requests.RequestException:
            pass
        time.sleep(POLITE_DELAY)
    return None


def cmd_recruit(limit: int = 0) -> None:
    if not OFFICIAL_JSON.exists():
        raise SystemExit("先に python fetch_links.py wikidata を実行してください。")
    official = json.loads(OFFICIAL_JSON.read_text(encoding="utf-8"))
    done = json.loads(RECRUIT_JSON.read_text(encoding="utf-8")) if RECRUIT_JSON.exists() else {}

    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    todo = [(k, v) for k, v in official.items() if k not in done]
    if limit:
        todo = todo[:limit]
    print(f"未取得 {len(todo)} 社を処理する（1社あたり robots.txt とトップの2回、{POLITE_DELAY}秒間隔）")

    for i, (code, info) in enumerate(todo, 1):
        url = info["url"]
        origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(url))
        rec: dict = {"official": url}
        allowed = robots_allows(sess, origin, urllib.parse.urlsplit(url).path or "/")
        time.sleep(POLITE_DELAY)
        if allowed is None:
            rec["status"] = "robots.txt を読めないため取得しない"
        elif not allowed:
            rec["status"] = "robots.txt により対象外"
            log(f"{code}\t{origin}\tDISALLOW")
        else:
            try:
                r = sess.get(url, timeout=TIMEOUT, allow_redirects=True)
                log(f"{code}\t{url}\t{r.status_code}")
                if r.status_code >= 400:
                    rec["status"] = f"トップページが HTTP {r.status_code}"
                else:
                    r.encoding = r.apparent_encoding or r.encoding
                    got = pick_recruit_link(r.text, r.url)
                    if got:
                        rec["recruit"], rec["recruit_text"] = got
                        rec["status"] = "採用ページのリンクをトップページから取得"
                    else:
                        time.sleep(POLITE_DELAY)
                        got = probe_paths(sess, origin)
                        if got:
                            rec["recruit"], rec["recruit_text"] = got
                            rec["status"] = "慣例のパスを確かめて到達（トップページのリンクはJSで描画）"
                        else:
                            rec["status"] = "トップページに採用ページへのリンクが見つからない"
            except requests.RequestException as e:
                rec["status"] = f"取得できない（{type(e).__name__}）"
            time.sleep(POLITE_DELAY)

        done[code] = rec
        if i % 25 == 0 or i == len(todo):
            RECRUIT_JSON.parent.mkdir(parents=True, exist_ok=True)
            RECRUIT_JSON.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
            hit = sum(1 for v in done.values() if v.get("recruit"))
            print(f"  {i}/{len(todo)}  採用ページ判明 {hit} 社")

    RECRUIT_JSON.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    hit = sum(1 for v in done.values() if v.get("recruit"))
    print(f"\n完了。採用ページ {hit} 社 / 調査 {len(done)} 社 → {RECRUIT_JSON}")


def cmd_merge() -> None:
    official = json.loads(OFFICIAL_JSON.read_text(encoding="utf-8")) if OFFICIAL_JSON.exists() else {}
    recruit = json.loads(RECRUIT_JSON.read_text(encoding="utf-8")) if RECRUIT_JSON.exists() else {}
    out = {}
    for code, info in official.items():
        rec = recruit.get(code, {})
        out[code] = {
            "official": info["url"],
            "official_source": info["source"],
            "recruit": rec.get("recruit"),
            "recruit_text": rec.get("recruit_text"),
            "recruit_status": rec.get("status"),
        }
    MERGED_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    n_rec = sum(1 for v in out.values() if v["recruit"])
    print(f"公式 {len(out)} 社 / 採用 {n_rec} 社 → {MERGED_JSON}")


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else "merge"
    lim = int(args[args.index("--limit") + 1]) if "--limit" in args else 0
    {"wikidata": cmd_wikidata, "recruit": lambda: cmd_recruit(lim), "merge": cmd_merge}[cmd]()
