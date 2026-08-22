"""新卒の採用枠（コース名・対象学部・職種など）を読むため、採用サイトを数階層だけ深追いする。

`fetch_links.py` が集めるのは「採用ページのURL」までで、中身は読んでいない。
実際に3社のトップページを見た実測では、**採用トップは中身が薄いランディングページ**で、
「新卒採用」「職種一覧」「コース紹介」のような一段深いページに採用枠の説明がある。
一方で一部のサイトはJSで描画されるため、素朴な HTML 取得ではその一段深い中身すら空になる。

このファイルがやるのは「テキストを集めるところまで」。**採用枠を構造化するのはこのファイルの
仕事ではない**（数値と違って自然文の要約・分類なので、機械抽出ではなくセッションが直接読んで書く。
write_prose.py が事業内容の要約をAPIなしで直接書いたのと同じ理由）。

## 深追いの方法（当てずっぽうをしない・スコア優先で最大 MAX_HOPS 階層）
1. 採用トップページを1回取得する（1層目）
2. ページ内のリンクを見て、**リンク文字列**に「新卒」「職種」「コース」「募集要項」
   「仕事内容」「求める人物像」などが含まれるものだけを候補にする（href の当てずっぽうはしない）。
   リンク文字列はアンカー内テキストだけでなく **img の alt・aria-label・title 属性も見る**
   （2026-08-21 追加。画像ナビの会社はアンカー内テキストが空で、旧実装では77社が
   トップ1ページで止まっていた実測を受けて）
3. 候補は階層ごとではなく**全体をスコア順（優先度キュー）**で取得する。旧実装のBFSは
   複数ページから出た候補を到着順に混ぜていたため、「募集要項」（スコア100）より先に
   ただの手がかり語リンク（スコア20）へ予算を使うことがあった
4. 全社で合計 MAX_TOTAL_PAGES 件まで（1ホストへの負荷を無限に増やさないための上限）
5. robots.txt は fetch_links.py と同じ関数で確認し、Disallow なら取りに行かない。
   **User-Agent は偽装しない。403 等で弾かれたら、ヘッダを標準的なブラウザ相当
   （Accept / Accept-Language。UAはそのまま）にして1回だけ再試行し、それでも弾かれたら
   その会社を対象から外す**（fetch_links.py の原則を維持）
6. 集めた全ページのプレーンテキストが THIN_CHARS 未満なら「動的サイトの疑い・情報薄い」として
   status を thin にする（採用枠は書かず、リンクだけ出す判断はこの後の工程がする）
7. 言語は問わない。日本人の新卒採用が対象である限り、英語ページ（グローバル採用サイトの
   日本向けセクションなど）に有用な情報があれば読む対象にする

## 「実際に書かれているものだけを辿る」原則の適用範囲（2026-08-21 拡張）
href の当てずっぽうをしない原則は変えない。ただし旧実装は「<a> タグのテキスト」しか
見ておらず、サイトが**実際に公開しているのに拾えない**リンクが多かった。以下はすべて
「サイト自身が書いて公開しているURL」なので原則の内側として扱う：
- <a> の中の img alt / aria-label / title（画像ナビ・アイコンナビ）
- <iframe src>（採用コンテンツやATSフォームの埋め込み）
- HTML 本文・スクリプト内に書かれた同系ドメインの採用系URL（SPAのルーター定義など。
  候補が乏しいときだけの補助。文字列として実在するURLのみで、パスの組み立てはしない）
- robots.txt の Sitemap: 行と sitemap.xml の <loc>（サイトが「ここにページがある」と
  宣言しているもの。候補ゼロのときだけの補助）
- <noscript> の中身（旧実装は捨てていたが、JSサイトが「JS無効環境向け」に置いた
  正式なフォールバック内容そのもの）

## 診断情報（2026-08-21 追加）
旧実装は error の理由を捨てていて（status:"error" のみ）、再発時に分類から
やり直しになった。v2 は raw JSON に error_reason / used（どの補助手段が効いたか）/
signals（募集要項系シグナル語の有無）を記録する。signals は「クロールは ok でも
構造化に使える記述が無い」ケース（旧実装で609社中130社）を機械的に数えるための指標。

アクセスは同一ホストへの連続リクエストで POLITE_DELAY 秒あける。
取得済み（data/saiyo_raw/{code}.json が存在する）会社は --force を付けない限りスキップする＝差分実行。
（status が error の会社と、--retry-nosig 指定時は signals 無しの会社も再試行対象になる）

使い方:
    python fetch_saiyo.py                 # data/saiyo_pilot.json の全社
    python fetch_saiyo.py --limit 10       # 先頭10社だけ（動作確認用）
    python fetch_saiyo.py --force          # 取得済みも含め全社やり直す
    python fetch_saiyo.py --pilot data/x.json   # 対象企業リストを差し替える（再挑戦分だけ絞る等）
    python fetch_saiyo.py --retry-nosig    # ok でもシグナル語ゼロだった会社を再試行対象に含める
    # 採用URL未判明の会社（--all 選定の official_url のみの行）は、公式トップから
    # 採用リンクを引き直して起点にする（2026-08-21、記載企業すべてへの拡大に伴い追加）
    python fetch_saiyo.py --render         # thin/候補ゼロのとき Playwright で1回だけ描画して再抽出
                                           # （UAはbotのまま・robots遵守のまま。要 pip install playwright）
"""

