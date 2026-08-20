"""新卒の採用枠（コース名・対象学部・職種など）を読むため、採用サイトを数階層だけ深追いする。

`fetch_links.py` が集めるのは「採用ページのURL」までで、中身は読んでいない。
実際に3社のトップページを見た実測では、**採用トップは中身が薄いランディングページ**で、
「新卒採用」「職種一覧」「コース紹介」のような一段深いページに採用枠の説明がある。
一方で一部のサイトはJSで描画されるため、素朴な HTML 取得ではその一段深い中身すら空になる。

このファイルがやるのは「テキストを集めるところまで」。**採用枠を構造化するのはこのファイルの
仕事ではない**（数値と違って自然文の要約・分類なので、機械抽出ではなくセッションが直接読んで書く。
write_prose.py が事業内容の要約をAPIなしで直接書いたのと同じ理由）。

## 深追いの方法（当てずっぽうをしない・BFSで最大 MAX_HOPS 階層）
1. 採用トップページを1回取得する（1層目）
2. ページ内のリンクを見て、**リンク文字列**に「新卒」「職種」「コース」「募集要項」
   「仕事内容」「求める人物像」などが含まれるものだけを候補にする（href の当てずっぽうはしない）
3. 同一ホスト内のリンクだけを辿る。候補をスコア順に追加取得し、**取得したページ自身のリンクも
   同じ基準でさらに候補にする**（2層目・3層目）。「募集要項」ページは1層目のトップから
   直接リンクされているとは限らず、`採用トップ→新卒採用→募集要項`のように2〜3階層先にある
   会社が多いため（2026-08-20、実測で歩留まりを確認して深追いに変更）
4. 全社で合計 MAX_TOTAL_PAGES 件まで（1ホストへの負荷を無限に増やさないための上限）
5. robots.txt は fetch_links.py と同じ関数で確認し、Disallow なら取りに行かない
6. 集めた全ページのプレーンテキストが THIN_CHARS 未満なら「動的サイトの疑い・情報薄い」として
   status を thin にする（採用枠は書かず、リンクだけ出す判断はこの後の工程がする）
7. 言語は問わない。日本人の新卒採用が対象である限り、英語ページ（グローバル採用サイトの
   日本向けセクションなど）に有用な情報があれば読む対象にする

アクセスは同一ホストへの連続リクエストで POLITE_DELAY 秒あける（リクエストごとに1回。
以前は前後2回sleepしていて実質2.4秒空いていたが、階層を深くした分の総リクエスト数増加を
吸収するため1回に統一した。1ホスト1.2秒以上の間隔という制約自体は変えていない）。
取得済み（data/saiyo_raw/{code}.json が存在する）会社は --force を付けない限りスキップする＝差分実行。

使い方:
    python fetch_saiyo.py                 # data/saiyo_pilot.json の全社
    python fetch_saiyo.py --limit 10       # 先頭10社だけ（動作確認用）
    python fetch_saiyo.py --force          # 取得済みも含め全社やり直す
    python fetch_saiyo.py --pilot data/x.json   # 対象企業リストを差し替える（再挑戦分だけ絞る等）
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_links import UA, POLITE_DELAY, TIMEOUT, robots_allows, log  # noqa: E402

ROOT = Path(__file__).resolve().parent
PILOT_JSON = ROOT / "data" / "saiyo_pilot.json"
RAW_DIR = ROOT / "data" / "saiyo_raw"
LOG = ROOT / "logs" / "saiyo_access.log"

MAX_HOPS = 3              # トップから何階層先まで追うか（トップ自身は0層目）
MAX_TOTAL_PAGES = 12      # トップ込みで1社あたり取得するページ数の上限
MAX_CHARS_PER_PAGE = 9000  # 1ページぶんのプレーンテキストの上限
THIN_CHARS = 400          # 全ページ合計でこれ未満なら「情報薄い」扱い

FOLLOW_HINT = re.compile(
    "新卒|職種|コース|募集要項|仕事内容|求める人物|採用の流れ|選考|エントリー|よくある質問|初任給|待遇|"
    "course|job|position|entry|faq|shinsotsu"
)
SKIP_HINT = re.compile(
    "中途|キャリア採用|障が|インターン一覧|プライバシー|個人情報|お問い合わせ|ir|"
    "投資家|インタビュー|社員の?声|先輩|ストーリー|english|privacy|contact|interview|voice|story",
    re.I,
)
SKIP_URL = re.compile(
    r"/(interview|voice|story|people)/|"
    # axol.jp・i-webs.jp等のATS特有の会員規約・ログインページ。内容は定型の利用規約でクロール予算の無駄
    # （大垣共立銀行・群馬銀行・紀陽銀行で実測、2026-08-20追加）
    r"/(entry/agreement|agreement|login)(/|\?|$)", re.I)
# 上のSKIP_HINTは日本語込みなので大文字小文字を気にしない部分は別途 lower() で比較する。
# 「よくある質問」はFOLLOW_HINTと重複していたSKIP_HINT側から2026-08-20に削除
# （FAQページに選考時期・待遇の記述があることがあり、読む価値があるため）。
# 同日、STRONG_POSITIVEを新設：カード型UIでリンク文字列に説明文がくっつき
# 「新卒・キャリアの求人情報を紹介します」のような長い文になる会社が多く実測された。
# 「キャリア」「インタビュー」語だけでSKIP_HINTに引っかかって除外されるのを防ぐため、
# 強い肯定語が含まれるときはSKIP_HINTより優先する
STRONG_POSITIVE = re.compile("新卒|募集要項|初任給|選考|エントリー|shinsotsu")

# 短いメニュー的なラベル（「募集要項」「職種紹介」など）は強い手がかり。
# 長い見出し文（インタビュー記事のタイトルなど）にたまたま単語が混ざるのとは区別する
STRONG_LABEL = re.compile(
    "^(新卒採用|新卒|職種紹介?|コース紹介?|仕事内容|求める人物像?|採用の流れ|採用フロー|"
    "選考(の流れ|フロー)?|募集要項|よくある質問|FAQ|エントリー|給与|待遇|初任給|福利厚生)$"
)

META_REFRESH = re.compile(
    r'(?is)<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\']?\s*\d+\s*;\s*url=([^"\'>]+)')


def plain_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


EXTERNAL_DENY_HOST = re.compile(
    r"(facebook|twitter|x\.com|instagram|youtube|linkedin|line\.me|note\.com|"
    # 就活口コミ・ES共有サイト。「採用ページ自身を出典にする」原則に反するため2026-08-20に追加
    # （career-tasu.jpで実測：会社ページに少量の企業紹介があるが、そこからサイト内リンクを辿ると
    # 無関係な他社のES投稿ページにまで迷い込み、クロール予算を無駄にした上に出典として不適切）
    r"career-tasu\.jp|unistyle\.jp|onecareer\.jp|openwork\.jp|en-hyouban\.com|"
    r"vorkers\.com|geekly\.co\.jp/media|shukatsu-mirai)\.?", re.I)


def find_candidate_links(html: str, base: str, origin: str) -> list[tuple[int, str, str]]:
    """リンク文字列に手がかり語を含むものだけを候補にする。

    2026-08-20: 同一ホストへの限定をやめた。三菱UFJ（mufg.jp→採用専用の別ドメイン
    mufg-saiyo.jp）のように、コーポレートサイトと採用サイトが別ドメインの会社が多く
    実測された。href の当てずっぽうはせず**実際にページに貼られたリンクだけ**を辿る
    という原則は変えていない。SNS等の明らかに無関係なドメインだけ除く。
    """
    seen: dict[str, tuple[int, str]] = {}
    for m in re.finditer(r"(?is)<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html):
        href, inner = m.group(1), re.sub(r"(?s)<[^>]+>", "", m.group(2))
        text = re.sub(r"\s+", "", inner)
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        url = urllib.parse.urljoin(base, href)
        if not url.startswith(("http://", "https://")):
            continue
        host = urllib.parse.urlsplit(url).netloc
        if EXTERNAL_DENY_HOST.search(host):
            continue
        url = url.split("#")[0]
        low_text = text.lower()
        if SKIP_URL.search(url):
            continue
        if STRONG_LABEL.match(text):
            score = 100  # 「募集要項」のような短いメニューラベルそのもの
        elif (SKIP_HINT.search(text) or SKIP_HINT.search(low_text)) and not STRONG_POSITIVE.search(text):
            continue     # 「キャリア採用」「インタビュー」等の除外語のみで、強い肯定語を伴わない
        elif len(text) <= 100 and (FOLLOW_HINT.search(text) or FOLLOW_HINT.search(low_text)):
            score = 20   # 手がかり語を含む文（カードUIで説明文がくっつき長くなることがあるため100字まで許容）
        else:
            continue
        if url not in seen or score > seen[url][0]:
            seen[url] = (score, text[:30])
    return sorted(((s, u, t) for u, (s, t) in seen.items()), key=lambda x: -x[0])


def fetch_one(sess: requests.Session, code: str, top_url: str) -> dict:
    """トップページからBFSで最大 MAX_HOPS 階層まで、候補リンクを追いながらページを集める。
    「募集要項」ページがトップの直リンクではなく2〜3階層先にある会社が多いための対応
    （2026-08-20、1階層しか追わない旧実装で歩留まりが低かった実測を受けて変更）。
    """
    pages: list[dict] = []
    visited: set[str] = set()

    def get(url: str, _refresh_hop: int = 0) -> tuple[str, str] | None:
        """(最終URL, HTML) を返す。<meta refresh> は同一サイト内に限り最大2回まで追う。"""
        u = urllib.parse.urlsplit(url)
        here = f"{u.scheme}://{u.netloc}"
        allowed = robots_allows(sess, here, u.path or "/")
        if not allowed:
            log(f"saiyo\t{code}\t{url}\tROBOTS_DISALLOW_OR_UNREADABLE")
            time.sleep(POLITE_DELAY)
            return None
        try:
            r = sess.get(url, timeout=TIMEOUT, allow_redirects=True)
            log(f"saiyo\t{code}\t{url}\t{r.status_code}")
            if r.status_code >= 400:
                return None
            r.encoding = r.apparent_encoding or r.encoding
            html = r.text
            final_url = r.url
        except requests.RequestException as e:
            log(f"saiyo\t{code}\t{url}\tERROR\t{type(e).__name__}")
            return None
        finally:
            time.sleep(POLITE_DELAY)
        # <meta http-equiv="refresh"> でしか飛ばないページがある（住友商事のscg-recruit.jpなど）。
        # サーバー側のHTTPリダイレクトはrequestsが自動で追うが、これはHTML側の指示なので手動で追う。
        # 同一サイトの中の言い換え（www有無・サブドメイン違い）だけを許し、外部への飛び先は追わない
        m = META_REFRESH.search(html)
        if m and _refresh_hop < 2:
            target = urllib.parse.urljoin(final_url, m.group(1).strip())
            t_host = urllib.parse.urlsplit(target).netloc.removeprefix("www.")
            h_host = urllib.parse.urlsplit(final_url).netloc.removeprefix("www.")
            if target != final_url and (t_host == h_host or t_host.endswith("." + h_host)
                                         or h_host.endswith("." + t_host)):
                return get(target, _refresh_hop + 1)
        return final_url, html

    top = get(top_url)
    if top is None:
        return {"status": "error", "pages": []}
    top_final_url, top_html = top
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(top_final_url))
    visited.add(top_url)
    visited.add(top_final_url)
    text = plain_text(top_html)[:MAX_CHARS_PER_PAGE]
    pages.append({"url": top_final_url, "text": text})

    # BFS: (score, url, label, hop数) のキューを階層ごとに処理する。
    # 同じ階層内はスコアの高い順、次の階層に進むのはこの階層を使い切ってから。
    frontier = [(url, label) for _score, url, label in
                find_candidate_links(top_html, top_final_url, origin)]
    hop = 1
    while frontier and hop <= MAX_HOPS and len(pages) < MAX_TOTAL_PAGES:
        next_frontier: list[tuple[str, str]] = []
        for url, _label in frontier:
            if len(pages) >= MAX_TOTAL_PAGES:
                break
            if url in visited:
                continue
            visited.add(url)
            got = get(url)
            if got is None:
                continue
            final_url, html = got
            visited.add(final_url)
            t = plain_text(html)[:MAX_CHARS_PER_PAGE]
            if len(t) < 50:
                continue
            pages.append({"url": final_url, "text": t})
            if hop < MAX_HOPS:
                child_candidates = find_candidate_links(html, final_url, origin)
                next_frontier.extend((u, lb) for _s, u, lb in child_candidates if u not in visited)
        frontier = next_frontier
        hop += 1

    total_chars = sum(len(p["text"]) for p in pages)
    status = "ok" if total_chars >= THIN_CHARS else "thin"
    return {"status": status, "pages": pages, "total_chars": total_chars}


def main() -> None:
    args = sys.argv[1:]
    force = "--force" in args
    limit = 0
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    pilot_path = ROOT / args[args.index("--pilot") + 1] if "--pilot" in args else PILOT_JSON

    pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
    companies = pilot["companies"]
    if limit:
        companies = companies[:limit]

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    sess.headers["User-Agent"] = UA

    def already_done(code: str) -> bool:
        f = RAW_DIR / f"{code}.json"
        if not f.exists():
            return False
        try:
            # error（タイムアウト等の一時的な失敗）は取得済み扱いにせず再試行する
            return json.loads(f.read_text(encoding="utf-8")).get("status") != "error"
        except (json.JSONDecodeError, OSError):
            return False

    todo = [c for c in companies if force or not already_done(c["edinet_code"])]
    print(f"対象 {len(companies)} 社中、未取得（またはエラーで再試行）{len(todo)} 社を処理する")

    ok = thin = err = 0
    for i, c in enumerate(todo, 1):
        code = c["edinet_code"]
        result = fetch_one(sess, code, c["recruit_url"])
        result["edinet_code"] = code
        result["name"] = c["name"]
        result["fetched_at"] = time.strftime("%Y-%m-%d")
        (RAW_DIR / f"{code}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        if result["status"] == "ok":
            ok += 1
        elif result["status"] == "thin":
            thin += 1
        else:
            err += 1
        print(f"  [{i}/{len(todo)}] {c['name'].strip()}: {result['status']}"
              f" ({len(result['pages'])}ページ, {result.get('total_chars', 0)}字)")

    print(f"\n完了。ok={ok} thin={thin} error={err}")


if __name__ == "__main__":
    main()