from __future__ import annotations

import heapq
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
LINKS_JSON = ROOT / "data" / "company_links.json"
RAW_DIR = ROOT / "data" / "saiyo_raw"

MAX_HOPS = 3              # トップから何階層先まで追うか（トップ自身は0層目）
MAX_TOTAL_PAGES = 12      # トップ込みで1社あたり取得するページ数の上限
MAX_CHARS_PER_PAGE = 9000   # 1ページぶんのプレーンテキストの上限
MAX_CHARS_KEY_PAGE = 16000  # 募集要項などスコア100のページは表が長いので上限を緩める
                            # （2026-08-21。バッチ側の上限は上げたのにページ側9000で
                            #  表が切れている会社があったため）
THIN_CHARS = 400          # 全ページ合計でこれ未満なら「情報薄い」扱い

# 「構造化に使える記述が取れたか」の機械的な目安。dump後にセッションが読む内容の先行指標で、
# 旧パイロットの実測では signals あり436社中222社が構造化成功、なし130社中11社しか成功していない
SIGNAL = re.compile(
    "初任給|基本給|月給|応募資格|募集要項|卒業見込|全学部|学部不問|募集職種|採用フロー|選考フロー|給与|待遇")

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
STRONG_POSITIVE = re.compile("新卒|募集要項|初任給|選考|エントリー|shinsotsu")

# 短いメニュー的なラベル（「募集要項」「職種紹介」など）は強い手がかり。
# 長い見出し文（インタビュー記事のタイトルなど）にたまたま単語が混ざるのとは区別する
STRONG_LABEL = re.compile(
    "^(新卒採用|新卒|職種紹介?|コース紹介?|仕事内容|求める人物像?|採用の流れ|採用フロー|"
    "選考(の流れ|フロー)?|募集要項|よくある質問|FAQ|エントリー|給与|待遇|初任給|福利厚生)$"
)

META_REFRESH = re.compile(
    r'(?is)<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\']?\s*\d+\s*;\s*url=([^"\'>]+)')
# ほぼ空のページがJSでだけ飛ばすパターン（location.href / location.replace）。
# メタリフレッシュと同じ扱い：ページに実際に書かれた行き先だけを追う
JS_REDIRECT = re.compile(
    r"""(?i)(?:location\.href\s*=|location\.replace\s*\(|window\.location\s*=)\s*["']([^"']+)["']""")

# URLに採用系の語を含むか（生HTML走査・sitemap絞り込みに使う。hrefの組み立てはしない）
RECRUIT_URL_HINT = re.compile(r"recruit|saiyo|saiyou|career|shinsotsu|graduate|freshers|entry", re.I)

EXTERNAL_DENY_HOST = re.compile(
    r"(facebook|twitter|x\.com|instagram|youtube|linkedin|line\.me|note\.com|"
    # 就活口コミ・ES共有サイト。「採用ページ自身を出典にする」原則に反するため2026-08-20に追加
    r"career-tasu\.jp|unistyle\.jp|onecareer\.jp|openwork\.jp|en-hyouban\.com|"
    r"vorkers\.com|geekly\.co\.jp/media|shukatsu-mirai)\.?", re.I)

# 候補スコア。数字はキュー内の優先順位にだけ使う
S_STRONG = 100   # 「募集要項」のような短いメニューラベルそのもの
S_IFRAME = 60    # iframe埋め込み（採用コンテンツ・ATSフォーム）
S_FOLLOW = 20    # 手がかり語を含むリンク文
S_SITEMAP = 15   # sitemap.xml 由来（候補ゼロのときだけ）
S_RAWURL = 10    # 生HTML中の採用系URL（候補が乏しいときだけ）


def plain_text(html: str) -> str:
    # noscript は捨てない：JSサイトが置いた正式なフォールバック内容で、thin判定の救済になる
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def norm_url(url: str) -> str:
    """visited 判定用の正規化。www有無・末尾スラッシュ・#以降の違いで二重取得しない。
    実際のアクセスには元のURLを使う（正規化はあくまで重複判定だけ）。"""
    u = urllib.parse.urlsplit(url.split("#")[0])
    host = u.netloc.lower().removeprefix("www.")
    path = u.path.rstrip("/") or "/"
    return f"{host}{path}?{u.query}" if u.query else f"{host}{path}"


def anchor_label(inner_html: str, attrs: str) -> str:
    """リンクの「文字列」を決める。アンカー内テキスト→img alt→aria-label→title の順。
    画像ナビの会社はアンカー内テキストが空になるため（2026-08-21、実測77社の主因）。"""
    text = re.sub(r"\s+", "", re.sub(r"(?s)<[^>]+>", "", inner_html))
    if text:
        return text
    for pat in (r'(?is)<img[^>]+alt=["\']([^"\']+)["\']',):
        m = re.search(pat, inner_html)
        if m and m.group(1).strip():
            return re.sub(r"\s+", "", m.group(1))
    for pat in (r'(?i)aria-label=["\']([^"\']+)["\']', r'(?i)\btitle=["\']([^"\']+)["\']'):
        m = re.search(pat, attrs)
        if m and m.group(1).strip():
            return re.sub(r"\s+", "", m.group(1))
    return ""


def score_label(text: str) -> int | None:
    """リンク文字列のスコア。None は「辿らない」。"""
    if not text:
        return None
    low_text = text.lower()
    if STRONG_LABEL.match(text):
        return S_STRONG
    if (SKIP_HINT.search(text) or SKIP_HINT.search(low_text)) and not STRONG_POSITIVE.search(text):
        return None
    if len(text) <= 100 and (FOLLOW_HINT.search(text) or FOLLOW_HINT.search(low_text)):
        return S_FOLLOW
    return None


def _accept_url(url: str, base: str) -> str | None:
    url = urllib.parse.urljoin(base, url)
    if not url.startswith(("http://", "https://")):
        return None
    if EXTERNAL_DENY_HOST.search(urllib.parse.urlsplit(url).netloc):
        return None
    url = url.split("#")[0]
    if SKIP_URL.search(url):
        return None
    return url


def find_candidate_links(html: str, base: str) -> list[tuple[int, str, str]]:
    """リンク文字列に手がかり語を含むものだけを候補にする。

    2026-08-20: 同一ホストへの限定をやめた（mufg.jp→mufg-saiyo.jp のような採用専用
    別ドメインが多いため）。href の当てずっぽうはせず**実際にページに貼られたリンクだけ**を
    辿る原則は変えていない。SNS等の明らかに無関係なドメインだけ除く。
    2026-08-21: img alt / aria-label / title もリンク文字列として見る。iframe src も候補にする。
    """
    seen: dict[str, tuple[int, str]] = {}
    for m in re.finditer(r"(?is)<a\b([^>]*)href=[\"']([^\"']+)[\"']([^>]*)>(.*?)</a>", html):
        pre_attrs, href, post_attrs, inner = m.group(1), m.group(2), m.group(3), m.group(4)
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        url = _accept_url(href, base)
        if url is None:
            continue
        text = anchor_label(inner, pre_attrs + " " + post_attrs)
        score = score_label(text)
        if score is None:
            continue
        if url not in seen or score > seen[url][0]:
            seen[url] = (score, text[:30])
    # iframe：採用コンテンツやATSフォームの埋め込み。src はページに実際に書かれているURL
    for m in re.finditer(r"(?is)<iframe\b[^>]*src=[\"']([^\"']+)[\"']", html):
        url = _accept_url(m.group(1), base)
        if url is None or url in seen:
            continue
        if RECRUIT_URL_HINT.search(url):
            seen[url] = (S_IFRAME, "(iframe)")
    return sorted(((s, u, t) for u, (s, t) in seen.items()), key=lambda x: -x[0])


def harvest_raw_urls(html: str, base: str) -> list[tuple[int, str, str]]:
    """<a> で拾えなかったときの補助：HTML（スクリプト含む）に文字列として実在する
    採用系URLを拾う。SPAのルーター定義・JSONに埋まったリンクが対象。
    パスの組み立て・推測はしない（書かれているものだけ）。同系ドメインに限定する。"""
    base_host = urllib.parse.urlsplit(base).netloc.lower().removeprefix("www.")
    # 例: example.co.jp の「登記ドメイン」ざっくり比較（サブドメイン違いは同系とみなす）
    base_root = ".".join(base_host.rsplit(".", 3)[-3:]) if base_host.endswith(".jp") \
        else ".".join(base_host.rsplit(".", 2)[-2:])
    out: dict[str, tuple[int, str]] = {}
    for m in re.finditer(r"""https?://[^\s"'<>\\)]+""", html):
        url = m.group(0).rstrip(".,;")
        if not RECRUIT_URL_HINT.search(url):
            continue
        host = urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")
        if not (host == base_host or host.endswith("." + base_root) or base_root in host):
            continue
        u = _accept_url(url, base)
        if u is None:
            continue
        if re.search(r"\.(png|jpe?g|gif|svg|webp|css|js|ico|woff2?)(\?|$)", u, re.I):
            continue
        out.setdefault(u, (S_RAWURL, "(生HTML)"))
    # ルート相対パス（"/recruit/..." のような引用文字列）も同様に拾う
    for m in re.finditer(r"""["'](/[^"'\s<>]{2,120})["']""", html):
        path = m.group(1)
        if not RECRUIT_URL_HINT.search(path):
            continue
        if re.search(r"\.(png|jpe?g|gif|svg|webp|css|js|ico|woff2?)(\?|$)", path, re.I):
            continue
        u = _accept_url(path, base)
        if u is not None:
            out.setdefault(u, (S_RAWURL, "(生HTML)"))
    return [(s, u, t) for u, (s, t) in out.items()]


def sitemap_candidates(sess: requests.Session, origin: str, code: str) -> list[tuple[int, str, str]]:
    """候補ゼロのときだけの補助：robots.txt の Sitemap: 行 → 無ければ /sitemap.xml。
    サイトが自分で宣言しているURL一覧なので「書かれているものだけを辿る」の内側。
    採用系の語を含む <loc> だけを最大10件。"""
    maps: list[str] = []
    try:
        r = sess.get(urllib.parse.urljoin(origin, "/robots.txt"), timeout=TIMEOUT)
        time.sleep(POLITE_DELAY)
        if r.status_code < 400:
            maps = re.findall(r"(?im)^sitemap:\s*(\S+)", r.text)[:3]
    except requests.RequestException:
        pass
    if not maps:
        maps = [urllib.parse.urljoin(origin, "/sitemap.xml")]
    out: list[tuple[int, str, str]] = []
    for sm in maps:
        try:
            r = sess.get(sm, timeout=TIMEOUT)
            time.sleep(POLITE_DELAY)
            if r.status_code >= 400:
                continue
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
        except requests.RequestException:
            continue
        # sitemapindex なら、採用系の語を含む子sitemapを1つだけ潜る
        child_maps = [u for u in locs if u.endswith(".xml") and RECRUIT_URL_HINT.search(u)][:1]
        for cm in child_maps:
            try:
                r2 = sess.get(cm, timeout=TIMEOUT)
                time.sleep(POLITE_DELAY)
                if r2.status_code < 400:
                    locs += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r2.text)
            except requests.RequestException:
                pass
        for u in locs:
            if u.endswith(".xml") or not RECRUIT_URL_HINT.search(u):
                continue
            a = _accept_url(u, sm)
            if a is not None:
                out.append((S_SITEMAP, a, "(sitemap)"))
            if len(out) >= 10:
                break
        if out:
            break
    log(f"saiyo\t{code}\t(sitemap)\t{len(out)}件")
    return out


BROWSERISH_HEADERS = {
    # UAは偽装しない（プロジェクト原則）。CDN/WAFがAcceptヘッダ欠落を機械アクセスと
    # 誤認して 403 を返す構成があるため、ブラウザが常に送る標準ヘッダだけを足して1回再試行する
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.8",
}


def render_with_browser(url: str, code: str) -> tuple[str, str] | None:
    """--render 指定時のみ。Playwright(Chromium)で1回だけ描画してHTMLを得る。
    UAはbot文字列のまま上書きし、robots確認は呼び出し側で済んでいる前提。
    Playwright未導入なら None（呼び出し側が案内を出す）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=UA)
            page.goto(url, timeout=30000, wait_until="networkidle")
            html = page.content()
            final_url = page.url
            browser.close()
        log(f"saiyo\t{code}\t{url}\tRENDERED")
        time.sleep(POLITE_DELAY)
        return final_url, html
    except Exception as e:  # noqa: BLE001  描画失敗は素のHTMLで続行するだけ
        log(f"saiyo\t{code}\t{url}\tRENDER_ERROR\t{type(e).__name__}")
        return None


def fetch_one(sess: requests.Session, code: str, top_url: str | None,
              render: bool = False, official_url: str | None = None) -> dict:
    """トップページから優先度キューで最大 MAX_HOPS 階層まで、候補リンクを追いながらページを集める。
    「募集要項」ページがトップの直リンクではなく2〜3階層先にある会社が多いための深追い
    （2026-08-20）と、スコア順のグローバルキュー・画像ナビ対応・補助的なURL収集（2026-08-21）。
    """
    pages: list[dict] = []
    visited: set[str] = set()
    used: list[str] = []
    error_reason = ""

    def get(url: str, _refresh_hop: int = 0, extra_headers: dict | None = None
            ) -> tuple[str, str] | None:
        nonlocal error_reason
        u = urllib.parse.urlsplit(url)
        here = f"{u.scheme}://{u.netloc}"
        allowed = robots_allows(sess, here, u.path or "/")
        if allowed is None:
            error_reason = error_reason or "robots_unreadable"
            log(f"saiyo\t{code}\t{url}\tROBOTS_UNREADABLE")
            time.sleep(POLITE_DELAY)
            return None
        if not allowed:
            error_reason = error_reason or "robots_disallow"
            log(f"saiyo\t{code}\t{url}\tROBOTS_DISALLOW")
            time.sleep(POLITE_DELAY)
            return None
        try:
            r = sess.get(url, timeout=TIMEOUT, allow_redirects=True,
                         headers=extra_headers or {})
            log(f"saiyo\t{code}\t{url}\t{r.status_code}")
            if r.status_code in (403, 406, 429) and extra_headers is None:
                # ヘッダ欠落起因のWAF誤認だけ救う。UAはそのまま（原則）。再試行は1回だけ
                time.sleep(POLITE_DELAY)
                return get(url, _refresh_hop, BROWSERISH_HEADERS)
            if r.status_code >= 400:
                error_reason = error_reason or f"http_{r.status_code}"
                return None
            r.encoding = r.apparent_encoding or r.encoding
            html = r.text
            final_url = r.url
        except requests.RequestException as e:
            error_reason = error_reason or f"request_{type(e).__name__}"
            log(f"saiyo\t{code}\t{url}\tERROR\t{type(e).__name__}")
            return None
        finally:
            time.sleep(POLITE_DELAY)
        # <meta http-equiv="refresh"> でしか飛ばないページ（住友商事のscg-recruit.jpなど）と、
        # ほぼ空のページがJSでだけ飛ばすパターンを、ページに書かれた行き先に限り最大2回追う。
        # 行き先の制限はリンクと同じ基準（deny-host以外は追う。2026-08-21に同一サイト限定を撤廃：
        # リンク追跡側は別ドメイン許可済みで、リダイレクトだけ同一サイト限定にする理由が無いため）
        target = None
        m = META_REFRESH.search(html)
        if m:
            target = m.group(1).strip()
        elif len(plain_text(html)) < 200:
            m = JS_REDIRECT.search(html)
            if m:
                target = m.group(1).strip()
        if target and _refresh_hop < 2:
            t_url = _accept_url(target, final_url)
            if t_url and norm_url(t_url) != norm_url(final_url):
                nxt = get(t_url, _refresh_hop + 1)
                if nxt is not None:
                    return nxt
        return final_url, html

    def discover_from_official(off_url: str) -> str | None:
        """公式トップを1回取得し、貼られているリンクだけから採用ページを選ぶ
        （fetch_links の pick_recruit_link と同じ方式。当てずっぽうはしない）。"""
        got = get(off_url)
        if got is None:
            return None
        _f, _h = got
        from fetch_links import pick_recruit_link
        picked = pick_recruit_link(_h, _f)
        if picked:
            return picked[0]
        cands = find_candidate_links(_h, _f)  # 「新卒採用」等の強いラベルがあればそれでも良い
        return cands[0][1] if cands else None

    official = official_url or _official_url(code)
    if top_url:
        top = get(top_url)
        if top is None and error_reason.startswith("http_4") and official:
            # 採用URLが陳腐化して404/410のことがある（マイナビの年度URL等）。公式から引き直す
            r_url = discover_from_official(official)
            if r_url:
                top = get(r_url)
                if top is not None:
                    used.append("rediscovered_recruit_url")
                    error_reason = ""
    else:
        # 採用URL未判明の会社（--all 選定で official のみ判明の249社+手動特定分）。
        # 公式トップから採用リンクを見つけてそこを起点にする（2026-08-21、全社化に伴い追加）
        top = None
        if official:
            r_url = discover_from_official(official)
            if r_url:
                top = get(r_url)
                if top is not None:
                    used.append("discovered_from_official")
                    error_reason = ""
            elif not error_reason:
                error_reason = "no_recruit_link_on_official"
        else:
            error_reason = "no_url"
    if top is None:
        return {"status": "error", "pages": [], "error_reason": error_reason, "used": used}
    top_final_url, top_html = top
    visited.add(norm_url(top_final_url))
    if top_url:
        visited.add(norm_url(top_url))

    def page_cap(score: int) -> int:
        return MAX_CHARS_KEY_PAGE if score >= S_STRONG else MAX_CHARS_PER_PAGE

    def add_page(url: str, html: str, score: int) -> None:
        t = plain_text(html)[:page_cap(score)]
        if len(t) >= 50:
            pages.append({"url": url, "text": t})

    add_page(top_final_url, top_html, S_STRONG)  # トップは表が切れないよう大きい方の上限で
    if not pages:
        pages.append({"url": top_final_url, "text": plain_text(top_html)})  # 空でも記録は残す

    candidates = find_candidate_links(top_html, top_final_url)
    if len(candidates) < 3:
        extra = harvest_raw_urls(top_html, top_final_url)
        if extra:
            used.append("raw_url_harvest")
            candidates += extra
    if not candidates and render:
        rendered = render_with_browser(top_final_url, code)
        if rendered is not None:
            top_final_url, top_html = rendered
            used.append("rendered_top")
            pages[0] = {"url": top_final_url,
                        "text": plain_text(top_html)[:MAX_CHARS_KEY_PAGE]}
            candidates = find_candidate_links(top_html, top_final_url) \
                or harvest_raw_urls(top_html, top_final_url)
    if not candidates:
        origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(top_final_url))
        sm = sitemap_candidates(sess, origin, code)
        if sm:
            used.append("sitemap")
            candidates = sm

    # グローバル優先度キュー：(-score, hop, 連番) の順。スコア同点なら浅い階層から
    heap: list[tuple[int, int, int, str]] = []
    seq = 0
    for score, url, _label in candidates:
        heapq.heappush(heap, (-score, 1, seq, url)); seq += 1

    while heap and len(pages) < MAX_TOTAL_PAGES:
        neg_score, hop, _n, url = heapq.heappop(heap)
        if norm_url(url) in visited:
            continue
        visited.add(norm_url(url))
        got = get(url)
        if got is None:
            continue
        final_url, html = got
        visited.add(norm_url(final_url))
        add_page(final_url, html, -neg_score)
        # テキストが薄いページ（JSシェル）でも、その中のリンクは実在する導線なので拾う
        # （旧実装は50字未満のページを丸ごと捨てていて、JSシェル配下の募集要項に届かなかった）
        if hop < MAX_HOPS:
            for score, u, _label in find_candidate_links(html, final_url):
                if norm_url(u) not in visited:
                    heapq.heappush(heap, (-score, hop + 1, seq, u)); seq += 1

    total_chars = sum(len(p["text"]) for p in pages)
    all_text = " ".join(p["text"] for p in pages)
    if total_chars < THIN_CHARS and render and "rendered_top" not in used:
        rendered = render_with_browser(top_final_url, code)
        if rendered is not None:
            r_url, r_html = rendered
            used.append("rendered_thin")
            add_page(r_url, r_html, S_STRONG)
            total_chars = sum(len(p["text"]) for p in pages)
            all_text = " ".join(p["text"] for p in pages)

    status = "ok" if total_chars >= THIN_CHARS else "thin"
    return {"status": status, "pages": pages, "total_chars": total_chars,
            "signals": bool(SIGNAL.search(all_text)), "used": used,
            "error_reason": ""}


_LINKS_CACHE: dict | None = None


def _official_url(code: str) -> str | None:
    global _LINKS_CACHE
    if _LINKS_CACHE is None:
        try:
            _LINKS_CACHE = json.loads(LINKS_JSON.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _LINKS_CACHE = {}
        # 手動特定分（data/links/manual.json）を上書き。company_links.json は
        # fetch_links merge で再生成され手動編集が消えるため、別ファイルで重ねる
        try:
            manual = json.loads((ROOT / "data" / "links" / "manual.json")
                                .read_text(encoding="utf-8"))
            for c, ent in manual.items():
                if c.startswith("_"):
                    continue
                base = _LINKS_CACHE.setdefault(c, {})
                for k in ("official", "recruit"):
                    if ent.get(k) and not base.get(k):
                        base[k] = ent[k]
        except (OSError, json.JSONDecodeError):
            pass
    ent = _LINKS_CACHE.get(code) or {}
    return ent.get("official") or None


def main() -> None:
    args = sys.argv[1:]
    force = "--force" in args
    render = "--render" in args
    retry_nosig = "--retry-nosig" in args
    limit = 0
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    pilot_path = ROOT / args[args.index("--pilot") + 1] if "--pilot" in args else PILOT_JSON

    if render:
        try:
            import playwright  # noqa: F401
        except ImportError:
            print("--render には Playwright が必要: pip install playwright && playwright install chromium")
            return

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
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        if d.get("status") == "error":
            return False   # error（タイムアウト等の一時的な失敗）は再試行する
        if retry_nosig:
            # ok でも構造化に使える記述ゼロ（signals無し）は取り直す。
            # 旧形式（signalsキー無し）はテキストから判定する
            if d.get("status") == "thin":
                return False
            sig = d.get("signals")
            if sig is None:
                sig = bool(SIGNAL.search(" ".join(p.get("text", "") for p in d.get("pages", []))))
            return sig
        return True

    todo = [c for c in companies if force or not already_done(c["edinet_code"])]
    print(f"対象 {len(companies)} 社中、未取得（または再試行対象）{len(todo)} 社を処理する")

    ok = thin = err = sig = 0
    for i, c in enumerate(todo, 1):
        code = c["edinet_code"]
        result = fetch_one(sess, code, c.get("recruit_url"), render=render,
                           official_url=c.get("official_url"))
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
        sig += 1 if result.get("signals") else 0
        extra = "・".join(result.get("used") or [])
        print(f"  [{i}/{len(todo)}] {c['name'].strip()}: {result['status']}"
              f" ({len(result['pages'])}ページ, {result.get('total_chars', 0)}字,"
              f" signals={'あり' if result.get('signals') else 'なし'}"
              f"{', ' + extra if extra else ''}"
              f"{', ' + result.get('error_reason', '') if result.get('error_reason') else ''})")

    print(f"\n完了。ok={ok} thin={thin} error={err}  signalsあり={sig}"
          f"（構造化に使える記述が取れた見込みの社数）")


if __name__ == "__main__":
    main()
