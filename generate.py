"""data/companies/*.json から静的サイトを組み立てる。

    python generate.py            # site/ に全ページを書き出す

このスクリプトは **Claude APIを呼ばない**。数値はすべて data/companies/*.json
（= 有報から機械抽出し、PDFと突き合わせ済み）から読むだけで、文章は下の
定数に書かれた固定文しか使わない。解説文を Claude に書かせる場合は
write_prose.py が data/prose/{code}.json を置き、ここはそれを差し込むだけ。
**文章が1文字も無くてもサイトは完全に成立する。**

守っていること（ATTRIBUTION.md より）
------------------------------------
- 全ページに EDINET の出典URL・PDL1.0・「当サイトが機械的に抽出・整形した」旨
- 全ページに免責文
- データが無い項目は「非公表」。0や平均値で埋めない

数字の見せ方で嘘をつかないための規則
------------------------------------
1. 持株会社（MUFG・東京海上HD等）の平均年間給与は**持株会社本体**の数字であって、
   三菱UFJ銀行の水準ではない。ページ冒頭に警告を出し、連結子会社の表を併記する。
2. 「男女間賃金差異」は**男性を100としたときの女性の賃金の割合**であって、
   「差」ではない。ラベルにそう書く。64.7 は「女性は男性の64.7%」の意味。
3. 男性育休取得率には2方式ある。71条の6第2号方式は育児目的休暇を含むので**高く出る**。
   方式を併記し、**方式をまたいで順位を付けない**。
4. 従業員数は「連結」と「提出会社」を別項目にする。**平均年間給与の分母は提出会社**。
5. salary_trend_comparable が false の企業（基準変更あり）には5年増減率を出さず、
   有報の注記文をそのまま載せる。headcount_breaks は人数の推移にだけ注記する。
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import html
import json
import math
import os
import shutil
import sys
import urllib.parse
from pathlib import Path

import koumu
import rankings
import shindan
import tekisei

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "companies"
PROSE = ROOT / "data" / "prose"
SITE = ROOT / "site"

# 独自ドメイン取得後にここを変える。sitemap.xml の絶対URLに使う。
BASE_URL = os.getenv("SITE_BASE_URL", "https://shukatsu-data.com").rstrip("/")
SITE_NAME = "Shukatsu.com"
TAGLINE = "有価証券報告書の数字だけで、同業他社を並べる"

EDINET_VIEW = "https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?{doc_id}"
EDINET_PDF = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/pdf/{doc_id}.pdf"
PDL_URL = "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0"

DISCLAIMER = (
    "本サイトの数値は公的な一次情報から機械的に抽出したものですが、正確性を保証するものではありません。"
    "最終的な判断は必ず原典（有価証券報告書・各社の公式発表）をご確認ください。"
)
PROCESSING_NOTICE = "本ページの数値は、上記の有価証券報告書を当サイトが機械的に抽出・整形したものです。"

# URLは一度公開したら変えられない（変えるとSEOが死ぬ）。EDINETコードは永久に変わらないが、
# 読めるURLの方がクリックされるので romaji を振る。ここに無ければEDINETコードを使う。
SLUG = {
    "E02529": "mitsubishi-corporation", "E02513": "mitsui-bussan", "E02497": "itochu",
    "E02528": "sumitomo-corporation", "E02498": "marubeni", "E02958": "sojitz",
    "E02505": "toyota-tsusho",
    "E01777": "sony-group", "E01737": "hitachi", "E01739": "mitsubishi-electric",
    "E01914": "murata", "E01967": "keyence",
    "E02144": "toyota", "E02166": "honda", "E02142": "nissan",
    "E03606": "mufg", "E03614": "smfg", "E03615": "mizuho-fg",
    "E03847": "tokio-marine-hd", "E03854": "ms-and-ad", "E23924": "sompo-hd",
    "E04430": "ntt", "E04425": "kddi", "E04426": "softbank",
    "E03855": "mitsui-fudosan", "E03856": "mitsubishi-estate", "E03907": "sumitomo-realty",
    "E04235": "nippon-yusen", "E04236": "mol", "E04237": "kawasaki-kisen",
    "E00919": "takeda", "E00920": "astellas", "E00984": "daiichi-sankyo",
    "E00939": "eisai", "E00932": "chugai",
    "E00436": "ajinomoto", "E21902": "meiji-holdings", "E00457": "nissin-foods",
    "E00395": "kirin", "E00394": "asahi-group",
    "E00776": "shinetsu-chemical", "E00808": "mitsubishi-chemical", "E00877": "asahi-kasei",
    "E00752": "sumitomo-chemical", "E00873": "toray",
    "E00058": "kajima", "E00052": "taisei", "E00055": "obayashi", "E00053": "shimizu",
    "E01225": "nippon-steel", "E01264": "jfe", "E01231": "kobe-steel",
    "E04147": "jr-east", "E04149": "jr-central", "E04148": "jr-west",
    "E04273": "ana", "E04272": "jal", "E38082": "skymark",
    "E03462": "seven-and-i", "E03217": "fast-retailing", "E03061": "aeon",
    "E03752": "nomura", "E03753": "daiwa-securities", "E05159": "sbi",
    "E04498": "tepco", "E04499": "kansai-electric", "E04502": "chubu-electric",
    "E02126": "mitsubishi-heavy", "E01532": "komatsu", "E01570": "daikin",
    "E01630": "terumo", "E02272": "olympus", "E02271": "nikon", "E02274": "canon",
    "E01122": "agc", "E01130": "taiheiyo-cement", "E01138": "toto",
    "E00023": "sumitomo-metal-mining", "E00021": "mitsubishi-materials", "E00028": "dowa-holdings",
    "E01086": "bridgestone", "E01085": "yokohama-rubber", "E01090": "toyo-tire",
    "E00642": "oji-holdings", "E11873": "nippon-paper", "E00659": "rengo",
    "E24050": "eneos-holdings", "E01084": "idemitsu-kosan", "E31632": "cosmo-energy-holdings",
    "E02367": "nintendo", "E02481": "bandai-namco-holdings", "E00693": "dai-nippon-printing",
    "E04514": "tokyo-gas", "E04520": "osaka-gas", "E04517": "toho-gas",
    "E04707": "oriental-land", "E07801": "recruit-holdings", "E05425": "m3",
    "E05346": "tokyo-century", "E04762": "orix", "E03041": "credit-saison",
    "E05080": "rakuten-group", "E05000": "line-yahoo",
    "E01772": "panasonic-holdings", "E01766": "fujitsu", "E01182": "kyocera", "E01780": "tdk",
    "E00923": "shionogi", "E21183": "otsuka-holdings", "E00816": "kyowa-kirin",
    "E00334": "nipponham", "E25303": "calbee", "E27622": "suntory-beverage-food",
    "E03144": "nitori-holdings", "E03248": "ryohin-keikaku", "E03280": "ppih", "E03013": "takashimaya",
    "E00988": "fujifilm-holdings", "E00876": "kuraray", "E01888": "nitto-denko",
    "E03610": "resona-holdings", "E03611": "smtb-group", "E03556": "chiba-bank",
    "E06141": "dai-ichi-life-group", "E31755": "japan-post-insurance",
    "E05460": "dena", "E05072": "cyberagent", "E05041": "gmo-internet-group",
    "E04187": "yamato-holdings", "E32292": "sg-holdings",
    "E00872": "teijin", "E00525": "toyobo", "E00528": "kurashiki-boseki",
    "E04283": "mitsubishi-warehouse", "E04289": "nippon-transcity", "E04345": "kamigumi",
    "E02632": "medipal-holdings", "E02691": "paltac", "E02567": "iwatani",
    "E02152": "subaru", "E02163": "mazda", "E02167": "suzuki",
    "E00014": "nissui", "E00015": "umios", "E00012": "kyokuyo",
    "E00043": "inpex", "E00041": "japex", "E21342": "sumiseki-holdings",
    "E01317": "lixil", "E01353": "toyo-seikan-group-holdings", "E02379": "noritz",
    "E04060": "nomura-real-estate-holdings", "E27633": "tokyu-fudosan-holdings",
    "E00090": "haseko",
}
GROUP_SLUG = {
    "総合商社": "sogo-shosha", "電気機器": "denki-kiki", "輸送用機器": "yusoyo-kiki",
    "銀行業": "ginko", "保険業": "hoken", "情報・通信業": "joho-tsushin", "不動産業": "fudosan",
    "海運": "kaiun", "医薬品": "iyakuhin", "食料品": "shokuryohin", "化学": "kagaku",
    "建設業": "kensetsu", "鉄鋼": "tekko", "陸運": "rikuun", "空運": "kuuun",
    "小売": "kouri", "証券": "shoken", "電力": "denryoku", "機械": "kikai",
    "精密機器": "seimitsu-kiki",
    "ガラス・土石": "glass-ceramics", "非鉄金属": "hitetsu-kinzoku", "ゴム製品": "gomu-seihin",
    "パルプ・紙": "pulp-kami", "石油・石炭": "sekiyu-sekitan", "その他製品": "sonota-seihin",
    "ガス業": "gas-gyo", "サービス業": "service-gyo", "その他金融": "sonota-kinyu",
    "繊維製品": "sen-i-seihin", "倉庫運輸": "souko-unyu", "卸売業": "oroshiuri-gyo",
    "水産農林": "suisan-norin", "鉱業": "kougyou", "金属製品": "kinzoku-seihin",
}

NA = '<span class="na">非公表</span>'


# ---------------------------------------------------------------- 整形

def e(s) -> str:
    return html.escape(str(s), quote=True)


def yen(v) -> str:
    return f"{v:,}円" if isinstance(v, int) else NA


def man_plain(v) -> str:
    """meta description用。man()と違いHTMLの<span>を含まない生テキストを返す。

    かんぽ生命保険（平均年間給与が内部・営業職員で分かれ単一値が無い）で、
    man()の返す非公表マーク（<span class="na">）がmeta descriptionにそのまま
    漏れて壊れた文字列になったことで見つかった。検索結果のスニペットが壊れる
    実害があるので、HTML部品とプレーンテキスト部品を分けた。
    """
    return f"約{round(v / 10000):,}万円" if isinstance(v, int) else "非公表"


def dec1_plain(v, unit: str = "") -> str:
    return f"{v:.1f}{unit}" if isinstance(v, (int, float)) else "非公表"


def man(v) -> str:
    """円 → 「約1,562万円」。桁が大きい数は万円のほうが直感に合う。"""
    return f"約{round(v / 10000):,}万円" if isinstance(v, int) else NA


def pct(v, digits: int = 1) -> str:
    return f"{v * 100:.{digits}f}%" if isinstance(v, (int, float)) else NA


def num(v, unit: str = "") -> str:
    if isinstance(v, int):
        return f"{v:,}{unit}"
    if isinstance(v, float):
        return f"{v:g}{unit}"
    return NA


def dec1(v, unit: str = "") -> str:
    """平均年齢・平均勤続年数は小数1桁で揃える。42歳と42.3歳が混ざると表が読めない。

    勤続年数は有報の「◯年◯ヶ月」を年に直した値なので 17.58 のような桁が出る。
    小数1桁に丸めて見せ、並べ替えは丸める前の値で行う。
    """
    return f"{v:.1f}{unit}" if isinstance(v, (int, float)) else NA


EXTRA_SLUG_PATH = ROOT / "data" / "company_slugs.json"
EXTRA_SLUG: dict[str, str] = (
    json.loads(EXTRA_SLUG_PATH.read_text(encoding="utf-8")) if EXTRA_SLUG_PATH.exists() else {}
)


def slug_of(c: dict) -> str:
    """手動選定(SLUG)を最優先、無ければ自動生成(EXTRA_SLUG, tools/build_company_slugs.py)、
    どちらにも無ければEDINETコードそのもの。どの段でも一度出したURLは変えない。"""
    code = c["edinet_code"]
    return SLUG.get(code) or EXTRA_SLUG.get(code) or code


def logo_img(icon: str | None, size: int = 16) -> str:
    """企業のロゴ。公式サイトで見つけた実物のアイコン（icon）があればそれを出す。
    無ければ何も出さない（代替マークは作らない。favicon代行サービスはドメインによって
    実体が16x16固定で返ってくることを確認しており、拡大表示すると必ず粗くなるため使わない）。"""
    if not icon:
        return ""
    return f'<img class="co-logo" src="{e(icon)}" width="{size}" height="{size}" alt="">'


def short_name(name: str) -> str:
    for x in ("株式会社", "(株)", "（株）"):
        name = name.replace(x, "")
    return name.strip()


def sort_key(v, reverse: bool):
    """None を必ず末尾に落とす。非公表を0として並べると嘘になる。"""
    if v is None:
        return (1, 0)
    return (0, -v if reverse else v)


# ---------------------------------------------------------------- 読み込み

def load() -> tuple[list[dict], str]:
    domains_path = ROOT / "data" / "company_domains.json"
    domains = json.loads(domains_path.read_text(encoding="utf-8")) if domains_path.exists() else {}
    companies = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(DATA.glob("*.json"))]
    fin = rankings.load_financials()
    ovs = rankings.load_overseas()
    # 公式サイト・採用ページ（fetch_links.py が作る）。無くてもサイトは成立する
    links_path = ROOT / "data" / "company_links.json"
    links = json.loads(links_path.read_text(encoding="utf-8")) if links_path.exists() else {}
    for c in companies:
        c["slug"] = slug_of(c)
        c["short"] = short_name(c["name"])
        # ロゴ表示用のドメイン。手で用意した154社ぶんを優先し、無ければ
        # 法人番号で特定した公式サイトのホストを使う（154社 → 1,383社に増える）
        c["domain"] = domains.get(c["edinet_code"])
        if not c["domain"]:
            off = (links.get(c["edinet_code"]) or {}).get("official")
            if off:
                c["domain"] = urllib.parse.urlsplit(off).netloc.removeprefix("www.")
        # 公式サイトの実物アイコン（fetch_links.py が<link rel="icon">等から見つけたもの）。
        # 無ければ logo_img() は何も出さない
        c["icon"] = (links.get(c["edinet_code"]) or {}).get("icon")
        pp = PROSE / f"{c['edinet_code']}.json"
        c["prose"] = json.loads(pp.read_text(encoding="utf-8")) if pp.exists() else {}
        # 財務（extract_financials.py の出力）。無い会社・無い項目は素通しし、
        # 「あれば載せる」扱いにする。財務が1社も無くてもサイトは成立する
        c["fin"] = fin.get(c["edinet_code"])
        c["overseas"] = ovs.get(c["edinet_code"])
        c["links"] = links.get(c["edinet_code"]) or {}
        # 新卒採用枠（fetch_saiyo.py が生を集め、このセッションが直接読んで構造化したもの）。
        # 対象は一部の会社のみ（パイロット）。無い会社は今まで通りリンクだけを出す
        sp = ROOT / "data" / "saiyo" / f"{c['edinet_code']}.json"
        c["saiyo"] = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else None
    idx = ROOT / "data" / "doc_index.csv"
    fetched = dt.date.fromtimestamp(idx.stat().st_mtime).isoformat()
    return companies, fetched


def order_in_csv() -> dict[str, int]:
    with (ROOT / "companies.csv").open(encoding="utf-8", newline="") as f:
        return {r["edinet_code"]: i for i, r in enumerate(csv.DictReader(f))}


# ---------------------------------------------------------------- 部品

def search_box(pos: str) -> str:
    return f"""<div class="site-search" data-pos="{pos}">
  <input type="search" class="search-input" placeholder="企業名で検索（例：三菱商事）" autocomplete="off" aria-label="企業を検索">
  <div class="search-results" hidden></div>
</div>"""


def page(title: str, desc: str, body: str, depth: int, canonical: str) -> str:
    up = "../" * depth
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{e(BASE_URL + canonical)}">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:type" content="website">
<link rel="stylesheet" href="{up}style.css?v={ASSET_V}">
<script src="{up}app.js?v={ASSET_V}" defer></script>
</head>
<body data-base="{up}">
<header class="site">
  <a class="brand" href="{up}index.html">
    <svg width="28" height="28" viewBox="0 0 22 22" aria-hidden="true">
      <rect x="1" y="12" width="5" height="9" rx="1" fill="#fff"/>
      <rect x="8.5" y="6" width="5" height="15" rx="1" fill="#fff" opacity=".78"/>
      <rect x="16" y="1" width="5" height="20" rx="1" fill="#fff" opacity=".5"/>
    </svg>
    <span class="brand-word">{e(SITE_NAME)}</span>
  </a>
  <nav class="header-nav"><a href="{up}shindan.html">就活の軸診断</a><a href="{up}tekisei.html">学部・興味から絞る</a><a href="{up}ranking/index.html">ランキング</a><a href="{up}koumu/index.html">官公庁・政府系</a><a href="{up}hikaku.html">企業を選んで比較する →</a></nav>
  {search_box("header")}
</header>
<main>
{body}
</main>
<footer class="site">
  <p class="footer-search-label">企業を探す</p>
  {search_box("footer")}
  <p class="disclaimer">{e(DISCLAIMER)}</p>
  <p class="license">
    出典：EDINET閲覧（提出）サイト（各ページに当該書類のURLを記載）／
    <a href="{PDL_URL}" rel="nofollow">公共データ利用規約（PDL1.0）</a>
  </p>
  <p class="license">{e(PROCESSING_NOTICE)}</p>
</footer>
</body>
</html>
"""


def source_block(src: dict, fetched: str) -> str:
    """EDINETの出典。ATTRIBUTION.md の義務2つ（出典・加工の主体）をここで果たす。"""
    doc_id = src["doc_id"]
    return f"""<section class="source">
<h2>出典</h2>
<ul>
  <li>{e(src['doc_type'])}（{e(src['period_end'])} 期）
    ── <a href="{EDINET_VIEW.format(doc_id=doc_id)}" rel="nofollow">EDINETで閲覧</a>
    ／ <a href="{EDINET_PDF.format(doc_id=doc_id)}" rel="nofollow">原典PDF</a></li>
  <li>提出日時 {e(src['submit_datetime'])}／書類管理番号 {e(doc_id)}／当サイトのデータ取得日 {e(fetched)}</li>
  <li>ライセンス：<a href="{PDL_URL}" rel="nofollow">公共データ利用規約（PDL1.0）</a></li>
</ul>
<p class="processing">{e(PROCESSING_NOTICE)}</p>
</section>"""


def holding_warning(c: dict) -> str:
    """持株会社の平均年収は本体の数字。事業会社の水準ではない。

    有報に連結子会社別の指標が載っている会社（MUFG・SMFG・東京海上HD）だけが
    子会社の表を持つ。載っていない会社（ソニーG・みずほFG・MS&AD・NTT・SOMPO HD）に
    #subsidiaries へのリンクを出すとリンク切れになるので、文面を分ける。
    """
    if not c.get("is_holding"):
        return ""
    emp = c["latest"]["employees"]["reporting_company"]
    subs = c["latest"].get("subsidiaries") or []
    if subs:
        tail = (
            f"実際に多くの新卒が入る{e(subs[0]['name'])}などの事業会社の水準<b>ではありません</b>。"
            '事業会社の数字は<a href="#subsidiaries">連結子会社の指標</a>を見てください。'
        )
    else:
        tail = (
            "傘下の事業会社の水準<b>ではありません</b>。"
            "この会社の有価証券報告書には事業会社ごとの指標の記載がないため、当サイトも掲載していません。"
        )
    return f"""<div class="warn">
<strong>この会社は持株会社です。</strong>
下に出る平均年間給与・平均年齢・平均勤続年数は、<b>持株会社本体の従業員{num(emp, "人")}</b>のものです。
{tail}
</div>"""


STALE_DAYS = 300  # 決算期の違い（12月期と3月期で最大3ヶ月）を超えて古いもの


def _period(c: dict) -> dt.date:
    return dt.date.fromisoformat(c["latest"]["source"]["period_end"])


def stale_note(c: dict, newest: dt.date) -> str:
    """最新の有報が他社より一世代古い会社を黙って並べない。

    アサヒグループHDは2025年12月期の有報を2026年7月時点で提出しておらず、
    最新は2024年12月期にとどまる。数字を消すのではなく、古いと書く。
    """
    gap = (newest - _period(c)).days
    if gap < STALE_DAYS:
        return ""
    return f"""<div class="warn">
<strong>この会社の数値は他社より古い期のものです。</strong>
掲載しているのは<b>{e(c["latest"]["source"]["period_end"][:7])}期</b>の有価証券報告書で、
これがEDINETで確認できる最新のものです（同じ表に並ぶ他社は{e(newest.isoformat()[:7])}期）。
以後の事業年度の有価証券報告書は、当サイトのデータ取得時点で提出されていません。
</div>"""


def trend_breaks(c: dict) -> str:
    out = []
    if not c["salary_trend_comparable"]:
        notes = " ".join(c["salary_breaks"].values())
        out.append(
            '<div class="warn"><strong>平均年間給与の算定基準が期の途中で変わっています。</strong>'
            f'年度をまたいだ増減率は出しません。有報の注記：<q>{e(notes)}</q></div>'
        )
    if c["headcount_breaks"]:
        notes = " ".join(c["headcount_breaks"].values())
        out.append(
            '<div class="note"><strong>従業員数に組織再編による不連続があります。</strong>'
            f'給与の算定基準は変わっていません。有報の注記：<q>{e(notes)}</q></div>'
        )
    return "\n".join(out)


_LC_ID = 0  # <linearGradient id> の衝突を避けるための連番。1ページに複数グラフが乗るため


def line_chart(series: dict, fmt) -> str:
    """年次推移の折れ線グラフ。SVGを直接組み立てる——JSもチャートライブラリも使わない
    （壊れる部品を増やさない、という設計方針を崩さないため）。

    viewBox 座標系を使い、CSSで width:100% にして親要素の幅に合わせる。
    データ点が1つしかない年は折れ線にならないので、その場合だけ数値を出す。
    """
    global _LC_ID
    if not series:
        return f"<p>{NA}</p>"
    items = sorted(series.items())
    if len(items) < 2:
        (k, v), = items
        return f'<p class="rate">{e(k[:7])}期　{fmt(v)}</p>'

    _LC_ID += 1
    gid = f"lc-fill-{_LC_ID}"
    W, H = 640, 220
    pad_l, pad_r, pad_t, pad_b = 8, 8, 30, 28
    plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b
    vals = [v for _, v in items]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    n = len(items)

    def px(i: int) -> float:
        return pad_l + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)

    def py(v: float) -> float:
        return pad_t + plot_h * (1 - (v - lo) / span)

    pts = [(px(i), py(v)) for i, (_, v) in enumerate(items)]
    line_path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area_path = (
        line_path
        + f" L {pts[-1][0]:.1f},{pad_t + plot_h:.1f}"
        + f" L {pts[0][0]:.1f},{pad_t + plot_h:.1f} Z"
    )
    dots = "".join(f'<circle class="chart-dot" cx="{x:.1f}" cy="{y:.1f}" r="4"/>' for x, y in pts)

    # 両端の点はtext-anchor:middleのままだとラベルがviewBox外にはみ出てSVGの
    # overflow:hiddenで欠ける（実ブラウザで確認して発覚）。両端だけstart/endに寄せる
    def anchor(i: int) -> str:
        if n == 1 or 0 < i < n - 1:
            return "middle"
        return "start" if i == 0 else "end"

    x_labels = "".join(
        f'<text class="chart-x" x="{x:.1f}" y="{H - 6}" style="text-anchor:{anchor(i)}">{e(k[:7])}</text>'
        for i, ((k, _), (x, _)) in enumerate(zip(items, pts))
    )
    # 値ラベルは点の上に出す。一番上の点だけはグラフ枠からはみ出るので下に逃がす
    v_labels = "".join(
        f'<text class="chart-val" x="{x:.1f}" y="{(y - 12 if y > pad_t + 14 else y + 22):.1f}" style="text-anchor:{anchor(i)}">{fmt(v)}</text>'
        for i, ((x, y), (_, v)) in enumerate(zip(pts, items))
    )
    return f"""<div class="chart-wrap"><svg class="linechart" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="年次推移の折れ線グラフ">
<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="var(--accent)" stop-opacity=".22"/>
<stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>
</linearGradient></defs>
<line class="grid-line" x1="{pad_l}" y1="{pad_t + plot_h}" x2="{W - pad_r}" y2="{pad_t + plot_h}"/>
<path class="chart-area" fill="url(#{gid})" d="{area_path}"/>
<path class="chart-line" d="{line_path}"/>
{dots}{x_labels}{v_labels}
</svg></div>"""


def donut(pct_value: float | None, size: int = 96, stroke: int = 11, method: str = "") -> str:
    """比率1つをドーナツ（リング）グラフで見せる。0〜100%の範囲にクランプして描き、
    100%を超える値（男性育休取得率など）は中央の数字にそのまま出して視覚的な嘘を避ける。
    非公表（None）はグレーの空リングに「非公表」とだけ書く——0%と混同しない。
    """
    r = (size - stroke) / 2
    cx = cy = size / 2
    circumference = 2 * math.pi * r
    track = f'<circle class="donut-track" cx="{cx}" cy="{cy}" r="{r}" stroke-width="{stroke}"/>'
    if pct_value is None:
        return (
            f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" role="img" aria-label="非公表">'
            f"{track}"
            f'<text class="donut-na" x="{cx}" y="{cy}">{NA}</text></svg>'
        )
    # 値の大小で色分けしない（青一色）。「女性管理職比率は低いほど赤」のような評価を
    # このサイトが下すと、数字に意味づけをしてしまい「事実だけを機械的に出す」という
    # 設計原則に反する。100%超（男性育休取得率）はリングを満タンにしたうえで中央の
    # 数字はそのまま実値を出す——リングを101%分描こうとして崩れることはない。
    clamped = max(0.0, min(pct_value, 100.0))
    dash = circumference * clamped / 100
    arc = (
        f'<circle class="donut-value" cx="{cx}" cy="{cy}" r="{r}" stroke-width="{stroke}" '
        f'stroke-dasharray="{dash:.2f} {circumference:.2f}" '
        f'transform="rotate(-90 {cx} {cy})"/>'
    )
    label = f"{pct_value:.1f}%"
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" role="img" aria-label="{label}">'
        f"{track}{arc}"
        f'<text class="donut-center" x="{cx}" y="{cy}">{label}</text></svg>'
    )


def hbars(rows: list[tuple[str, str, float | None, str | None]], fmt, scale: str = "group") -> str:
    """横棒グラフ。(会社名, リンク先href, 値, ロゴアイコンURL) の並びをCSSの幅%だけで描く。

    SVGではなくCSS幅を使う理由：折れ線グラフでラベルがviewBox外にはみ出て欠ける不具合を
    実ブラウザで踏んだため。横棒はテキストが通常のHTMLノードなら原理的にクリップされない。
    scale="group": その並びの最大値を100%とする（給与など0-100%に自然な上限が無い指標向け）。
    scale="pct100": 値そのもの（0.0-1.0の比率）を0-100%として使う（女性管理職比率など）。
    """
    vals = [v for _, _, v, _ in rows if v is not None]
    if not vals:
        return f"<p>{NA}</p>"
    if scale == "pct100":
        widths = [v * 100 if v is not None else None for _, _, v, _ in rows]
    else:
        m = max(vals) or 1
        widths = [v / m * 100 if v is not None else None for _, _, v, _ in rows]
    items = []
    for (name, href, v, icon), w in zip(rows, widths):
        label = logo_img(icon) + (e(name) if not href else f'<a href="{e(href)}">{e(name)}</a>')
        if v is None:
            items.append(
                f'<div class="hbar-row"><span class="hbar-name" title="{e(name)}">{label}</span>'
                f'<span class="hbar-track"></span><span class="hbar-val na">{NA}</span></div>'
            )
        else:
            items.append(
                f'<div class="hbar-row"><span class="hbar-name" title="{e(name)}">{label}</span>'
                f'<span class="hbar-track"><span class="hbar-fill" style="width:{max(w, 1.5):.1f}%"></span></span>'
                f'<span class="hbar-val">{fmt(v)}</span></div>'
            )
    return f'<div class="hbars">{"".join(items)}</div>'


def change_rate(series: dict) -> str:
    """「5年で」と決め打ちしない。決算期は企業ごとに違い（12月期・2月期・8月期）、
    上場が新しい会社は5期そろわない。実際に手元にある最初と最後の期を書く。"""
    items = sorted(series.items())
    if len(items) < 2:
        return NA
    (k0, v0), (k1, v1) = items[0], items[-1]
    r = (v1 / v0 - 1) * 100
    cls = "up" if r > 0 else "down"
    return f'{e(k0[:7])}期 → {e(k1[:7])}期で <span class="{cls}">{r:+.1f}%</span>'


def childcare_cell(ratio, method) -> str:
    if ratio is None:
        return NA
    tag = "第2号方式" if method and "2号" in method else "原則方式" if method else "方式不明"
    cls = "method2" if "2号" in tag else "method1"
    return f'{pct(ratio)} <span class="method {cls}" title="{e(method or "")}">{tag}</span>'


CHILDCARE_CAVEAT = (
    "男性育休取得率には2つの算出方式があります。<b>「第2号方式」（育児休業等及び育児目的休暇の取得割合）は"
    "育児目的休暇を含むため、原則方式より高く出ます。</b>方式が違う会社どうしを並べて順位を付けることはできません。"
    "また、分母が当期に配偶者が出産した男性、分子が当期に取得した男性であるため、"
    "前期に生まれた子で当期に取得した人が加わり<b>100%を超えることがあります</b>。"
)
WAGE_CAVEAT = (
    "「男女の賃金の差異」は<b>男性の賃金を100としたときの女性の賃金の割合</b>です。"
    "64.7%なら「女性は男性の64.7%」の意味で、数値が小さいほど差が大きいことを表します。"
    "職階や勤続年数の構成差を含んだ値であり、同一労働に対する賃金差ではありません。"
)
SALARY_CAVEAT = (
    "平均年間給与は<b>提出会社（単体）の従業員が分母</b>です。連結従業員数ではありません。"
    "有報が定める記載事項で、賞与・基準外賃金を含み、退職金は含みません。"
)


# ---------------------------------------------------------------- 企業ページ

# 東証33業種は学生の比較単位より粗い。「情報・通信業」165社にはNTT（33万人）と
# 従業員数千人のネット企業が同居し、1つの表に並べても比較にならない。
# ただし1,549社をサブ業界へ人手で仕分けると、誤分類がそのまま「同業として並べる」
# という商品を壊す。そこで**判断の要らない客観軸＝連結従業員数**で帯に切る。
SIZE_BANDS = [
    (100_000, "従業員10万人以上"),
    (10_000, "従業員1万人以上10万人未満"),
    (1_000, "従業員1,000人以上1万人未満"),
    (0, "従業員1,000人未満"),
]
BAND_SPLIT_MIN = 40  # これ未満の業界は分けない（分けると各帯が数社になって比較にならない）


def size_band(c: dict) -> tuple[int, str]:
    n = c["latest"]["employees"]["consolidated"]
    if n is None:
        return (-1, "従業員数が非公表")
    for i, (lo, label) in enumerate(SIZE_BANDS):
        if n >= lo:
            return (len(SIZE_BANDS) - i, label)
    return (-1, "従業員数が非公表")


def external_links(c: dict) -> str:
    """公式サイトと採用ページへの導線。

    **このサイトで唯一、有価証券報告書以外を出典にする箇所。** ただし出しているのは
    数値ではなくリンクである。公式サイトは法人番号（国が振った一意な識別子）で
    Wikidata を引いて特定しており、社名の表記ゆれで別会社に飛ぶ余地を消してある。
    採用ページは公式トップのリンクから辿った結果で、見つからなければ黙って出さない。
    """
    L = c.get("links") or {}
    if not L.get("official"):
        return ""
    host = urllib.parse.urlsplit(L["official"]).netloc
    items = [
        f'<a class="extlink" href="{e(L["official"])}" rel="nofollow noopener" target="_blank">'
        f'{logo_img(c.get("icon"))}公式サイト <small>{e(host)}</small></a>'
    ]
    if L.get("recruit"):
        items.append(
            f'<a class="extlink is-recruit" href="{e(L["recruit"])}" rel="nofollow noopener" target="_blank">'
            f'採用ページ <small>{e(urllib.parse.urlsplit(L["recruit"]).netloc)}</small></a>'
        )
    note = (
        "" if L.get("recruit") else
        '<p class="caveat">採用ページのリンクは、公式サイトのトップから辿れたときだけ載せています。'
        "この会社では見つけられませんでした（トップの案内がJavaScriptで描かれている、"
        "robots.txt で対象外、などの理由）。推測のURLは載せません。</p>"
    )
    return f'<div class="extlinks">{"".join(items)}</div>{note}'


def oku(v) -> str:
    """億円表示。桁が大きい財務値を就活生が読める単位に落とす。"""
    if v is None:
        return NA
    return f"{v / 100_000_000:,.0f}億円" if abs(v) >= 100_000_000 else f"{v / 1_000_000:,.0f}百万円"


def financial_section(c: dict) -> str:
    """財務（連結）。**平均年収は提出会社・財務は連結**という違いを必ず書く。

    ここを混ぜると「連結の売上を単体の人数で割る」類の誤りになる。1人当たりの分母は
    extract_financials.py が財務と同じスコープで選んでおり、その分母をそのまま表示する。
    """
    f = c.get("fin")
    if not f:
        return ""
    lat = f["latest"]
    v, pc = lat["values"], lat["per_capita"]
    if not v:
        return ""

    rev_label = lat.get("revenue_label") or "売上高"
    std = lat.get("accounting_standard", "")
    scope = lat.get("scope", "連結")

    rows = [("決算期", f'{e(lat["period_end"][:7])}期'), ("会計基準", e(std)), ("集計範囲", e(scope))]
    for label, key in ((rev_label, "revenue"), ("営業利益", "operating_income"),
                       ("経常利益", "ordinary_income"), ("親会社株主に帰属する当期純利益", "net_income"),
                       ("総資産", "total_assets"), ("純資産", "net_assets")):
        if key in v:
            rows.append((label, oku(v[key])))
    if "equity_ratio" in v:
        rows.append(("自己資本比率", pct(v["equity_ratio"])))
    if "roe" in v:
        rows.append(("自己資本利益率（ROE）", pct(v["roe"])))
    kv = "".join(f'<tr><th scope="row">{k}</th><td>{val}</td></tr>' for k, val in rows)

    per = ""
    if pc:
        denom = lat.get("per_capita_denominator")
        denom_label = lat.get("per_capita_denominator_label") or ""
        prows = []
        if "revenue_per_employee" in pc:
            prows.append((f"1人当たり{rev_label}", oku(pc["revenue_per_employee"])))
        if "operating_income_per_employee" in pc:
            prows.append(("1人当たり営業利益", oku(pc["operating_income_per_employee"])))
        pkv = "".join(f'<tr><th scope="row">{e(k)}</th><td>{val}</td></tr>' for k, val in prows)
        per = f"""<h3>従業員1人当たり</h3>
<table class="kv">{pkv}</table>
<p class="caveat">分母は{e(denom_label)}{num(denom, "人") if denom else ""}です。
仲介取引の多い商社や、少人数で大きな資産を動かす業態は大きく出ます。人数の効率を見る指標であって、
給与の高さを意味しません。</p>"""

    trend = {y["period_end"]: y["values"]["revenue"] for y in f["years"] if y["values"].get("revenue")}
    trend_html = ""
    if len(trend) >= 2:
        scopes = {y.get("scope") for y in f["years"]}
        mixed = (
            '<p class="caveat">年度によって連結・単体の別が変わっているため、この推移は連続していません。</p>'
            if len(scopes) > 1 else ""
        )
        trend_html = f'<h3>{e(rev_label)}の推移</h3>{line_chart(trend, oku)}{mixed}'

    # 海外売上高比率。有報の「地域ごとの情報」から読み、同じ有報のXBRLの売上高と
    # 一致したものだけが data/overseas に入っている（extract_overseas.py）
    ov = c.get("overseas")
    overseas_html = ""
    if ov:
        ovrows = "".join(
            f'<tr><th scope="row">{e(k)}</th><td>{val}</td></tr>' for k, val in (
                ("海外売上高比率", f'<b>{pct(ov["overseas_ratio"])}</b>'),
                ("海外", oku(ov["overseas"])),
                ("日本", oku(ov["japan"])),
                ("合計", oku(ov["total"])),
            )
        )
        overseas_html = f"""<h3>海外売上高比率</h3>
<table class="kv">{ovrows}</table>
<p class="caveat">有価証券報告書の連結財務諸表注記「地域ごとの情報」から抽出しました。
合計が同じ有報のXBRLに記載された売上高と一致することを確認しています（差 {ov.get("diff_vs_xbrl", 0) * 100:.2f}%）。
<b>海外売上があることと、海外駐在の機会があることは別です。</b>この数値は売上の出どころであって、
働く場所の内訳ではありません。</p>"""

    ifrs_note = (
        '<p class="caveat">IFRSの営業利益は日本基準と定義が異なります（非経常項目の扱い）。'
        "基準の違う会社と営業利益を直接比べないでください。</p>" if std == "IFRS" else ""
    )
    bank_note = (
        '<p class="caveat">銀行・保険業に「売上高」という科目はありません。'
        "有価証券報告書の経常収益を掲載しています。</p>" if "経常収益" in rev_label else ""
    )

    return f"""<section>
<h2>財務（{e(scope)}）</h2>
<p class="caveat"><b>この節だけ集計範囲が違います。</b>平均年間給与・平均勤続年数は提出会社（単体）の数値ですが、
財務は{e(scope)}の数値です。人数も{e(scope)}のものを使っています。</p>
<table class="kv">{kv}</table>
{bank_note}{ifrs_note}
{per}
{overseas_html}
{trend_html}
</section>"""


def peer_rank(c: dict, pool: list[dict], key: str, ascending: bool = False) -> tuple[int | None, int]:
    """指定した指標でpool内のcの順位（1始まり）と、値のある会社数を返す。
    値がNoneの会社は順位付けの対象外（sort_key()と同じ規約で末尾に落ちる）。
    """
    ranked = sorted(pool, key=lambda x: sort_key(x["latest"]["reporting_company"][key], not ascending))
    total = sum(1 for x in pool if x["latest"]["reporting_company"][key] is not None)
    for i, x in enumerate(ranked, start=1):
        if x["edinet_code"] == c["edinet_code"]:
            return (i if i <= total else None), total
    return None, total


def metric_card(c: dict, peers: list[dict], companies: list[dict], key: str, label: str,
                 fmt_display, ascending: bool = False, rank_suffix: str = "", sub_fn=None) -> dict:
    """基本データカード1枚ぶんの数値・バー%・業界内順位・全体パーセンタイルを計算する。
    業界内順位はpeers（同業他社、自分を含む）、全体パーセンタイルはcompanies（全社）が基準。
    """
    v = c["latest"]["reporting_company"][key]
    if v is None:
        return {"label": label, "display": NA, "sub": "", "bar_pct": "1.5%", "rank_label": "", "pct_label": ""}
    rank_g, total_g = peer_rank(c, peers, key, ascending)
    rank_o, total_o = peer_rank(c, companies, key, ascending)
    group_vals = [x["latest"]["reporting_company"][key] for x in peers
                  if x["latest"]["reporting_company"][key] is not None]
    m = max(group_vals) if group_vals else v
    bar_pct = max(1.5, min(100.0, v / m * 100)) if m else 1.5
    rank_label = f"{c['peer_group']}{total_g}社中{rank_g}位{rank_suffix}" if rank_g else ""
    pct_label = f"全体上位{rank_o / total_o * 100:.1f}%" if rank_o else ""
    return {
        "label": label, "display": fmt_display(v), "sub": sub_fn(v) if sub_fn else "",
        "bar_pct": f"{bar_pct:.1f}%", "rank_label": rank_label, "pct_label": pct_label,
    }


def identity_box(icon: str | None, initial: str, size: int) -> str:
    """企業ロゴ。実物のアイコンが無ければ頭文字1文字のフォールバックにする。"""
    if icon:
        inner = f'<img src="{e(icon)}" alt="" width="{size - 32}" height="{size - 32}" style="width:{size - 32}px;height:{size - 32}px;object-fit:contain">'
    else:
        inner = e(initial)
    return f'<div class="cp-logo" style="width:{size}px;height:{size}px;font-size:{size * 0.36:.0f}px">{inner}</div>'


def ring_style(pct_value: float | None, color: str) -> str:
    if pct_value is None:
        return "background:conic-gradient(#eef0f4 0 100%)"
    clamped = max(0.0, min(pct_value, 100.0))
    return f"background:conic-gradient({color} 0 {clamped:.1f}%,#eef0f4 {clamped:.1f}% 100%)"


def company_page(c: dict, peers: list[dict], companies: list[dict], fetched: str, newest: dt.date) -> str:
    L = c["latest"]
    emp, rc, div = L["employees"], L["reporting_company"], L["diversity"]
    src = L["source"]
    period = src["period_end"][:7]
    gslug = GROUP_SLUG.get(c["peer_group"], c["peer_group"])
    initial = c["short"][0] if c["short"] else "?"

    # ---- 企業アイデンティティ帯 ----
    links = c.get("links") or {}
    official = links.get("official")
    recruit = links.get("recruit")
    saiyo_urls = (c.get("saiyo") or {}).get("source_urls") or []
    recruit_effective = recruit or (saiyo_urls[0] if saiyo_urls else None)
    actions = []
    if official:
        host = urllib.parse.urlsplit(official).netloc.removeprefix("www.")
        actions.append(
            f'<a class="cp-official" href="{e(official)}" rel="nofollow noopener" target="_blank">'
            f'公式サイト<span class="cp-action-host">{e(host)}</span></a>'
        )
        if recruit_effective:
            rhost = urllib.parse.urlsplit(recruit_effective).netloc.removeprefix("www.")
            actions.append(
                f'<a class="cp-recruit" href="{e(recruit_effective)}" rel="nofollow noopener" target="_blank">'
                f'採用ホームページ<span class="cp-action-host">{e(rhost)}</span></a>'
            )
        else:
            actions.append('<p class="cp-recruit-note">採用ページは見つけられませんでした。</p>')
    actions.append(
        f'<a class="cp-compare-cta" href="../gyoukai/{e(gslug)}.html">{e(c["peer_group"])}{len(peers)}社を比較する →</a>'
    )

    identity = f"""<div class="cp-identity">
<div class="cp-identity-inner">
<nav class="cp-crumb"><a href="../index.html">トップ</a> › <a href="../gyoukai/{e(gslug)}.html">{e(c["peer_group"])}</a> › {e(c["short"])}</nav>
<div class="cp-identity-row">
{identity_box(c["icon"], initial, 84)}
<div class="cp-identity-body">
<h1>{e(c["short"])}の平均年収・男女の賃金の差異・従業員数</h1>
<div class="cp-identity-badges">
<a class="cp-group-badge" href="../gyoukai/{e(gslug)}.html">{e(c["peer_group"])}</a>
<span class="cp-identity-meta">業種：{e(c["industry"])}</span>
<span class="cp-identity-meta">証券コード {e(c["sec_code"][:4])}</span>
<span class="cp-identity-meta">／ {e(period)}期</span>
</div>
</div>
<div class="cp-identity-actions">{"".join(actions)}</div>
</div>
</div>
</div>"""

    # ---- sticky sub-nav（採用枠タブはJS側 initSaiyoInject が該当会社だけ動的追加） ----
    sections = [
        ("基本データ", "#kihon"), ("給与の推移", "#suii"), ("多様性の指標", "#tayosei"),
        ("同業他社と比較", "#hikaku"), ("出典", "#shutten"),
    ]
    subnav = '<div class="cp-subnav"><div class="cp-subnav-inner">' + "".join(
        f'<a href="{href}">{e(label)}</a>' for label, href in sections
    ) + "</div></div>"

    # ---- 基本データ（3指標カード＋従業員数カード） ----
    salary_card = metric_card(c, peers, companies, "average_annual_salary_yen", "平均年間給与", man, sub_fn=yen)
    tenure_card = metric_card(c, peers, companies, "average_tenure_years", "平均勤続年数",
                               lambda v: dec1(v, "年"))
    age_card = metric_card(c, peers, companies, "average_age_years", "平均年齢",
                            lambda v: dec1(v, "歳"), ascending=True, rank_suffix="（若い順）")

    def _metric_card_html(m: dict) -> str:
        badges = ""
        if m["rank_label"]:
            badges = f'<span class="cp-metric-rank">{e(m["rank_label"])}</span>'
            if m["pct_label"]:
                badges += f'<span class="cp-metric-pct">{e(m["pct_label"])}</span>'
        sub = f'<div class="cp-metric-sub">{e(m["sub"])}</div>' if m["sub"] else ""
        return f"""<div class="cp-card cp-metric-card">
<div class="cp-metric-label">{e(m["label"])}</div>
<div class="cp-metric-value">{m["display"]}</div>
{sub}
<div class="cp-metric-bar"><div class="cp-metric-bar-fill" style="width:{m["bar_pct"]}"></div></div>
<div class="cp-metric-badges">{badges}</div>
</div>"""

    emp_card = f"""<div class="cp-card cp-metric-card">
<div class="cp-metric-label">従業員数</div>
<div class="cp-metric-value">{num(emp["reporting_company"], "人")}</div>
<div class="cp-metric-sub">提出会社（単体）</div>
<div class="cp-emp-divider"></div>
<div class="cp-metric-value cp-metric-value-sm">{num(emp["consolidated"], "人")}</div>
<div class="cp-metric-sub">連結</div>
</div>"""

    kihon_section = f"""<section id="kihon" class="cp-section">
<h2>基本データ（{e(period)}期・提出会社）</h2>
<p class="cp-lead">同じ業種の中での位置と、{len(companies)}社全体での位置を併記しています。</p>
<div class="cp-metrics">{_metric_card_html(salary_card)}{_metric_card_html(tenure_card)}{_metric_card_html(age_card)}{emp_card}</div>
<details class="cp-details"><summary>この数字の読み方<span class="cp-details-hint">（平均年間給与の分母について）</span></summary>
<p>{SALARY_CAVEAT}</p></details>
</section>"""

    # ---- 平均年間給与の推移 ----
    items = sorted(c["trend"]["average_annual_salary_yen"].items())
    trend_up, trend_range_label, trend_delta, trend_abs, trend_years = True, "", "—", "", ""
    if len(items) > 1 and c["salary_trend_comparable"]:
        (k0, v0), (k1, v1) = items[0], items[-1]
        pct_delta = (v1 / v0 - 1) * 100
        trend_up = pct_delta >= 0
        trend_range_label = f"{e(k0[:7])}期 → {e(k1[:7])}期"
        trend_delta = f"{'+' if trend_up else ''}{pct_delta:.1f}%"
        trend_abs = f"{'+' if trend_up else '−'}{abs(round((v1 - v0) / 10000)):,}万円"
        trend_years = f"{len(items)}期分"
    comparable_note = (
        "算定基準が変わった年があるため、年ごとの単純比較はできません。"
        if not c["salary_trend_comparable"] else "各年の有価証券報告書の記載値です。"
    )
    trend_color = "#1b7f5f" if trend_up else "#b4443a"
    trend_bg = "linear-gradient(140deg,#eaf7f1,#dff0e8)" if trend_up else "linear-gradient(140deg,#fdeeec,#f9e3e0)"
    trend_border = "#c3e6d5" if trend_up else "#f0cdc7"
    trend_arrow = "↗" if trend_up else "↘"

    suii_section = f"""<section id="suii" class="cp-section">
<h2>平均年間給与の推移</h2>
<div class="cp-trend">
<div class="cp-trend-info" style="background:{trend_bg};border-color:{trend_border}">
<div class="cp-trend-range">{trend_range_label}</div>
<div class="cp-trend-delta" style="color:{trend_color}">{trend_delta}<span class="cp-trend-arrow">{trend_arrow if trend_delta != "—" else ""}</span></div>
<div class="cp-trend-abs" style="color:{trend_color}">{trend_abs}</div>
<div class="cp-trend-note">有報{trend_years}の記載値の差。</div>
</div>
<div class="cp-card cp-trend-chart">{line_chart(c["trend"]["average_annual_salary_yen"], man)}</div>
</div>
<p class="caveat">単位は万円。{e(comparable_note)}</p>
<h3>従業員数（提出会社）の推移</h3>
{line_chart(c["trend"]["employees_reporting_company"], lambda v: num(v, "人"))}
</section>"""

    # ---- 多様性の指標 ----
    div_keys = ["female_manager_ratio", "female_to_male_wage_ratio_all", "female_to_male_wage_ratio_regular",
                "female_to_male_wage_ratio_nonregular", "male_childcare_leave_ratio"]
    div_empty = all(div.get(k) is None for k in div_keys)
    if div_empty:
        to_subs = (
            '実際の勤務先となる<a href="#hikaku">同業他社の指標</a>を参照してください。'
            if L.get("subsidiaries") else ""
        )
        tayosei_section = f"""<section id="tayosei" class="cp-section">
<h2>多様性の指標</h2>
<p class="note">提出会社（{e("持株会社本体" if c.get("is_holding") else "本体")}）の女性管理職比率・男女の賃金の差異・男性育休取得率は、
有価証券報告書に記載がありません。{to_subs}</p>
</section>"""
    else:
        scope = div.get("scope") or "提出会社"
        scope_note = "" if scope == "提出会社" else f'<p class="small">対象範囲：{e(scope)}</p>'
        method_tag = None
        if div["male_childcare_leave_method"]:
            method_tag = "第2号方式" if "2号" in div["male_childcare_leave_method"] else "原則方式"
        fm, wr, cc = div["female_manager_ratio"], div["female_to_male_wage_ratio_all"], div["male_childcare_leave_ratio"]
        diversity_items = [
            ("女性管理職比率", fm, "#2f4bd6", "提出会社（単体）の管理職に占める女性の割合。", None),
            ("男女の賃金の差異（全労働者）", wr, "#5570e6", "男性の賃金を100としたときの女性の賃金の割合。", None),
            ("育児休業取得率（男性）", cc, "#1b7f5f",
             "当期に配偶者が出産した男性のうち、取得した男性の割合。" if cc is not None else "この企業の有報からは未取得です。",
             method_tag),
        ]
        cards = []
        for label, v, color, note, tag in diversity_items:
            value_html = "未取得" if v is None and "育児" in label else (NA if v is None else pct(v))
            tag_html = f'<span class="cp-diversity-tag">{e(tag)}</span>' if tag else ""
            cards.append(f"""<div class="cp-card cp-diversity-card">
<div class="cp-ring" style="{ring_style(v * 100 if v is not None else None, color)}">
<div class="cp-ring-inner{"" if v is not None else " cp-ring-inner-na"}">{value_html}</div></div>
<div class="cp-diversity-body">
<div class="cp-diversity-head"><span class="cp-diversity-label">{e(label)}</span>{tag_html}</div>
<div class="cp-diversity-note">{e(note)}</div>
</div>
</div>""")
        div_trend = ""
        if c["trend"].get("female_manager_ratio") or c["trend"].get("female_to_male_wage_ratio_all"):
            div_trend = f"""<div class="cols">
<div class="col"><h3>女性管理職比率</h3>{line_chart(c["trend"].get("female_manager_ratio", {}), pct)}</div>
<div class="col"><h3>男女の賃金の差異（全労働者）</h3>{line_chart(c["trend"].get("female_to_male_wage_ratio_all", {}), pct)}</div>
</div>"""
        tayosei_section = f"""<section id="tayosei" class="cp-section">
<h2>多様性の指標</h2>
{scope_note}
<div class="cp-diversity">{"".join(cards)}</div>
{div_trend}
<details class="cp-details"><summary>この数字の読み方<span class="cp-details-hint">（男女の賃金の差異）</span></summary><p>{WAGE_CAVEAT}</p></details>
<details class="cp-details"><summary>この数字の読み方<span class="cp-details-hint">（育児休業取得率の算出方式）</span></summary><p>{CHILDCARE_CAVEAT}</p></details>
</section>"""

    # ---- 規模の近い同業他社（連結従業員数が近い順に7社＋自社。列名クリックで並び替え） ----
    mine = c["latest"]["employees"]["consolidated"]
    others = [x for x in peers if x["edinet_code"] != c["edinet_code"] and x["latest"]["employees"]["consolidated"]]
    near = sorted(others, key=lambda x: abs(x["latest"]["employees"]["consolidated"] - mine))[:7] if mine else others[:7]
    shown = sorted([c] + near, key=lambda x: -(x["latest"]["employees"]["consolidated"] or 0))

    pcols = [
        ("emp", "従業員数(連結)", lambda x: x["latest"]["employees"]["consolidated"], lambda v: num(v, "人")),
        ("salary", "平均年間給与", lambda x: x["latest"]["reporting_company"]["average_annual_salary_yen"], man),
        ("tenure", "平均勤続年数", lambda x: x["latest"]["reporting_company"]["average_tenure_years"],
         lambda v: dec1(v, "年")),
        ("fm", "女性管理職比率", lambda x: x["latest"]["diversity"]["female_manager_ratio"], pct),
    ]
    maxes = {}
    for key, _, get, _ in pcols:
        vals = [get(x) for x in shown if get(x) is not None]
        maxes[key] = max(vals) if vals else 1

    head_cells = "".join(
        f'<th class="cp-ptable-th"><button type="button" class="cp-sort-btn" data-key="{key}">{e(label)}'
        f'<span class="cp-sort-arrow"></span></button></th>'
        for key, label, _, _ in pcols
    )
    prows = []
    for x in shown:
        is_self = x["edinet_code"] == c["edinet_code"]
        x_initial = x["short"][0] if x["short"] else "?"
        logo = (f'<img src="{e(x["icon"])}" alt="" width="16" height="16" style="width:16px;height:16px;object-fit:contain">'
                if x["icon"] else e(x_initial))
        self_badge = ' <span class="cp-self-badge">この会社</span>' if is_self else ""
        name_html = e(x["short"]) if is_self else f'<a href="{e(x["slug"])}.html">{e(x["short"])}</a>'
        cells = []
        for key, _, get, fmt in pcols:
            v = get(x)
            width = max(1.5, min(100.0, (v / maxes[key] * 100))) if v is not None and maxes[key] else 0.0
            fill_cls = "cp-bar-self" if is_self else "cp-bar-other"
            cells.append(
                f'<td class="cp-ptable-td" data-key="{key}" data-val="{v if v is not None else ""}">'
                f'<div class="cp-cell-val{"" if v is not None else " cp-cell-na"}">{fmt(v)}</div>'
                f'<div class="cp-bar-track"><div class="cp-bar-fill {fill_cls}" style="width:{width:.1f}%"></div></div></td>'
            )
        prows.append(
            f'<tr class="{"cp-row-self" if is_self else ""}">'
            f'<th scope="row" class="cp-ptable-name"><span class="cp-row-logo">{logo}</span>{name_html}{self_badge}</th>'
            + "".join(cells) + "</tr>"
        )

    hikaku_section = f"""<section id="hikaku" class="cp-section">
<h2>規模の近い同業他社</h2>
<p class="cp-lead">{e(c["peer_group"])}は{len(peers)}社あり、規模も事業もばらばらです。
連結従業員数が{e(c["short"])}に近い順に並べています。列名をクリックすると並び替わります。</p>
<div class="scroll"><table class="cp-ptable" id="cp-peer-table">
<thead><tr><th class="cp-ptable-th cp-ptable-th-name">会社</th>{head_cells}</tr></thead>
<tbody>{"".join(prows)}</tbody></table></div>
<p class="caveat">平均年間給与・平均勤続年数・女性管理職比率は提出会社（単体）、従業員数は連結です。</p>
</section>"""

    # ---- 出典 ----
    shutten_section = f"""<section id="shutten" class="cp-section cp-card cp-source">
<h2>出典</h2>
<ul>
<li>{e(src['doc_type'])}（{e(src['period_end'])} 期）
　── <a href="{EDINET_VIEW.format(doc_id=src['doc_id'])}" rel="nofollow">EDINETで閲覧</a>
　／ <a href="{EDINET_PDF.format(doc_id=src['doc_id'])}" rel="nofollow">原典PDF</a></li>
<li>提出日時 {e(src['submit_datetime'])}／書類管理番号 {e(src['doc_id'])}／当サイトのデータ取得日 {e(fetched)}</li>
<li>ライセンス：<a href="{PDL_URL}" rel="nofollow">公共データ利用規約（PDL1.0）</a></li>
</ul>
<p class="processing">{e(PROCESSING_NOTICE)}</p>
</section>"""

    # ---- 既存セクション（今回の新デザインのプレビューには出てこないが、削除しない） ----
    p = c.get("prose") or {}
    prose_html = f'<section><h2>事業の内容</h2><p>{e(p["business"])}</p></section>' if p.get("business") else ""

    subs = L.get("subsidiaries") or []
    sub_html = ""
    if subs:
        sbody = "".join(
            f'<tr><th scope="row">{e(s["name"])}</th>'
            f'<td>{pct(s["female_manager_ratio"])}</td>'
            f'<td>{pct(s["female_to_male_wage_ratio_all"])}</td>'
            f'<td>{childcare_cell(s["male_childcare_leave_ratio"], s["male_childcare_leave_method"])}</td></tr>'
            for s in subs
        )
        sub_html = f"""<section id="subsidiaries">
<h2>連結子会社の指標（{len(subs)}社）</h2>
<p class="lead">持株会社は本体の指標を開示しない場合があります。実際に働く場となる事業会社の数字はこちらです。</p>
<div class="scroll"><table class="grid">
<thead><tr><th>会社</th><th>女性管理職比率</th><th>男女の賃金の差異（全労働者）</th><th>男性育休取得率</th></tr></thead>
<tbody>{sbody}</tbody></table></div>
<p class="caveat">{CHILDCARE_CAVEAT}</p>
</section>"""

    notes = list(L.get("notes") or [])
    for a in c.get("value_anomalies") or []:
        label = {"average_annual_salary_yen": "平均年間給与",
                 "average_age_years": "平均年齢",
                 "average_tenure_years": "平均勤続年数"}.get(a["field"], a["field"])
        notes.append(
            f'{a["period_end"][:7]}期の{label}を掲載していません。'
            f'有価証券報告書のXBRLに記載された値が {a["raw_value"]:,} で、{a["reason"]}ためです。'
            "提出企業のタグ付け誤りと考えられますが、正しい値を推測で置くことはしません。"
            "原典（下の出典欄のEDINET）で確認してください。"
        )
    notes = list(dict.fromkeys(notes))
    notes_html = ""
    if notes:
        items_html = "".join(f"<li>{e(n)}</li>" for n in notes)
        notes_html = f'<section class="notes"><h2>抽出上の注記</h2><ul>{items_html}</ul></section>'

    peer_links = " ".join(
        f'<a href="{e(x["slug"])}.html">{e(x["short"])}</a>' for x in peers if x["edinet_code"] != c["edinet_code"]
    )
    peers_all_html = f"""<section class="peers">
<h2>{e(c["peer_group"])}の全社</h2>
<p class="small">{peer_links}</p>
</section>""" if peer_links else ""

    legacy_html = "\n".join(x for x in [
        stale_note(c, newest), holding_warning(c), trend_breaks(c),
        financial_section(c), sub_html, notes_html, peers_all_html,
    ] if x)

    body = f"""{identity}
{subnav}
<div class="cp-main">
{prose_html}
{kihon_section}
{suii_section}
{tayosei_section}
{hikaku_section}
{legacy_html}
{shutten_section}
</div>"""
    title = f"{c['short']}の平均年収・男女の賃金の差異｜有価証券報告書（{period}期）｜{SITE_NAME}"
    desc = (
        f"{c['short']}の平均年間給与{man_plain(L['reporting_company']['average_annual_salary_yen'])}、"
        f"平均勤続年数{dec1_plain(L['reporting_company']['average_tenure_years'], '年')}、"
        f"女性管理職比率、男女の賃金の差異を有価証券報告書から抽出。{c['peer_group']}他社との横比較つき。"
    )
    return page(title, desc, body, depth=1, canonical=f"/kigyou/{c['slug']}.html")


# ---------------------------------------------------------------- 業界ページ

PEER_COLS = [
    # 決算期を必ず出す。同じ業界でも12月期・2月期・8月期が混じり、並べる年度が違う
    ("決算期", lambda c: c["latest"]["source"]["period_end"][:7], lambda v: f"{e(v)}期", False),
    ("平均年間給与", lambda c: c["latest"]["reporting_company"]["average_annual_salary_yen"], man, True),
    ("平均年齢", lambda c: c["latest"]["reporting_company"]["average_age_years"], lambda v: dec1(v, "歳"), False),
    ("平均勤続年数", lambda c: c["latest"]["reporting_company"]["average_tenure_years"], lambda v: dec1(v, "年"), True),
    ("従業員数(単体)", lambda c: c["latest"]["employees"]["reporting_company"], lambda v: num(v, "人"), True),
    ("従業員数(連結)", lambda c: c["latest"]["employees"]["consolidated"], lambda v: num(v, "人"), True),
    ("女性管理職比率", lambda c: c["latest"]["diversity"]["female_manager_ratio"], lambda v: pct(v), True),
    ("男女の賃金の差異", lambda c: c["latest"]["diversity"]["female_to_male_wage_ratio_all"], lambda v: pct(v), True),
]


def group_page(group: str, members: list[dict], fetched: str) -> str:
    newest = max(_period(c) for c in members)
    stale: list[str] = []
    members = sorted(
        members,
        key=lambda c: sort_key(c["latest"]["reporting_company"]["average_annual_salary_yen"], reverse=True),
    )
    has_holding = any(c.get("is_holding") for c in members)

    head = "".join(f"<th>{e(t)}</th>" for t, *_ in PEER_COLS)
    # 社数の多い業界は規模帯で区切る。NTTと従業員数千人の会社を同じ並びで見ても比較にならない
    banded = len(members) > BAND_SPLIT_MIN
    if banded:
        members = sorted(
            members,
            key=lambda c: (-size_band(c)[0],
                           sort_key(c["latest"]["reporting_company"]["average_annual_salary_yen"], True)),
        )
    rows = []
    current_band = None
    for c in members:
        if banded and size_band(c)[1] != current_band:
            current_band = size_band(c)[1]
            n_in_band = sum(1 for x in members if size_band(x)[1] == current_band)
            rows.append(
                f'<tr class="band"><th colspan="{len(PEER_COLS) + 1}" scope="colgroup">'
                f'{e(current_band)}（{n_in_band}社）</th></tr>'
            )
        cells = []
        for _, get, fmt, _hi in PEER_COLS:
            v = get(c)
            cells.append(f"<td>{fmt(v) if v is not None else NA}</td>")
        mark = ' <span class="hd" title="持株会社">持株</span>' if c.get("is_holding") else ""
        if (newest - _period(c)).days >= STALE_DAYS:
            mark += ' <span class="stale" title="他社より古い期">古い期</span>'
            stale.append(c["short"])
        rows.append(
            f'<tr><th scope="row"><a href="../kigyou/{e(c["slug"])}.html">{logo_img(c["icon"])}{e(c["short"])}</a>{mark}</th>'
            + "".join(cells) + "</tr>"
        )
    band_note = (
        '<p class="caveat">この業界は社数が多いため、<b>連結従業員数の規模帯</b>で区切ってあります。'
        "東京証券取引所の33業種区分は就活で比べたい単位より粗く、同じ「業界」に規模も事業も違う会社が"
        "混ざるためです。帯の中は平均年間給与の高い順です。</p>" if banded else ""
    )
    stale_caveat = (
        f'<p class="caveat"><b>{e("、".join(stale))}</b> は最新の有価証券報告書がまだ提出されていないため、'
        "一世代前の期の数値です。決算期の列を確認してください。</p>" if stale else ""
    )
    main_table = f"""<div class="scroll"><table class="grid rank">
<thead><tr><th>会社</th>{head}</tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>{band_note}{stale_caveat}"""

    # 5年推移は基準変更のあった会社を落とす。落とした事実を必ず書く。
    trend_rows, excluded = [], []
    for c in members:
        if not c["salary_trend_comparable"]:
            excluded.append(c["short"])
            continue
        s = sorted(c["trend"]["average_annual_salary_yen"].items())
        if len(s) < 2:
            continue
        trend_rows.append((c, s[0], s[-1], s[-1][1] / s[0][1] - 1))
    trend_rows.sort(key=lambda x: -x[3])
    # 決算期は行ごとに違いうる（12月期・2月期・8月期）。列見出しに年度を書くと嘘になるので、
    # 値の下にその行の期を添える。上場が新しい会社は5期そろわない。
    tr = "".join(
        f'<tr><th scope="row"><a href="../kigyou/{e(c["slug"])}.html">{logo_img(c["icon"])}{e(c["short"])}</a></th>'
        f'<td>{man(a[1])}<br><span class="small">{e(a[0][:7])}期</span></td>'
        f'<td>{man(b[1])}<br><span class="small">{e(b[0][:7])}期</span></td>'
        f'<td class="{"up" if r > 0 else "down"}">{r * 100:+.1f}%</td></tr>'
        for c, a, b, r in trend_rows
    )
    excl = (
        f'<p class="caveat">{e("、".join(excluded))} は平均年間給与の算定基準が期の途中で変わったため、'
        "増減率を出していません（各社ページに有報の注記を掲示しています）。</p>"
        if excluded else ""
    )
    trend_table = f"""<div class="scroll"><table class="grid rank">
<thead><tr><th>会社</th><th>最も古い期</th><th>直近の期</th><th>増減率</th></tr></thead>
<tbody>{tr}</tbody></table></div>{excl}"""

    cc = "".join(
        f'<tr><th scope="row"><a href="../kigyou/{e(c["slug"])}.html">{logo_img(c["icon"])}{e(c["short"])}</a></th>'
        f'<td>{childcare_cell(c["latest"]["diversity"]["male_childcare_leave_ratio"], c["latest"]["diversity"]["male_childcare_leave_method"])}</td></tr>'
        for c in members
    )
    childcare_table = f"""<div class="scroll"><table class="grid">
<thead><tr><th>会社</th><th>男性育休取得率（算出方式つき）</th></tr></thead>
<tbody>{cc}</tbody></table></div>
<p class="caveat">{CHILDCARE_CAVEAT}<b>そのため、この表は高い順に並べていません。</b></p>"""

    # 財務（連結）の横比較。数値のある会社だけを1人当たり売上高の高い順に並べる。
    # 平均年収の表とは集計範囲が違う（あちらは提出会社）ので、節を分けて明示する。
    fin_rows = []
    for m in members:
        f = m.get("fin")
        if not f or not f["latest"]["values"]:
            continue
        lat = f["latest"]
        fin_rows.append((m, lat, lat["per_capita"].get("revenue_per_employee")))
    fin_rows.sort(key=lambda r: (r[2] is None, -(r[2] or 0)))
    fin_table = ""
    if fin_rows:
        body_rows = "".join(
            f'<tr><th scope="row"><a href="../kigyou/{e(m["slug"])}.html">{logo_img(m["icon"])}{e(m["short"])}</a></th>'
            f'<td>{oku(lat["values"].get("revenue"))}</td>'
            f'<td>{oku(lat["values"].get("operating_income"))}</td>'
            f'<td>{oku(rpe) if rpe else NA}</td>'
            f'<td>{oku(lat["per_capita"].get("operating_income_per_employee")) if lat["per_capita"].get("operating_income_per_employee") else NA}</td>'
            f'<td>{pct(lat["values"].get("equity_ratio"))}</td>'
            f'<td>{pct((m.get("overseas") or {}).get("overseas_ratio"))}</td>'
            f'<td class="small">{e(lat.get("accounting_standard", ""))}</td>'
            f'<td class="small">{e(lat["period_end"][:7])}期</td></tr>'
            for m, lat, rpe in fin_rows
        )
        n_ov = sum(1 for m, _, _ in fin_rows if m.get("overseas"))
        ov_note = (
            f'<p class="caveat">海外売上高比率は{n_ov}社ぶんだけ載っています。'
            "有価証券報告書の「地域ごとの情報」は単一セグメントの会社に開示義務が無く、"
            "記載のある会社だけを、同じ有報のXBRLの売上高と突き合わせて一致した場合に限り掲載しています。"
            "<b>海外売上があることと海外駐在の機会があることは別です。</b></p>"
            if n_ov else ""
        )
        missing = len(members) - len(fin_rows)
        missing_note = (
            f'<p class="caveat">{missing}社は財務数値を有価証券報告書から確定できなかったため載せていません。</p>'
            if missing else ""
        )
        fin_table = f"""<section>
<h2>財務（連結）と従業員1人当たりの指標</h2>
<p class="lead"><b>この表だけ集計範囲が違います。</b>上の平均年収・勤続年数は提出会社（単体）ですが、
ここは連結です。1人当たりの値は連結の数値を連結従業員数で割ったもので、人数の効率を見る指標です。
給与の高さを意味しません。</p>
<div class="scroll"><table class="grid rank">
<thead><tr><th>会社</th><th>売上高／経常収益</th><th>営業利益</th><th>1人当たり売上高</th><th>1人当たり営業利益</th><th>自己資本比率</th><th>海外売上高比率</th><th>会計基準</th><th>決算期</th></tr></thead>
<tbody>{body_rows}</tbody></table></div>
{missing_note}{ov_note}
<p class="caveat">銀行・保険業に「売上高」の科目はないため、経常収益を掲載しています。
IFRSの営業利益は日本基準と定義が異なります（非経常項目の扱い）ので、会計基準の列を確認してください。</p>
</section>"""

    sub_html = ""
    if has_holding:
        srows = []
        for c in members:
            for s in (c["latest"].get("subsidiaries") or [])[:6]:
                srows.append(
                    f'<tr><td class="parent">{e(c["short"])}</td><th scope="row">{e(s["name"])}</th>'
                    f'<td>{pct(s["female_manager_ratio"])}</td>'
                    f'<td>{pct(s["female_to_male_wage_ratio_all"])}</td></tr>'
                )
        sub_html = f"""<section>
<h2>事業会社（連結子会社）の指標</h2>
<p class="lead">この業界には持株会社が含まれます。持株会社の平均年収は本体のもので、実際の勤務先である事業会社の水準ではありません。
持株会社は本体の多様性指標を開示しないことがあり、その場合は上の表で「非公表」になります。</p>
<div class="scroll"><table class="grid">
<thead><tr><th>持株会社</th><th>事業会社</th><th>女性管理職比率</th><th>男女の賃金の差異</th></tr></thead>
<tbody>{"".join(srows)}</tbody></table></div>
<p class="caveat">各持株会社につき有報の記載順で最大6社を掲載しています。全社は各企業ページを参照してください。</p>
</section>"""

    src_items = "".join(
        f'<li>{e(c["short"])} — {e(c["latest"]["source"]["period_end"])}期 '
        f'<a href="{EDINET_VIEW.format(doc_id=c["latest"]["source"]["doc_id"])}" rel="nofollow">EDINET</a>'
        f' / <a href="{EDINET_PDF.format(doc_id=c["latest"]["source"]["doc_id"])}" rel="nofollow">PDF</a></li>'
        for c in members
    )

    def _rows(get):
        rs = [(c["short"], f'../kigyou/{c["slug"]}.html', get(c), c["icon"]) for c in members]
        rs.sort(key=lambda r: (r[2] is None, -(r[2] or 0)))
        return rs

    salary_chart = hbars(_rows(lambda c: c["latest"]["reporting_company"]["average_annual_salary_yen"]), man)
    fmr_chart = hbars(
        _rows(lambda c: c["latest"]["diversity"]["female_manager_ratio"]), pct, scale="pct100"
    )
    wage_chart = hbars(
        _rows(lambda c: c["latest"]["diversity"]["female_to_male_wage_ratio_all"]), pct, scale="pct100"
    )

    body = f"""
<nav class="crumb"><a href="../index.html">トップ</a> › {e(group)}</nav>

<h1>{e(group)}{len(members)}社の平均年収・男女の賃金の差異 横比較</h1>
<p class="lead">有価証券報告書だけを出典に、{e(group)}{len(members)}社を1つの表に並べました。
広告主の都合が入らないよう、掲載企業に費用は請求していません。並び順は平均年間給与の高い順です。</p>

<section>
<h2>平均年間給与</h2>
{salary_chart}
<p class="caveat">{SALARY_CAVEAT}</p>
</section>

<section>
<h2>女性管理職比率</h2>
{fmr_chart}
</section>

<section>
<h2>男女の賃金の差異（全労働者）</h2>
{wage_chart}
<p class="caveat">{WAGE_CAVEAT}</p>
</section>

<section>
<h2>詳細データ（提出会社）</h2>
{main_table}
<p class="caveat">{SALARY_CAVEAT}</p>
<p class="caveat">{WAGE_CAVEAT}</p>
</section>

<section>
<h2>平均年間給与の推移と増減率</h2>
<p class="lead">いま高いかではなく、<b>伸びているか</b>。新卒で入って数年後に受け取る額はこちらに近い。決算期は会社ごとに違うため、各行に期を添えています。</p>
{trend_table}
</section>

<section>
<h2>男性育休取得率</h2>
{childcare_table}
</section>

{fin_table}

{sub_html}

<section class="source">
<h2>出典</h2>
<ul>{src_items}</ul>
<p>ライセンス：<a href="{PDL_URL}" rel="nofollow">公共データ利用規約（PDL1.0）</a>／当サイトのデータ取得日 {e(fetched)}</p>
<p class="processing">{e(PROCESSING_NOTICE)}</p>
</section>
"""
    names = "・".join(c["short"] for c in members)
    title = f"{group}{len(members)}社の平均年収・男女の賃金の差異 横比較｜{SITE_NAME}"
    desc = f"{names}の平均年間給与・平均勤続年数・女性管理職比率・男女の賃金の差異・男性育休取得率を、有価証券報告書だけを出典に1つの表で比較。給与の推移と増減率つき。"
    return page(title, desc, body, depth=1, canonical=f"/gyoukai/{GROUP_SLUG.get(group, group)}.html")


# ---------------------------------------------------------------- 企業を選んで比較

def hikaku_page(companies: list[dict], groups: dict[str, list[dict]], fetched: str) -> str:
    """業界をまたいで任意の企業を選び横比較する。全社ぶんの数値をここに埋め込むのではなく、
    /data/companies.json をクライアント側JSが読んで描く（app.js）。理由は2つ：
    ①静的HTMLだけでは「どの組み合わせを選ぶか」が事前に分からず生成しようがない
    ②companies.jsonはすでにLayer2用に生成済みで、二重管理を避けられる。
    チェックボックスの一覧はここで機械的に埋め込み、業界ごとに<details>で折りたたむ。
    """
    group_blocks = []
    for g, members in groups.items():
        opts = "".join(
            f'<label><input type="checkbox" data-slug="{e(c["slug"])}"> {logo_img(c["icon"])}{e(c["short"])}</label>'
            for c in sorted(members, key=lambda c: sort_key(c["latest"]["reporting_company"]["average_annual_salary_yen"], True))
        )
        group_blocks.append(f"""<details>
<summary>{e(g)}（{len(members)}社）</summary>
<div class="compare-checklist">{opts}</div>
</details>""")

    body = f"""
<nav class="crumb"><a href="index.html">トップ</a> › 企業を選んで比較する</nav>

<h1>企業を選んで比較する</h1>
<p class="lead">業界の枠にとらわれず、気になる企業だけを選んで並べられます。
チェックすると平均年間給与・女性管理職比率・男女の賃金の差異をグラフで比較します。
選んだ組み合わせはURLに残るので、そのままブックマークや共有ができます。</p>

<div id="compare-app">
<div class="compare-toolbar">
  <span id="compare-count" class="compare-count">0社選択中</span>
  <button type="button" id="compare-clear">選択をクリア</button>
</div>
<div class="compare-groups">{"".join(group_blocks)}</div>
<div id="compare-result"><p class="lead">上のリストから企業を選ぶと、ここに横比較が表示されます。</p></div>
</div>

<noscript><p class="warn">このページはJavaScriptを使います。個別の企業比較は各<a href="index.html">業界ページ</a>をご覧ください。</p></noscript>

<section class="source">
<h2>出典</h2>
<p>各企業の出典・取得日は<a href="index.html">個別の企業ページ</a>に記載しています。当サイトのデータ取得日 {e(fetched)}</p>
<p class="processing">{e(PROCESSING_NOTICE)}</p>
</section>
"""
    title = f"企業を選んで比較する｜{SITE_NAME}"
    desc = f"業界の枠を超えて、気になる{len(companies)}社の中から自由に企業を選び、平均年間給与・女性管理職比率・男女の賃金の差異を横比較できます。"
    return page(title, desc, body, depth=0, canonical="/hikaku.html")


# ---------------------------------------------------------------- トップ

def index_page(companies: list[dict], groups: dict[str, list[dict]], fetched: str) -> str:
    # 業界タブ＋横に流れる企業帯（マーキー）。以前は35業界ぶんの企業を縦一列にすべて並べ、
    # ページ高が7万pxを超えていた（initReveal のコメント参照）。横帯なら何社載せても
    # 縦の高さは1業界ぶんのままなので、全社リンクを維持しつつ軽くできる。
    tabs, lanes = [], []
    for i, (g, members) in enumerate(groups.items()):
        gslug = GROUP_SLUG.get(g, g)
        lane_id, tab_id = f"lane-{gslug}", f"tab-{gslug}"
        active = " is-active" if i == 0 else ""
        ordered = sorted(
            members, key=lambda c: sort_key(c["latest"]["reporting_company"]["average_annual_salary_yen"], True)
        )
        chips = "".join(
            f'<a class="chip" href="kigyou/{e(c["slug"])}.html">{logo_img(c["icon"], 44)}'
            f'<span class="chip-name">{e(c["short"])}</span></a>'
            for c in ordered
        )
        n = len(members)
        # 帯が短いとすぐ一周して継ぎ目が目立つので、社数が少ない業界ほど多く複製する。
        # JS側は「複製の1コピーぶんの幅」＝scrollWidth/repeat を1周とみなして無限ループさせる。
        repeat = 6 if n <= 4 else 4 if n <= 8 else 3 if n <= 16 else 2
        tabs.append(
            f'<button type="button" id="{tab_id}" class="tab-btn{active}" role="tab" '
            f'aria-selected="{"true" if i == 0 else "false"}" aria-controls="{lane_id}" '
            f'data-target="{lane_id}" data-href="gyoukai/{e(gslug)}.html" data-group="{e(g)}" data-count="{n}">'
            f'{e(g)}<span class="tab-n">{n}</span></button>'
        )
        lanes.append(f"""<div class="lane{active}" id="{lane_id}" role="tabpanel" aria-labelledby="{tab_id}">
<div class="lane-fade lane-fade-l"></div><div class="lane-fade lane-fade-r"></div>
<div class="lane-track" data-repeat="{repeat}">{chips * repeat}</div>
</div>""")

    first_g, first_members = next(iter(groups.items()))
    showcase_cta = (
        f'<p class="showcase-cta"><a class="cta" id="showcase-cta-link" '
        f'href="gyoukai/{e(GROUP_SLUG.get(first_g, first_g))}.html">'
        f'{e(first_g)}{len(first_members)}社を1つの表で比較する →</a></p>'
    )

    # ---- ヒーローカルーセル（注目企業）----
    # 固定6社・機関（三井物産・内閣府・JETRO・日本政策投資銀行・三井不動産・ヒューリック）に加え、
    # 「年収が業界4位以内・ロゴが判明・採用ホームページが判明」の3条件を満たす企業を
    # 平均年収の高い順に追加して、合計20件ほどになるよう選ぶ（2026-08-23 依頼）。
    FEATURED_FIXED_SLUGS = ["mitsui-bussan", "naikakufu", "jetro", "dbj", "mitsui-fudosan", "hulic"]
    FEATURED_TOTAL = 20

    koumu_loaded = koumu.load_koumu()
    koumu_by_slug = {x["slug"]: x for x in (koumu_loaded[0] if koumu_loaded else [])}
    companies_by_slug = {x["slug"]: x for x in companies}

    def _has_recruit(c: dict) -> bool:
        links = c.get("links") or {}
        return bool(links.get("recruit")) or bool((c.get("saiyo") or {}).get("source_urls"))

    def _meets_conditions(c: dict) -> bool:
        if not c.get("icon") or not (c.get("links") or {}).get("official") or not _has_recruit(c):
            return False
        salary = c["latest"]["reporting_company"]["average_annual_salary_yen"]
        if salary is None:
            return False
        rank, _total = peer_rank(c, groups[c["peer_group"]], "average_annual_salary_yen")
        return rank is not None and rank <= 4

    fixed_items = []
    for slug in FEATURED_FIXED_SLUGS:
        if slug in companies_by_slug:
            fixed_items.append(("company", companies_by_slug[slug]))
        elif slug in koumu_by_slug:
            fixed_items.append(("koumu", koumu_by_slug[slug]))

    fixed_slugs = {slug for slug in FEATURED_FIXED_SLUGS}
    candidates = sorted(
        (c for c in companies if c["slug"] not in fixed_slugs and _meets_conditions(c)),
        key=lambda c: -c["latest"]["reporting_company"]["average_annual_salary_yen"],
    )
    extra_n = max(0, FEATURED_TOTAL - len(fixed_items))
    featured_items = fixed_items + [("company", c) for c in candidates[:extra_n]]

    def _car_card(card_kind: str, x: dict) -> str:
        if card_kind == "koumu":
            name, icon = x["name"], x.get("icon")
            industry = koumu.KIND_LABEL.get(x["kind"], x["kind"])
            href = f'koumu/{e(x["slug"])}.html'
        else:
            name, icon = x["short"], x.get("icon")
            industry = x["peer_group"]
            href = f'kigyou/{e(x["slug"])}.html'
        initial = name[0] if name else "?"
        box = (
            f'<img src="{e(icon)}" alt="" width="60" height="60" style="width:60px;height:60px;object-fit:contain">'
            if icon else e(initial)
        )
        return f"""<a class="hero-car-card" href="{href}">
<div class="hero-car-logo">{box}</div>
<div class="hero-car-name">{e(name)}</div>
<div class="hero-car-industry">{e(industry)}</div>
<div class="hero-car-cta">詳細を見てみる<span>→</span></div>
</a>"""

    car_cards = "".join(_car_card(k, x) for k, x in featured_items)

    # ---- 官公庁・政府系セクション（2026-08-23 追加）----
    # ヒーローカルーセルの固定枠には内閣府・JETRO・日本政策投資銀行の3機関しか出ないため、
    # 126機関全件を種別ごとにグループ化して専用セクションで出す。koumu.entity_link() は
    # koumu/index.html の一覧と同じ描画関数（公式サイト・採用ページの直リンク付き）を再利用する。
    koumu_entities = koumu_loaded[0] if koumu_loaded else []
    _koumu_kind_order = ["省庁", "独立行政法人", "政府系金融機関"]
    koumu_groups_html = "".join(
        f"""<div class="koumu-groups-flat">
<h3>{e(koumu.KIND_LABEL.get(kind, kind))}（{len([x for x in koumu_entities if x["kind"] == kind])}）</h3>
<div class="koumu-list">{"".join(koumu.entity_link(sys.modules[__name__], x) for x in koumu_entities if x["kind"] == kind)}</div>
</div>"""
        for kind in _koumu_kind_order
    )
    koumu_section_html = ""
    if koumu_entities:
        n_shocho = len([x for x in koumu_entities if x["kind"] == "省庁"])
        n_dokuho = len([x for x in koumu_entities if x["kind"] == "独立行政法人"])
        n_kinyu = len([x for x in koumu_entities if x["kind"] == "政府系金融機関"])
        koumu_section_html = f"""<section>
<h2>官公庁・政府系</h2>
<p>省庁{n_shocho}機関・新卒採用を行う独立行政法人{n_dokuho}法人・政府系金融機関{n_kinyu}機関の
新卒採用トラックと給与水準を、人事院や各機関の公式発表だけをもとにまとめています。</p>
{koumu_groups_html}
<p class="showcase-cta"><a class="cta" href="koumu/index.html">官公庁・政府系をすべて見る →</a></p>
</section>"""
    carousel_html = f"""<div class="hero-carousel-wrap">
<div class="hero-carousel chip-scroll" id="hero-carousel">{car_cards}{car_cards}</div>
<button type="button" class="hero-car-btn hero-car-prev" aria-label="前へ">‹</button>
<button type="button" class="hero-car-btn hero-car-next" aria-label="次へ">›</button>
</div>"""

    total_reports = sum(len(x["years"]) for x in companies)
    stats_html = "".join(
        f'<div class="hero-stat"><div class="hero-stat-value">{v}</div><div class="hero-stat-label">{lb}</div></div>'
        for v, lb in [
            (f"{len(companies):,}社", "掲載企業数"),
            (f"{len(groups)}業界", "カバーする業種"),
            (f"{total_reports:,}件", "突き合わせた有報"),
        ]
    )

    hero_band = f"""<div class="hero-band">
<div class="hero-blob hero-blob-a"></div><div class="hero-blob hero-blob-b"></div>
<div class="hero-inner">
{carousel_html}
<h1>データで見る就活サイト</h1>
<p class="hero-sub">
数値はプログラムがXBRLから機械的に抽出し、有報のPDFと1件ずつ突き合わせて検証しています（{len(companies)}社・計{total_reports}件の有価証券報告書）。
AIに数字を書かせていません。データが無い項目は推測で埋めず「非公表」と書きます。
</p>
<div class="hero-cta-row">
<a class="hero-cta-primary" href="shindan.html">就活の軸診断をやってみる</a>
<a class="hero-cta-secondary" href="hikaku.html">企業を選んで比較する</a>
</div>
<div class="hero-stats">{stats_html}</div>
</div>
</div>"""

    axes_html = "".join(
        f'<div class="idx-axis"><div class="idx-axis-name">{e(name)}</div>'
        f'<div class="idx-axis-bar"><div class="idx-axis-fill" style="width:{p}%"></div></div></div>'
        for name, p in [("給与水準", 82), ("安定性", 64), ("成長速度", 48), ("働きやすさ", 71)]
    )
    ranklinks_html = "".join(
        f'<a class="idx-ranklink" href="{href}"><div class="idx-ranklink-title">{e(title_)}</div>'
        + (f'<div class="idx-ranklink-note">{e(note)}</div>' if note else "") + "</a>"
        for href, title_, note in [
            ("ranking/index.html", "ランキングと条件別の企業一覧（すべて）", "平均年収・平均勤続年数・女性管理職比率・1人当たり売上高ほか"),
            ("ranking/nenshu.html", "平均年収ランキング", "持株会社は本体の数字になるため除いています"),
            ("ranking/nenshu-nobi.html", "平均年収の5年増減率ランキング", "算定基準が変わった会社は対象外"),
            ("ranking/nenshu-1000man.html", "平均年収1,000万円以上の企業一覧", ""),
            ("ranking/kinzoku-20nen.html", "平均勤続年数20年以上の企業一覧", ""),
        ]
    )

    body = f"""{hero_band}
<div class="idx-main">

<section class="idx-diagnosis">
<div class="idx-diagnosis-copy">
<h2>何を優先するかで、合う会社は変わる</h2>
<p>12問の二者択一に答えると、<b>自分が就活で何を大事にしているか</b>が8つの軸で出ます。
そのうえで同じ重みを使って{len(companies)}社を並べ替えます。回答はブラウザの中だけで処理され、どこにも送信されません。</p>
<p><a class="hero-cta-primary" href="shindan.html">就活の軸診断をやってみる</a></p>
<p class="caveat">性格診断ではありません。有価証券報告書の数値を、あなたが選んだ重みで並べ替えているだけです。</p>
</div>
<div class="idx-diagnosis-axes">{axes_html}</div>
</section>

<section>
<h2>ランキングと条件で探す</h2>
<p>業界をまたいで並べたり、条件で絞り込んだりできます。掲載企業から広告費を受け取っていないので、企業に不利な順位もそのまま出します。</p>
<div class="idx-ranklinks">{ranklinks_html}</div>
</section>

{koumu_section_html}

<section class="showcase" id="showcase">
<h2>業界を選んで、企業を流し見る</h2>
<p class="lead small">気になる業界を選ぶと、その業界の企業が横に流れます。ホバーすると止まるので、気になった企業をクリックしてください。</p>
<div class="showcase-tabs" role="tablist">{"".join(tabs)}</div>
<div class="showcase-lanes">{"".join(lanes)}</div>
{showcase_cta}
</section>

<section>
<h2>数字の読み方</h2>
<p>{WAGE_CAVEAT}</p>
<p>{CHILDCARE_CAVEAT}</p>
<p>{SALARY_CAVEAT}</p>
<p><b>持株会社（◯◯フィナンシャル・グループ、◯◯ホールディングス）の平均年収には注意してください。</b>
それは持株会社本体の数百〜数千人の数字で、実際の勤務先である銀行や保険会社の水準ではありません。各ページに事業会社の指標を併記しています。</p>
<p><b>3年以内離職率は載せていません。</b>大手企業はこれを公的に開示しておらず、厚労省「しょくばらぼ」にも掲載がないためです（全掲載148,228社を調べたところ、この項目を埋めているのは1.8%＝中小企業層だけで、大手はすべて空欄でした）。
推測値を置くくらいなら空けておきます。代わりに<b>平均勤続年数</b>を定着の代理指標として使ってください。</p>
</section>

<section class="source">
<h2>出典とライセンス</h2>
<p>出典：EDINET閲覧（提出）サイト（各企業ページに当該書類のURLを記載）／
<a href="{PDL_URL}" rel="nofollow">公共データ利用規約（PDL1.0）</a>／当サイトのデータ取得日 {e(fetched)}</p>
<p class="processing">{e(PROCESSING_NOTICE)}</p>
</section>

</div>"""
    title = f"{SITE_NAME}｜{TAGLINE}"
    desc = (
        f"総合商社・銀行・保険・医薬品・海運ほか{len(groups)}業界{len(companies)}社の平均年収、男女の賃金の差異、"
        "女性管理職比率、男性育休取得率を有価証券報告書だけを出典に横比較。給与の推移つき。"
        "広告主からの掲載料を受け取らない就活データサイト。"
    )
    return page(title, desc, body, depth=0, canonical="/index.html")


# ---------------------------------------------------------------- CSS

CSS = """:root{
  --fg:#12141f;--mut:#66707f;--line:#e3e6ec;--bg:#f5f6f9;--card:#ffffff;
  --accent:#2f4bd6;--accent-dk:#22399e;--link:#3a52c9;
  --warn:#fdf6ec;--warnline:#c98a2b;--note:#eef1fc;
  --up:#0f8a5f;--down:#c53434;
  --shadow:0 1px 2px rgba(18,20,31,.04),0 8px 24px rgba(18,20,31,.06);
  --shadow-lift:0 2px 6px rgba(18,20,31,.06),0 18px 44px rgba(18,20,31,.10);
  --radius:12px;--radius-sm:8px;
  /* 動きの基準を1か所に集める。ばらばらの秒数を各所に書くと「ぬるぬる」ではなく雑味になる */
  --ease:cubic-bezier(.22,.61,.36,1);
  --ease-out:cubic-bezier(.16,1,.3,1);
  --t-fast:.16s;--t:.28s;--t-slow:.55s
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
a,button,input,select,.linklist li,.grid tbody tr{transition:color var(--t-fast) var(--ease),background-color var(--t-fast) var(--ease),border-color var(--t-fast) var(--ease),box-shadow var(--t) var(--ease),transform var(--t) var(--ease-out)}

/* スクロールで現れる。**html.js-anim が付いているときだけ隠す。**
   JSが落ちた・実行されない環境では .reveal は何もしないので、本文は必ず読める。
   装飾のためにコンテンツが消えることがあってはならない。 */
.reveal{transition:opacity var(--t-slow) var(--ease-out),transform var(--t-slow) var(--ease-out)}
html.js-anim .reveal{opacity:0;transform:translateY(14px)}
html.js-anim .reveal.is-in{opacity:1;transform:none}

/* 読了バー（ページ上端） */
.read-bar{position:fixed;top:0;left:0;height:2px;width:100%;background:linear-gradient(90deg,var(--accent),#6f8bff);transform:scaleX(0);transform-origin:0 50%;z-index:60;pointer-events:none;will-change:transform}

/* 動きを減らす設定の人には動かさない。装飾のために読みづらくしない */
@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  *,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important}
  .reveal{opacity:1;transform:none}
  .read-bar{display:none}
}
body{margin:0;font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,system-ui,sans-serif;color:var(--fg);background:var(--bg);line-height:1.75;font-size:16px;-webkit-font-smoothing:antialiased;letter-spacing:.01em}
main{max-width:960px;margin:0 auto;padding:0 20px 64px}
header.site{background:linear-gradient(120deg,#10122a 0%,#1d2864 100%);position:sticky;top:0;z-index:50;transition:padding var(--t) var(--ease),box-shadow var(--t) var(--ease)}
/* スクロールすると縮んで影が出る。タグラインと検索は畳んで、戻る導線だけ残す */
header.site.is-stuck{box-shadow:0 6px 24px rgba(16,18,42,.28)}
header.site.is-stuck .brand{padding-top:12px}
header.site.is-stuck .brand-word{font-size:20px}
header.site.is-stuck .tagline{max-height:0;opacity:0;padding:0;overflow:hidden}
header.site.is-stuck .site-search{max-height:0;opacity:0;padding-bottom:0;overflow:hidden}
header.site.is-stuck .header-nav{padding-bottom:10px}
header.site .tagline,header.site .site-search,header.site .brand,header.site .brand-word{transition:all var(--t) var(--ease)}
header.site>*{max-width:960px;margin:0 auto;padding:0 20px}
.brand{display:flex;align-items:center;gap:10px;padding-top:24px;text-decoration:none}
.brand svg{flex:none}
.brand-word{font-weight:800;font-size:26px;color:#fff;letter-spacing:-.02em}
.tagline{display:block;color:#9aa4d9;font-size:13px;padding:4px 0 22px;margin-left:38px}
.header-nav{margin-left:38px;padding-bottom:14px}
.header-nav a{color:#c2caf1;font-size:13px;font-weight:700;text-decoration:none}
.header-nav a:hover{color:#fff}
header.site .site-search{margin-left:38px;padding-bottom:20px;max-width:360px}
header.site .search-input{width:100%;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.18);color:#fff;border-radius:7px;padding:8px 12px;font-size:13.5px}
header.site .search-input::placeholder{color:#aab0d6}
header.site .search-input:focus{outline:2px solid rgba(255,255,255,.5);outline-offset:1px}
.site-search{position:relative}
.footer-search-label{font-weight:700;color:var(--fg);margin:0 0 8px;font-size:13px}
footer.site .site-search{max-width:420px;margin:0 0 22px}
footer.site .search-input{width:100%;background:var(--card);border:1px solid var(--line);color:var(--fg);border-radius:7px;padding:9px 12px;font-size:14px}
.search-results{position:absolute;left:0;right:0;top:100%;margin-top:4px;background:var(--card);border:1px solid var(--line);border-radius:8px;box-shadow:var(--shadow);max-height:280px;overflow-y:auto;z-index:20}
.search-results a{display:flex;justify-content:space-between;gap:10px;padding:9px 12px;color:var(--fg);text-decoration:none;font-size:13.5px;border-bottom:1px solid var(--line)}
.search-results a:last-child{border-bottom:none}
.search-results a:hover,.search-results a.active{background:#eef1fc}
.search-results .sr-group{color:var(--mut);font-size:11.5px;white-space:nowrap}
.co-logo{border-radius:3px;vertical-align:-3px;margin-right:5px;flex:none}
h1 .co-logo{vertical-align:-5px;margin-right:8px}
.search-results .sr-empty{padding:10px 12px;color:var(--mut);font-size:13px}
a{color:var(--link);text-decoration-color:rgba(58,82,201,.35);text-underline-offset:2px}
a:hover{text-decoration-color:var(--link)}
h1{font-size:26px;line-height:1.5;margin:28px 0 14px;font-weight:800;letter-spacing:-.01em}
h2{font-size:18px;margin:44px 0 14px;padding-bottom:9px;border-bottom:1px solid var(--line);color:var(--fg);font-weight:800;letter-spacing:-.005em}
h3{font-size:14px;margin:24px 0 8px;color:var(--mut);font-weight:700;text-transform:uppercase;letter-spacing:.04em}
.crumb{font-size:13px;color:var(--mut);margin:22px 0 0}
.crumb a{color:var(--mut)}
.lead{color:#3d4453}
.small{font-size:13px;color:var(--mut)}
.na{color:var(--mut);font-size:12.5px;background:#eef0f4;padding:1px 7px;border-radius:4px}
.warn{background:var(--warn);border:1px solid #f0dfc0;border-left:3px solid var(--warnline);border-radius:var(--radius-sm);padding:14px 16px;margin:18px 0;font-size:14px}
.note{background:var(--note);border:1px solid #d7ddf7;border-left:3px solid var(--accent);border-radius:var(--radius-sm);padding:14px 16px;margin:18px 0;font-size:14px}
.warn q,.note q{display:block;margin-top:8px;color:#3d4453;font-size:13px}
.caveat{font-size:13px;color:var(--mut);margin:10px 0 0;line-height:1.75}
.processing{font-size:13px;color:var(--mut)}
table{border-collapse:collapse;width:100%}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:var(--radius-sm)}
.kv{background:var(--card);border:1px solid var(--line);border-radius:var(--radius-sm);overflow:hidden}
.kv th{text-align:left;font-weight:600;color:var(--mut);width:15em;padding:11px 14px;border-bottom:1px solid var(--line);vertical-align:top}
.kv td{padding:11px 14px;border-bottom:1px solid var(--line)}
.kv tr:last-child th,.kv tr:last-child td{border-bottom:none}
.grid{font-size:13.5px;min-width:640px;border:1px solid var(--line);border-radius:var(--radius-sm);overflow:hidden}
.grid th,.grid td{border-bottom:1px solid var(--line);border-right:1px solid var(--line);padding:9px 11px;text-align:right;white-space:nowrap}
.grid th:last-child,.grid td:last-child{border-right:none}
.grid thead th{background:#f7f8fb;text-align:center;font-size:11.5px;color:var(--mut);font-weight:700;text-transform:uppercase;letter-spacing:.03em}
.grid tbody th{text-align:left;font-weight:700}
.grid tbody tr:hover{background:#f7f8fc}
.grid .parent{text-align:left;color:var(--mut);font-size:12px}
.rank tbody tr:first-child{background:#f0f3fd}
.grid tbody tr.band th{background:#eef1f7;text-align:left;font-size:12px;color:var(--accent-dk);letter-spacing:.02em;padding:7px 11px}
.grid tbody tr.band:hover{background:#eef1f7}
.grid tbody tr.self{background:#f0f3fd}
.grid tbody tr.self th{color:var(--accent-dk)}
.rank tbody tr.band:first-child{background:#eef1f7}
.grid td.rank-no{text-align:center;color:var(--mut);font-variant-numeric:tabular-nums;width:3.5em}
.rank tbody tr:nth-child(-n+3) td.rank-no{color:var(--accent-dk);font-weight:800}
.linklist{list-style:none;padding:0;margin:0;display:grid;gap:8px}
.linklist li{background:var(--card);border:1px solid var(--line);border-radius:var(--radius-sm);padding:12px 14px}
.linklist li a{font-weight:700}
.linklist li .small{display:block;color:var(--mut);margin-top:3px}
.linklist li{position:relative;overflow:hidden}
.linklist li:hover{transform:translateY(-2px);box-shadow:var(--shadow-lift);border-color:#c9d2ee}
.linklist li::after{content:"→";position:absolute;right:14px;top:50%;transform:translate(6px,-50%);opacity:0;color:var(--accent);font-weight:800;transition:opacity var(--t) var(--ease),transform var(--t) var(--ease-out)}
.linklist li:hover::after{opacity:1;transform:translate(0,-50%)}

/* ---- 会社ページ・一覧の手ざわり ---- */
.grid tbody tr:hover{background:#f4f7ff}
.grid tbody th a{background-image:linear-gradient(var(--accent),var(--accent));background-size:0 1.5px;background-position:0 100%;background-repeat:no-repeat;transition:background-size var(--t) var(--ease)}
.grid tbody th a:hover{background-size:100% 1.5px}
.cta-btn,.sd-start{appearance:none;border:0;cursor:pointer;font:inherit;font-weight:800;color:#fff;background:linear-gradient(120deg,var(--accent),#5570e6);padding:14px 30px;border-radius:999px;box-shadow:0 6px 18px rgba(47,75,214,.28)}
.cta-btn:hover,.sd-start:hover{transform:translateY(-2px);box-shadow:0 10px 26px rgba(47,75,214,.36)}
.cta-btn:active,.sd-start:active{transform:translateY(0)}

/* ---- 就活の軸診断 ---- */
.shindan{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:26px 22px;box-shadow:var(--shadow);margin:22px 0}
.sd-intro{text-align:center}
.sd-meta{color:var(--mut);font-size:13px;margin:0 0 18px}
.sd-progress{height:4px;background:var(--line);border-radius:999px;overflow:hidden}
.sd-bar{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--accent),#6f8bff);transition:width var(--t-slow) var(--ease-out)}
.sd-count{color:var(--mut);font-size:13px;margin:10px 0 2px;text-align:center}
.sd-count b{color:var(--accent-dk);font-size:19px}
.sd-lead{text-align:center;font-weight:700;margin:0 0 16px}
.sd-choices{display:grid;grid-template-columns:1fr auto 1fr;gap:12px;align-items:stretch}
.sd-vs{align-self:center;color:var(--mut);font-size:13px}
.sd-choice{appearance:none;cursor:pointer;font:inherit;text-align:left;background:var(--card);border:1.5px solid var(--line);border-radius:var(--radius);padding:22px 18px;font-weight:700;line-height:1.6;min-height:110px;display:flex;align-items:center}
.sd-choice:hover{border-color:var(--accent);background:#f7f9ff;transform:translateY(-3px);box-shadow:var(--shadow-lift)}
.sd-choice:active{transform:scale(.985)}
.sd-back{appearance:none;border:0;background:none;cursor:pointer;font:inherit;color:var(--mut);font-size:13px;margin-top:16px;padding:6px 0}
.sd-back:hover{color:var(--accent)}
@keyframes sdInL{from{opacity:0;transform:translateX(22px)}to{opacity:1;transform:none}}
@keyframes sdInR{from{opacity:0;transform:translateX(-22px)}to{opacity:1;transform:none}}
.sd-choices.in-l{animation:sdInL .34s var(--ease-out) both}
.sd-choices.in-r{animation:sdInR .34s var(--ease-out) both}

.sd-axis{display:grid;grid-template-columns:9.5em 1fr 3.2em;gap:10px;align-items:center;margin:8px 0;animation:sdRow .45s var(--ease-out) both;animation-delay:calc(var(--i) * .05s)}
@keyframes sdRow{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.sd-axis-name{font-weight:700;font-size:14px}
.sd-axis-bar{background:var(--line);border-radius:999px;height:10px;overflow:hidden}
.sd-axis-bar i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),#6f8bff);border-radius:999px;animation:sdGrow .7s var(--ease-out) both;animation-delay:calc(var(--i) * .05s + .1s)}
@keyframes sdGrow{from{width:0!important}}
.sd-axis-val{color:var(--mut);font-size:12px;text-align:right;font-variant-numeric:tabular-nums}
.sd-filters{display:flex;gap:18px;flex-wrap:wrap;align-items:center;margin:0 0 12px;font-size:13.5px;color:var(--mut)}
.sd-filters select{font:inherit;padding:5px 9px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
.sd-table tbody tr{animation:sdRow .4s var(--ease-out) both;animation-delay:calc(var(--i) * .022s)}
.sd-score{display:inline-block;vertical-align:middle;width:52px;height:7px;background:var(--line);border-radius:999px;overflow:hidden;margin-right:7px}
.sd-score i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),#6f8bff)}
.sd-why{display:inline-block;font-size:11px;background:var(--note);color:var(--accent-dk);border-radius:5px;padding:2px 7px;margin:1px 3px 1px 0;font-weight:700;white-space:nowrap}
.sd-used{display:block;color:var(--mut);font-size:11px}
.sd-actions{margin-top:18px}
.sd-retry{appearance:none;cursor:pointer;font:inherit;font-weight:700;color:var(--accent-dk);background:var(--note);border:1px solid #d7ddf7;border-radius:999px;padding:9px 22px}
.sd-retry:hover{background:#e4e9fb}
@media(max-width:640px){
  .sd-choices{grid-template-columns:1fr;gap:10px}
  .sd-vs{justify-self:center}
  .sd-choice{min-height:76px;padding:16px 15px}
  .sd-axis{grid-template-columns:7.5em 1fr 3em}
}

/* ---- 新卒の採用枠 ---- */
.saiyo-tracks{display:grid;gap:12px;margin:14px 0}
.saiyo-track{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px}
.saiyo-track h3{margin:0 0 6px;font-size:15.5px}
.saiyo-track p{margin:6px 0}
.saiyo-field b{color:var(--accent-dk)}
.saiyo-notes{margin:10px 0;padding-left:1.3em;font-size:14px;line-height:1.8}

/* ---- 学部・興味から絞る診断 ---- */
.tk-optgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin:10px 0 20px}
.tk-opt{appearance:none;cursor:pointer;font:inherit;text-align:left;background:var(--card);border:1.5px solid var(--line);border-radius:var(--radius);padding:14px 16px}
.tk-opt b{display:block;font-size:14px}
.tk-opt small{display:block;color:var(--mut);font-size:12px;margin-top:3px}
.tk-opt:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:var(--shadow)}
.tk-opt.is-on{border-color:var(--accent);background:var(--note);box-shadow:0 0 0 2px var(--accent) inset}
.tk-go{margin-top:6px}
.tk-go:disabled{opacity:.4;cursor:not-allowed;transform:none;box-shadow:none}
.tk-filters{display:flex;gap:18px;flex-wrap:wrap;align-items:center;margin:14px 0 12px;font-size:13.5px;color:var(--mut)}
.tk-fact{font-size:11px;background:var(--note);color:var(--accent-dk);border-radius:5px;padding:2px 7px;font-weight:700}
.tk-guess{color:var(--mut);font-size:12px}

/* ---- 公式サイト・採用ページへの導線 ---- */
.extlinks{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 0}
.extlink{display:inline-flex;align-items:center;gap:7px;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:8px 16px;font-size:13.5px;font-weight:700;text-decoration:none;color:var(--fg)}
.extlink:hover{border-color:var(--accent);color:var(--accent-dk);transform:translateY(-2px);box-shadow:var(--shadow)}
.extlink.is-recruit{background:var(--note);border-color:#d7ddf7;color:var(--accent-dk)}
.extlink small{font-weight:400;color:var(--mut)}
.hero-cta{background:linear-gradient(140deg,#f4f6ff 0%,#eef1fc 60%,#f7f4ff 100%);border:1px solid #dfe4f6;border-radius:var(--radius);padding:28px 24px;text-align:center;margin:26px 0}
.hero-cta h2{border:0;margin:0 0 8px;font-size:20px}
.hero-cta p{margin:0 0 14px}
.hero-cta .caveat{margin:12px 0 0}
.header-nav a{margin-right:16px}
.hd{font-size:10px;background:#eef1fc;color:var(--accent-dk);padding:2px 7px;border-radius:4px;vertical-align:middle;font-weight:700}
.stale{font-size:10px;background:var(--warn);color:#8a5c14;padding:2px 7px;border-radius:4px;vertical-align:middle;font-weight:700}
.method{font-size:10px;padding:2px 7px;border-radius:4px;white-space:nowrap;font-weight:700}
.method1{background:#e6f5ee;color:#0f6b46}
.method2{background:var(--warn);color:#8a5c14}
.up{color:var(--up);font-weight:800}
.down{color:var(--down);font-weight:800}
.rate{font-size:15px;margin:0 0 12px;font-weight:600}

/* ---- SVGチャート（データはPythonが計算し、JSは使わない） ---- */
.chart-wrap{margin:14px 0 6px}
.linechart{width:100%;height:auto;display:block}
.linechart .grid-line{stroke:var(--line);stroke-width:1}
.linechart .chart-area{fill:url(#lc-fill)}
.linechart .chart-line{fill:none;stroke:var(--accent);stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round}
.linechart .chart-dot{fill:var(--card);stroke:var(--accent);stroke-width:2.5}
.linechart .chart-x{fill:var(--mut);font-size:11px;text-anchor:middle}
.linechart .chart-val{fill:var(--fg);font-size:11.5px;font-weight:700;text-anchor:middle}
.donuts{display:flex;flex-wrap:wrap;gap:28px;margin:16px 0 8px}
.donut-item{text-align:center;width:104px}
.donut-item .donut-label{font-size:11.5px;color:var(--mut);margin-top:6px;line-height:1.4}
.donut-item .donut-method{display:block;font-size:10px;margin-top:2px}
.donut-track{stroke:#e9ebf1;fill:none}
.donut-value{stroke:var(--accent);fill:none;stroke-linecap:round}
.donut-value.down{stroke:var(--down)}
.donut-center{font-size:17px;font-weight:800;fill:var(--fg);text-anchor:middle;dominant-baseline:central}
.donut-na{font-size:11px;fill:var(--mut);text-anchor:middle;dominant-baseline:central}

.hbars{margin:14px 0 6px}
.hbar-row{display:grid;grid-template-columns:minmax(7em,13em) 1fr 5.5em;align-items:center;gap:12px;padding:7px 0}
.hbar-row+.hbar-row{border-top:1px solid var(--line)}
.hbar-name{font-size:13.5px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hbar-name a{color:var(--fg);text-decoration:none}
.hbar-name a:hover{color:var(--link)}
.hbar-track{background:#eef0f6;border-radius:5px;height:14px;overflow:hidden}
.hbar-fill{display:block;background:linear-gradient(90deg,var(--accent-dk),var(--accent));height:100%;border-radius:5px}
.hbar-val{font-size:12.5px;color:var(--fg);font-weight:700;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.hbar-val.na{color:var(--mut);font-weight:400}

.cols{display:flex;gap:32px;flex-wrap:wrap}
.col{flex:1 1 320px;min-width:0}
.cta{display:inline-block;margin-top:12px;font-weight:700;font-size:14px;background:var(--fg);color:#fff!important;padding:10px 20px;border-radius:6px;text-decoration:none}
.cta:hover{background:var(--accent-dk)}

/* ---- 業界ショーケース（トップページ：横に流れる企業帯） ---- */
.showcase-tabs{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 16px}
.tab-btn{appearance:none;cursor:pointer;font:inherit;font-size:13px;font-weight:700;color:var(--fg);
  background:var(--card);border:1px solid var(--line);border-radius:999px;padding:7px 14px;
  display:inline-flex;align-items:center;gap:6px;white-space:nowrap;
  transition:background-color var(--t-fast) var(--ease),border-color var(--t-fast) var(--ease),color var(--t-fast) var(--ease),transform var(--t-fast) var(--ease-out)}
.tab-btn:hover{border-color:#c7cff0;transform:translateY(-1px)}
.tab-btn .tab-n{font-size:11px;font-weight:400;color:var(--mut)}
.tab-btn.is-active{background:var(--accent);border-color:var(--accent);color:#fff}
.tab-btn.is-active .tab-n{color:rgba(255,255,255,.78)}

.lane{display:none;position:relative;background:transparent}
.lane.is-active{display:block}
.lane-fade{position:absolute;top:0;bottom:0;width:44px;z-index:2;pointer-events:none}
.lane-fade-l{left:0;background:linear-gradient(90deg,var(--bg),transparent)}
.lane-fade-r{right:0;background:linear-gradient(270deg,var(--bg),transparent)}
/* ユーザーが自由にドラッグ/スワイプ/ホイールでスクロールできる。JS側は同じscrollLeftを
   一定間隔でチップ幅ぶんだけ smooth に進めるだけなので、手動操作と競合しない。 */
.lane-track{display:flex;gap:12px;overflow-x:auto;padding:4px 18px 12px;
  scroll-snap-type:x mandatory;-webkit-overflow-scrolling:touch;scrollbar-width:thin}
.chip{flex:0 0 auto;scroll-snap-align:start;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;gap:10px;
  width:112px;background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);
  padding:16px 10px;color:var(--fg);text-decoration:none;text-align:center;
  transition:border-color var(--t-fast) var(--ease),box-shadow var(--t-fast) var(--ease),transform var(--t-fast) var(--ease-out)}
.chip:hover{border-color:#c7cff0;box-shadow:var(--shadow-lift);transform:translateY(-2px)}
.chip .co-logo{width:44px;height:44px;margin:0;border-radius:9px;background:#fff;border:1px solid var(--line)}
.chip-name{font-size:13px;font-weight:700;line-height:1.35;color:var(--fg);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.showcase-cta{margin:14px 0 0}
.peers .small a{margin-right:10px;white-space:nowrap}
.source{margin-top:48px;font-size:13px;color:var(--mut);background:#f7f8fb;border:1px solid var(--line);border-radius:var(--radius-sm);padding:16px 18px}
.source ul{padding-left:1.2em}
.source h2{font-size:15px;border-bottom-width:1px}
.notes ul{font-size:13px;color:#3d4453}
footer.site{border-top:1px solid var(--line);padding:24px 20px;font-size:12px;color:var(--mut);background:var(--card)}
footer.site>p{max-width:960px;margin:6px auto}
.disclaimer{font-weight:700;color:var(--fg)}

.compare-toolbar{display:flex;align-items:center;gap:14px;margin:18px 0;padding:12px 16px;background:var(--note);border:1px solid #d7ddf7;border-radius:var(--radius-sm)}
.compare-toolbar button{font:inherit;font-weight:700;font-size:13.5px;padding:8px 16px;border-radius:6px;border:1px solid var(--line);background:var(--card);cursor:pointer}
.compare-toolbar button:hover{border-color:#c7cff0}
.compare-count{font-size:13px;color:var(--mut)}
.compare-groups{margin:8px 0 20px}
.compare-groups details{background:var(--card);border:1px solid var(--line);border-radius:var(--radius-sm);margin-bottom:8px;overflow:hidden}
.compare-groups summary{padding:12px 16px;cursor:pointer;font-weight:700;font-size:14px;list-style:none}
.compare-groups summary::-webkit-details-marker{display:none}
.compare-groups summary:after{content:"+";float:right;color:var(--mut);font-weight:400}
.compare-groups details[open] summary:after{content:"−"}
.compare-checklist{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:2px;padding:2px 16px 14px;border-top:1px solid var(--line)}
.compare-checklist label{display:flex;align-items:center;gap:7px;font-size:13.5px;padding:5px 0;cursor:pointer}
.koumu-groups{margin:8px 0 20px}
.koumu-groups details{background:var(--card);border:1px solid var(--line);border-radius:var(--radius-sm);margin-bottom:8px;overflow:hidden}
.koumu-groups summary{padding:12px 16px;cursor:pointer;font-weight:700;font-size:14px;list-style:none}
.koumu-groups summary::-webkit-details-marker{display:none}
.koumu-groups summary:after{content:"+";float:right;color:var(--mut);font-weight:400}
.koumu-groups details[open] summary:after{content:"−"}
.koumu-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:2px;padding:2px 16px 14px;border-top:1px solid var(--line)}
.koumu-list .koumu-row{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:13.5px;padding:7px 0;border-bottom:1px dashed var(--line)}
.koumu-row-link{display:flex;align-items:center;gap:8px;min-width:0;color:var(--fg);text-decoration:none}
.koumu-row-link:hover{color:var(--link)}
.koumu-list .koumu-flag{font-size:11px;color:var(--mut);white-space:nowrap}
.koumu-inline-links{display:flex;gap:4px;flex:0 0 auto}
.koumu-inline-link{font-size:11px;color:var(--mut);border:1px solid var(--line);border-radius:999px;padding:2px 8px;text-decoration:none;white-space:nowrap}
.koumu-inline-link:hover{color:var(--link);border-color:var(--link)}
.koumu-groups-flat{margin:18px 0}
.koumu-groups-flat h3{font-size:15px;margin:0 0 4px}
.koumu-flat{border-top:none;padding:2px 0 14px;margin:14px 0}
#compare-result{margin-top:8px}
@media(max-width:600px){h1{font-size:21px}main{padding:0 14px 48px}.kv th{width:9em;font-size:13px}.donuts{gap:16px;justify-content:space-between}.donut-item{width:88px}.brand-word{font-size:21px}.tagline{margin-left:32px}.hbar-row{grid-template-columns:6.5em 1fr 4.5em;gap:8px}.hbar-name{font-size:12.5px}.compare-checklist{grid-template-columns:1fr 1fr}}

/* =========================================================================
 * 新卒の採用枠 v2（新デザイン対応）
 * 新デザインのカード言語（#fff / #e3e6ec / radius 16px / 同一シャドウ）に合わせた。
 * ========================================================================= */

/* sticky ナビがアンカー先の見出しに被らないようにする（全セクション共通の直し）。
   企業ページは header(縮小時) + sub-nav の2段が重なって固定されるため、両方ぶんの高さを確保する */
main section[id]{scroll-margin-top:112px}

.saiyo-section{margin-bottom:44px}

.saiyo-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:0 0 4px}
.saiyo-head h2{font-size:19px;font-weight:800;letter-spacing:-.01em;margin:0}
.saiyo-badge{font-size:10.5px;font-weight:800;color:#8a6d1f;background:#fdf4e0;border:1px solid #f0e2bd;border-radius:999px;padding:2px 10px}

.saiyo-lead{margin:0 0 18px;font-size:13.5px;color:#66707f}

.saiyo-tracks{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;align-items:start;margin:0}
.saiyo-track{background:#fff;border:1px solid #e3e6ec;border-radius:16px;padding:18px 20px;box-shadow:0 1px 2px rgba(18,20,31,.04),0 10px 30px rgba(18,20,31,.05)}
.saiyo-track:only-child{grid-column:1 / -1}
.saiyo-track h3{margin:0 0 8px;font-size:14.5px;font-weight:800;line-height:1.5}
.saiyo-field{margin:0 0 6px;font-size:13.5px;color:#3d4453;line-height:1.8}
.saiyo-field b{color:#22399e;font-weight:800}

.saiyo-more{margin-top:10px;border-top:1px solid #eef0f4}
.saiyo-more>summary{list-style:none;cursor:pointer;padding:10px 0 0;font-size:12.5px;font-weight:700;color:#22399e}
.saiyo-more>summary::-webkit-details-marker{display:none}
.saiyo-more>summary::before{content:"＋ "}
.saiyo-more[open]>summary::before{content:"− "}
.saiyo-more .saiyo-field{margin:8px 0 0}

.saiyo-notes{margin:14px 0 0;padding-left:20px;font-size:13px;color:#3d4453;line-height:1.9}

.saiyo-section .caveat{margin:12px 0 0;font-size:12.5px;color:#8a93a3;line-height:1.8}
.saiyo-section .caveat a{color:#22399e}

/* =========================================================================
 * 新デザイン（Top Hero / Company Page）2026-08 移植ぶん
 * ========================================================================= */
html,body{overflow-x:hidden}

/* ---- 共通：カード・details ---- */
.cp-card{background:#fff;border:1px solid #e3e6ec;border-radius:16px;box-shadow:0 1px 2px rgba(18,20,31,.04),0 10px 30px rgba(18,20,31,.05)}
.cp-details{margin-top:14px;background:#fff;border:1px solid #e3e6ec;border-radius:14px;padding:0 18px}
.cp-details>summary{list-style:none;cursor:pointer;padding:14px 0;font-size:13px;font-weight:700;color:#22399e}
.cp-details>summary::-webkit-details-marker{display:none}
.cp-details-hint{font-size:11px;color:#8a93a3;font-weight:400;margin-left:6px}
.cp-details p{margin:0 0 16px;font-size:13.5px;color:#3d4453;line-height:1.85}
.cp-lead{margin:0 0 18px;font-size:13.5px;color:#66707f}

/* ---- 企業ページ：アイデンティティ帯 ---- */
.cp-identity{background:#fff;border-bottom:1px solid #e3e6ec;margin:0 -20px 20px;padding:26px 20px 22px}
.cp-crumb{font-size:12.5px;color:#66707f;margin-bottom:18px}
.cp-crumb a{color:#22399e;text-decoration:none}
.cp-identity-row{display:flex;gap:22px;align-items:flex-start;flex-wrap:wrap}
.cp-logo{border-radius:20px;background:linear-gradient(140deg,#eef1fc,#dfe4f6);border:1px solid #dfe4f6;display:flex;align-items:center;justify-content:center;font-weight:800;color:#22399e;overflow:hidden;flex:0 0 auto}
.cp-identity-body{flex:1 1 280px;min-width:240px}
.cp-identity-body h1{font-size:24px;line-height:1.35;font-weight:800;letter-spacing:-.02em;margin:0 0 8px}
.cp-identity-badges{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
.cp-group-badge{font-size:12.5px;font-weight:700;color:#22399e;background:#eef1fc;border:1px solid #dfe4f6;border-radius:999px;padding:4px 12px;text-decoration:none}
.cp-identity-meta{font-size:12.5px;color:#66707f}
.cp-identity-lead{margin:0;font-size:14px;color:#3d4453}
.cp-identity-actions{display:flex;flex-direction:column;gap:8px;flex:0 0 auto;min-width:200px}
.cp-official,.cp-recruit{display:inline-flex;align-items:center;gap:8px;font-size:13px;font-weight:700;border-radius:999px;padding:9px 18px;text-decoration:none}
.cp-official{color:#12141f;background:#fff;border:1px solid #e3e6ec}
.cp-official:hover{border-color:#c9d2ee}
.cp-recruit{color:#1b7f5f;background:#eaf7f1;border:1px solid #c3e6d5}
.cp-recruit:hover{border-color:#94d3b6}
.cp-action-host{font-size:11px;font-weight:400;color:#66707f}
.cp-recruit .cp-action-host{color:#4b8a72}
.cp-recruit-note{font-size:11.5px;color:#8a93a3;line-height:1.6;max-width:230px;margin:0}
.cp-compare-cta{display:inline-flex;align-items:center;justify-content:center;gap:6px;font-size:13px;font-weight:800;color:#fff;background:linear-gradient(120deg,#2f4bd6,#5570e6);border-radius:999px;padding:10px 18px;text-decoration:none;box-shadow:0 6px 18px rgba(47,75,214,.26)}

/* ---- 企業ページ：sticky sub-nav ----
   header.site（縮小時）と2段で重なって固定されるため、headerぶんの高さを見込んでtopをずらす */
.cp-subnav{position:sticky;top:64px;z-index:40;background:rgba(255,255,255,.94);backdrop-filter:blur(10px);border-bottom:1px solid #e3e6ec;margin:0 -20px 24px}
.cp-subnav-inner{display:flex;gap:4px;overflow-x:auto;padding:0 20px}
.cp-subnav a{flex:0 0 auto;white-space:nowrap;font-size:13px;font-weight:700;color:#3d4453;text-decoration:none;padding:14px 14px;border-bottom:2px solid transparent}
.cp-subnav a:hover{color:#22399e;border-bottom-color:#c9d2ee}

.cp-section{margin-bottom:44px}
.cp-section h2{font-size:19px;font-weight:800;letter-spacing:-.01em;margin:0 0 4px;padding-bottom:0;border-bottom:none}

/* ---- 基本データカード ---- */
.cp-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}
.cp-metric-card{padding:20px 22px}
.cp-metric-label{font-size:12.5px;font-weight:700;color:#66707f;margin-bottom:6px}
.cp-metric-value{font-size:27px;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.2}
.cp-metric-value-sm{font-size:19px}
.cp-metric-sub{font-size:12px;color:#8a93a3;margin-top:2px;font-variant-numeric:tabular-nums}
.cp-metric-bar{height:7px;border-radius:999px;background:#eef0f4;overflow:hidden;margin:14px 0 8px}
.cp-metric-bar-fill{height:100%;border-radius:999px;background:linear-gradient(90deg,#2f4bd6,#6f8bff)}
.cp-metric-badges{display:flex;gap:8px;flex-wrap:wrap;align-items:center;min-height:22px}
.cp-metric-rank{font-size:12px;font-weight:800;color:#22399e;background:#eef1fc;border-radius:999px;padding:3px 10px}
.cp-metric-pct{font-size:12px;color:#66707f}
.cp-emp-divider{border-top:1px solid #eef0f4;margin:14px 0 10px}

/* ---- 給与の推移 ---- */
.cp-trend{display:grid;grid-template-columns:220px 1fr;gap:14px;align-items:stretch;margin-bottom:12px}
.cp-trend-info{border:1px solid;border-radius:18px;padding:24px;display:flex;flex-direction:column;justify-content:center}
.cp-trend-range{font-size:12px;font-weight:700;color:#66707f;letter-spacing:.02em}
.cp-trend-delta{font-size:38px;line-height:1.1;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums;margin:6px 0 2px}
.cp-trend-arrow{font-size:22px;margin-left:6px}
.cp-trend-abs{font-size:15px;font-weight:800;font-variant-numeric:tabular-nums}
.cp-trend-note{font-size:12px;color:#66707f;margin-top:10px;line-height:1.7}
.cp-trend-chart{padding:14px 16px 8px}

/* ---- 多様性の指標 ---- */
.cp-diversity{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.cp-diversity-card{padding:22px;display:flex;gap:20px;align-items:center}
.cp-ring{position:relative;width:88px;height:88px;flex:0 0 auto;border-radius:50%;display:flex;align-items:center;justify-content:center}
.cp-ring-inner{width:68px;height:68px;border-radius:50%;background:#fff;display:flex;align-items:center;justify-content:center;font-size:17px;font-weight:800;font-variant-numeric:tabular-nums;color:#12141f;text-align:center}
.cp-ring-inner-na{font-size:12px}
.cp-diversity-body{min-width:0}
.cp-diversity-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px}
.cp-diversity-label{font-size:13.5px;font-weight:800}
.cp-diversity-tag{font-size:10.5px;font-weight:800;color:#8a6d1f;background:#fdf4e0;border:1px solid #f0e2bd;border-radius:999px;padding:2px 8px}
.cp-diversity-note{font-size:12.5px;color:#66707f;line-height:1.7}

/* ---- 規模の近い同業他社（表） ---- */
.cp-ptable{width:100%;border-collapse:collapse;min-width:640px;background:#fff;border:1px solid #e3e6ec;border-radius:16px;overflow:hidden}
.cp-ptable-th{text-align:left;font-size:12px;color:#66707f;font-weight:700;padding:0;border-bottom:1px solid #eef0f4;background:#fff}
.cp-ptable-th-name{padding:14px 18px}
.cp-sort-btn{appearance:none;cursor:pointer;font:inherit;font-size:12px;font-weight:700;color:#66707f;background:transparent;border:0;border-radius:8px;padding:14px 10px;white-space:nowrap}
.cp-sort-btn:hover,.cp-sort-btn.is-active{color:#22399e;background:#eef1fc}
.cp-sort-arrow{margin-left:3px}
.cp-ptable-name{text-align:left;padding:13px 18px;border-bottom:1px solid #f2f4f7;font-weight:700;font-size:13.5px}
.cp-ptable td{padding:13px 18px;border-bottom:1px solid #f2f4f7;vertical-align:middle}
.cp-row-self{background:#f4f6ff}
.cp-row-self .cp-ptable-name{background:#f4f6ff}
.cp-row-logo{display:inline-flex;width:22px;height:22px;border-radius:6px;background:#eef1fc;align-items:center;justify-content:center;overflow:hidden;font-size:11px;font-weight:800;color:#22399e;margin-right:8px;vertical-align:-6px}
.cp-self-badge{font-size:10.5px;font-weight:800;color:#fff;background:#2f4bd6;border-radius:999px;padding:2px 8px;margin-left:6px}
.cp-cell-val{font-size:13.5px;font-weight:700;font-variant-numeric:tabular-nums;color:#12141f}
.cp-cell-na{color:#8a93a3;font-weight:400}
.cp-bar-track{height:5px;border-radius:999px;background:#eef0f4;margin-top:6px;min-width:76px}
.cp-bar-fill{height:100%;border-radius:999px}
.cp-bar-self{background:linear-gradient(90deg,#2f4bd6,#6f8bff)}
.cp-bar-other{background:#cdd5ee}

/* ---- 出典 ---- */
.cp-source{padding:26px 28px}
.cp-source h2{margin:0 0 12px}
.cp-source ul{margin:0 0 14px;padding-left:20px;font-size:13.5px;color:#3d4453;line-height:1.9}
.cp-source .processing{margin:0;font-size:12.5px;color:#8a93a3}

@media(max-width:640px){
  .cp-identity{margin:0 -14px 16px;padding:20px 14px 18px}
  .cp-subnav{margin:0 -14px 18px}
  .cp-subnav-inner{padding:0 14px}
  .cp-trend{grid-template-columns:1fr}
  .cp-identity-actions{width:100%}
  .cp-official,.cp-recruit,.cp-compare-cta{justify-content:center}
}

/* ---- トップページ：ヒーロー帯 ----
   960px固定のmainの中にいてもビューポート全幅に見せるため、幅100vw+transformで中央基準に敷き直す
   （margin-left:calc(50% - 50vw)は親のmax-width幅を基準に計算されてしまい、960px制約下では
   正しく全幅にならないため使わない） */
.hero-band{width:100vw;margin-left:50%;transform:translateX(-50%);position:relative;overflow:hidden;background:linear-gradient(120deg,#10122a 0%,#1d2864 100%)}
.hero-blob{position:absolute;border-radius:50%;pointer-events:none}
.hero-blob-a{top:-120px;right:-80px;width:520px;height:520px;background:radial-gradient(circle,rgba(90,112,230,.35),transparent 70%)}
.hero-blob-b{bottom:-160px;left:-60px;width:420px;height:420px;background:radial-gradient(circle,rgba(90,112,230,.18),transparent 70%)}
.hero-inner{max-width:1120px;margin:0 auto;padding:40px 24px 64px;position:relative;z-index:1}
.hero-inner h1{color:#fff;font-size:27px;line-height:1.5;font-weight:800;letter-spacing:-.01em;margin:0 0 14px;max-width:720px}
.hero-lead{max-width:720px;color:#c2caf1;font-size:14.5px;line-height:1.85;margin:0 0 10px}
.hero-lead b{color:#fff}
.hero-sub{max-width:720px;font-size:13px;color:#9aa4d9;margin:0 0 30px;line-height:1.8}

.hero-carousel-wrap{max-width:1000px;position:relative;margin:0 0 28px;padding:0 44px}
.hero-carousel{display:flex;gap:14px;overflow-x:auto;padding:4px 2px 16px;scrollbar-width:none}
.hero-carousel::-webkit-scrollbar{display:none}
.hero-car-card{flex:0 0 auto;width:190px;display:flex;flex-direction:column;align-items:center;gap:12px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.18);border-radius:16px;padding:22px 18px 18px;text-decoration:none;text-align:center}
.hero-car-card:hover{background:rgba(255,255,255,.14);border-color:rgba(255,255,255,.36)}
.hero-car-logo{width:80px;height:80px;border-radius:20px;background:#fff;display:flex;align-items:center;justify-content:center;font-size:30px;font-weight:800;color:#22399e;overflow:hidden;box-shadow:0 8px 22px rgba(6,8,26,.28)}
.hero-car-name{font-size:14.5px;font-weight:800;color:#fff;letter-spacing:-.01em}
.hero-car-industry{font-size:11.5px;color:#9aa4d9;margin-top:-8px}
.hero-car-cta{margin-top:auto;font-size:12px;font-weight:700;color:#a9b6ff}
.hero-car-btn{position:absolute;top:50%;transform:translateY(-50%);width:36px;height:36px;border-radius:50%;border:1px solid rgba(255,255,255,.26);background:rgba(13,15,36,.82);color:#fff;font-size:16px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.hero-car-btn:hover{background:#2f4bd6}
.hero-car-prev{left:0}
.hero-car-next{right:0}

.hero-cta-row{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:36px}
.hero-cta-primary{display:inline-flex;align-items:center;font-weight:800;color:#fff;background:linear-gradient(120deg,#2f4bd6,#5570e6);padding:13px 26px;border-radius:999px;text-decoration:none;font-size:14.5px;box-shadow:0 6px 18px rgba(47,75,214,.28)}
.hero-cta-secondary{display:inline-flex;align-items:center;font-weight:700;color:#cfd4ef;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.24);padding:12px 25px;border-radius:999px;text-decoration:none;font-size:14.5px}
.hero-stats{display:flex;gap:14px;flex-wrap:wrap}
.hero-stat{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.16);border-radius:12px;padding:16px 22px;min-width:140px}
.hero-stat-value{font-size:24px;font-weight:800;color:#fff;font-variant-numeric:tabular-nums}
.hero-stat-label{font-size:12.5px;color:#9aa4d9;margin-top:4px}

/* ---- トップページ：診断カード・ランキングリンク ---- */
.idx-main{padding-top:8px}
.idx-diagnosis{margin:40px 0;background:linear-gradient(140deg,#f4f6ff 0%,#eef1fc 55%,#f7f4ff 100%);border:1px solid #dfe4f6;border-radius:20px;padding:32px;display:grid;grid-template-columns:1.3fr 1fr;gap:28px;align-items:center}
.idx-diagnosis h2{margin:0 0 12px;border-bottom:none;padding-bottom:0}
.idx-diagnosis-axes{display:flex;flex-direction:column;gap:10px}
.idx-axis{display:grid;grid-template-columns:6.5em 1fr;gap:10px;align-items:center}
.idx-axis-name{font-size:13px;font-weight:700}
.idx-axis-bar{background:#e3e6ec;border-radius:999px;height:9px;overflow:hidden}
.idx-axis-fill{height:100%;border-radius:999px;background:linear-gradient(90deg,#2f4bd6,#6f8bff)}

.idx-ranklinks{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.idx-ranklink{display:block;background:#fff;border:1px solid #e3e6ec;border-radius:14px;padding:16px 18px;text-decoration:none;color:#12141f}
.idx-ranklink:hover{border-color:#c9d2ee;box-shadow:var(--shadow-lift)}
.idx-ranklink-title{font-weight:700;font-size:14px}
.idx-ranklink-note{font-size:12.5px;color:#66707f;margin-top:5px}

@media(max-width:700px){
  .idx-diagnosis{grid-template-columns:1fr}
  .hero-inner h1{font-size:22px}
}
"""


APP_JS = r"""(function () {
  "use strict";
  var base = document.body.getAttribute("data-base") || "";
  var dataPromise = null;
  var koumuPromise = null;

  function loadData() {
    if (!dataPromise) {
      dataPromise = fetch(base + "data/companies.json").then(function (r) { return r.json(); });
    }
    return dataPromise;
  }

  // 官公庁・政府系（data/koumu/、koumu.py が書き出す）。検索窓だけが使うので
  // loadData() とは別キャッシュにし、企業データ側（比較ページ等）に影響を与えない。
  function loadKoumu() {
    if (!koumuPromise) {
      // koumu.json が無くても（官公庁データ未生成等）検索全体を止めない
      koumuPromise = fetch(base + "data/koumu.json")
        .then(function (r) { return r.ok ? r.json() : []; })
        .catch(function () { return []; });
    }
    return koumuPromise;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Google等のfavicon代行はドメインによって16x16固定で返ってくることを確認済みで、
  // 拡大表示すると粗くなるため使わない。実物のアイコンが無い会社は何も出さない。
  function logoImg(icon, size) {
    size = size || 16;
    if (!icon) return "";
    return '<img class="co-logo" src="' + icon + '" width="' + size + '" height="' + size + '" alt="">';
  }

  function manYen(v) {
    return typeof v === "number" ? "約" + Math.round(v / 10000).toLocaleString() + "万円" : "非公表";
  }
  function pct1(v) {
    return typeof v === "number" ? (v * 100).toFixed(1) + "%" : "非公表";
  }
  function num0(v, unit) {
    return typeof v === "number" ? v.toLocaleString() + (unit || "") : "非公表";
  }

  // ---- 検索窓（ヘッダー・フッター共通） ----
  function initSearch() {
    var widgets = document.querySelectorAll(".site-search");
    if (!widgets.length) return;
    Promise.all([loadData(), loadKoumu()]).then(function (results) {
      widgets.forEach(function (w) { wireWidget(w, results[0].companies, results[1]); });
    });
  }

  function wireWidget(widget, companies, koumu) {
    var input = widget.querySelector(".search-input");
    var results = widget.querySelector(".search-results");
    var activeIndex = -1;

    function render(matches) {
      if (!matches.length) {
        results.innerHTML = '<div class="sr-empty">一致する企業・機関がありません</div>';
        results.hidden = false;
        return;
      }
      results.innerHTML = matches.map(function (m) {
        return '<a href="' + m.href + '">' + logoImg(m.icon) + '<span>' + esc(m.name) +
          '</span><span class="sr-group">' + esc(m.group) + "</span></a>";
      }).join("");
      results.hidden = false;
      activeIndex = -1;
    }

    function search(q) {
      q = q.trim().toLowerCase();
      if (!q) { results.hidden = true; results.innerHTML = ""; return; }
      var companyMatches = companies.filter(function (c) {
        return c.name.toLowerCase().indexOf(q) !== -1 ||
          c.slug.toLowerCase().indexOf(q) !== -1 ||
          c.group.toLowerCase().indexOf(q) !== -1;
      }).map(function (c) {
        return { name: c.name, icon: c.icon, group: c.group, href: base + "kigyou/" + c.slug + ".html" };
      });
      var koumuMatches = koumu.filter(function (e) {
        return e.name.toLowerCase().indexOf(q) !== -1 ||
          e.slug.toLowerCase().indexOf(q) !== -1 ||
          e.group.toLowerCase().indexOf(q) !== -1 ||
          (e.alias && e.alias.toLowerCase().indexOf(q) !== -1);
      }).map(function (e) {
        return { name: e.name, icon: e.icon, group: e.group, href: base + "koumu/" + e.slug + ".html" };
      });
      render(companyMatches.concat(koumuMatches).slice(0, 8));
    }

    input.addEventListener("input", function () { search(input.value); });
    input.addEventListener("keydown", function (ev) {
      var items = results.querySelectorAll("a");
      if (ev.key === "ArrowDown" && items.length) {
        ev.preventDefault();
        activeIndex = Math.min(activeIndex + 1, items.length - 1);
        items.forEach(function (a, i) { a.classList.toggle("active", i === activeIndex); });
        items[activeIndex].scrollIntoView({ block: "nearest" });
      } else if (ev.key === "ArrowUp" && items.length) {
        ev.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        items.forEach(function (a, i) { a.classList.toggle("active", i === activeIndex); });
      } else if (ev.key === "Enter") {
        if (activeIndex >= 0 && items[activeIndex]) {
          window.location.href = items[activeIndex].href;
        } else if (items.length) {
          window.location.href = items[0].href;
        }
      } else if (ev.key === "Escape") {
        results.hidden = true;
        input.blur();
      }
    });
    input.addEventListener("blur", function () {
      setTimeout(function () { results.hidden = true; }, 150);
    });
    input.addEventListener("focus", function () {
      if (input.value.trim()) search(input.value);
    });
  }

  // ---- 企業を選んで比較するページ ----
  function initCompare() {
    var root = document.getElementById("compare-app");
    if (!root) return;
    var resultEl = document.getElementById("compare-result");
    var countEl = document.getElementById("compare-count");
    var clearBtn = document.getElementById("compare-clear");
    var checkboxes = root.querySelectorAll('input[type="checkbox"][data-slug]');

    loadData().then(function (data) {
      var bySlug = {};
      data.companies.forEach(function (c) { bySlug[c.slug] = c; });

      function selectedSlugs() {
        return Array.prototype.slice.call(checkboxes)
          .filter(function (cb) { return cb.checked; })
          .map(function (cb) { return cb.dataset.slug; });
      }

      function hbar(rows, fmt, scalePct) {
        var vals = rows.map(function (r) { return r[2]; }).filter(function (v) { return v !== null && v !== undefined; });
        if (!vals.length) return '<p><span class="na">非公表</span></p>';
        var max = Math.max.apply(null, vals) || 1;
        var sorted = rows.slice().sort(function (a, b) {
          var av = a[2], bv = b[2];
          if (av === null || av === undefined) return 1;
          if (bv === null || bv === undefined) return -1;
          return bv - av;
        });
        var html = '<div class="hbars">';
        sorted.forEach(function (r) {
          var name = r[0], href = r[1], v = r[2], icon = r[3];
          if (v === null || v === undefined) {
            html += '<div class="hbar-row"><span class="hbar-name" title="' + esc(name) +
              '"><a href="' + href + '">' + logoImg(icon) + esc(name) + '</a></span>' +
              '<span class="hbar-track"></span><span class="hbar-val na">非公表</span></div>';
          } else {
            var w = scalePct ? v * 100 : (v / max * 100);
            html += '<div class="hbar-row"><span class="hbar-name" title="' + esc(name) +
              '"><a href="' + href + '">' + logoImg(icon) + esc(name) + '</a></span>' +
              '<span class="hbar-track"><span class="hbar-fill" style="width:' + Math.max(w, 1.5).toFixed(1) + '%"></span></span>' +
              '<span class="hbar-val">' + fmt(v) + "</span></div>";
          }
        });
        html += "</div>";
        return html;
      }

      function render() {
        var slugs = selectedSlugs();
        if (countEl) countEl.textContent = slugs.length + "社選択中";

        var url = new URL(window.location.href);
        if (slugs.length) { url.searchParams.set("c", slugs.join(",")); } else { url.searchParams.delete("c"); }
        window.history.replaceState(null, "", url.pathname + url.search);

        if (!slugs.length) {
          resultEl.innerHTML = '<p class="lead">上のリストから企業を選ぶと、ここに横比較が表示されます。</p>';
          return;
        }
        var picked = slugs.map(function (s) { return bySlug[s]; }).filter(Boolean);
        var rowsFor = function (key) {
          return picked.map(function (c) { return [c.name, base + "kigyou/" + c.slug + ".html", c[key], c.icon]; });
        };
        var tableRows = picked.map(function (c) {
          return "<tr><th scope=\"row\"><a href=\"" + base + "kigyou/" + c.slug + ".html\">" + logoImg(c.icon) + esc(c.name) + "</a></th>" +
            "<td>" + esc((c.period || "").slice(0, 7)) + "期</td>" +
            "<td>" + manYen(c.salary) + "</td>" +
            "<td>" + (typeof c.age === "number" ? c.age.toFixed(1) + "歳" : "非公表") + "</td>" +
            "<td>" + (typeof c.tenure === "number" ? c.tenure.toFixed(1) + "年" : "非公表") + "</td>" +
            "<td>" + num0(c.employees_single, "人") + "</td>" +
            "<td>" + num0(c.employees_consolidated, "人") + "</td>" +
            "<td>" + pct1(c.female_manager_ratio) + "</td>" +
            "<td>" + pct1(c.wage_ratio_all) + "</td></tr>";
        }).join("");

        resultEl.innerHTML =
          "<section><h2>平均年間給与</h2>" + hbar(rowsFor("salary"), manYen, false) + "</section>" +
          "<section><h2>女性管理職比率</h2>" + hbar(rowsFor("female_manager_ratio"), pct1, true) + "</section>" +
          "<section><h2>男女の賃金の差異（全労働者）</h2>" + hbar(rowsFor("wage_ratio_all"), pct1, true) + "</section>" +
          '<section><h2>詳細データ</h2><div class="scroll"><table class="grid rank"><thead><tr>' +
          "<th>会社</th><th>決算期</th><th>平均年間給与</th><th>平均年齢</th><th>平均勤続年数</th>" +
          "<th>従業員数(単体)</th><th>従業員数(連結)</th><th>女性管理職比率</th><th>男女の賃金の差異</th>" +
          "</tr></thead><tbody>" + tableRows + "</tbody></table></div></section>";
      }

      checkboxes.forEach(function (cb) { cb.addEventListener("change", render); });
      if (clearBtn) {
        clearBtn.addEventListener("click", function () {
          checkboxes.forEach(function (cb) { cb.checked = false; });
          render();
        });
      }

      var params = new URLSearchParams(window.location.search);
      var pre = params.get("c");
      if (pre) {
        var want = {};
        pre.split(",").forEach(function (s) { want[s] = true; });
        checkboxes.forEach(function (cb) {
          if (want[cb.dataset.slug]) {
            cb.checked = true;
            var det = cb.closest("details");
            if (det) det.open = true;
          }
        });
      }
      render();
    });
  }

  // ---- 就活の軸診断（shindan.html でのみ動く） ----
  // 回答はどこにも送らない。fetch するのは自サイトの静的JSONだけ。
  function initShindan() {
    var root = document.getElementById("shindan");
    if (!root) return;

    var D = null, qi = 0, answers = [];
    var elQuiz = root.querySelector(".sd-quiz");
    var elIntro = root.querySelector(".sd-intro");
    var elRes = root.querySelector(".sd-result");
    var btns = root.querySelectorAll(".sd-choice");
    var back = root.querySelector(".sd-back");

    function show(sec) {
      [elIntro, elQuiz, elRes].forEach(function (s) { s.hidden = s !== sec; });
      root.setAttribute("data-state", sec === elIntro ? "intro" : sec === elQuiz ? "quiz" : "result");
      if (sec !== elIntro) sec.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function paintQuestion(dir) {
      var q = D.questions[qi];
      root.querySelector(".sd-no").textContent = qi + 1;
      root.querySelector(".sd-bar").style.width = ((qi) / D.questions.length * 100) + "%";
      btns[0].querySelector("span").textContent = q.at;
      btns[1].querySelector("span").textContent = q.bt;
      back.hidden = qi === 0;
      var wrap = root.querySelector(".sd-choices");
      wrap.classList.remove("in-l", "in-r");
      void wrap.offsetWidth;                       // アニメーションを再生させるための強制リフロー
      wrap.classList.add(dir === "back" ? "in-r" : "in-l");
    }

    function answer(side) {
      answers[qi] = side;
      qi++;
      if (qi >= D.questions.length) { result(); return; }
      paintQuestion();
    }

    function weights() {
      var w = {};
      D.axes.forEach(function (a) { w[a.key] = 0; });
      answers.forEach(function (side, i) {
        var q = D.questions[i];
        w[side === "a" ? q.a : q.b] += 1;
      });
      return w;
    }

    var FMT = {
      pay: function (v) { return manYen(v); },
      tenure: function (v) { return v.toFixed(1) + "年"; },
      women: pct1, wagegap: pct1, global: pct1, stability: pct1,
      scale: function (v) { return num0(v, "人"); },
      growth: function (v) { return (v * 100 >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%"; }
    };

    function result() {
      root.querySelector(".sd-bar").style.width = "100%";
      var w = weights();
      var axes = D.axes.slice().sort(function (a, b) { return w[b.key] - w[a.key]; });
      var maxw = Math.max.apply(null, D.axes.map(function (a) { return w[a.key]; })) || 1;

      root.querySelector(".sd-axes").innerHTML = axes.map(function (a, i) {
        var pctw = Math.round(w[a.key] / maxw * 100);
        return '<div class="sd-axis" style="--i:' + i + '">' +
          '<span class="sd-axis-name">' + esc(a.label) + "</span>" +
          '<span class="sd-axis-bar"><i style="width:' + pctw + '%"></i></span>' +
          '<span class="sd-axis-val">' + w[a.key] + "/3</span></div>";
      }).join("");

      var top = axes.filter(function (a) { return w[a.key] > 0; }).slice(0, 3)
        .map(function (a) { return a.label; });
      var zero = axes.filter(function (a) { return w[a.key] === 0; }).map(function (a) { return a.label; });
      root.querySelector(".sd-axes-note").innerHTML =
        "12問のうち、あなたが選んだ回数です。上位は<b>" + esc(top.join("・")) + "</b>。" +
        (zero.length ? "一度も選ばなかったのは" + esc(zero.join("・")) + "でした。" : "") +
        "この重みで下の順位を出しています。";

      var sel = root.querySelector(".sd-group");
      if (sel.options.length <= 1) {
        var groups = {};
        D.companies.forEach(function (c) { groups[c.g] = 1; });
        Object.keys(groups).sort().forEach(function (g) {
          var o = document.createElement("option"); o.value = g; o.textContent = g; sel.appendChild(o);
        });
      }
      renderTable(w);
      show(elRes);
    }

    function renderTable(w) {
      var noHd = root.querySelector(".sd-nohd").checked;
      var group = root.querySelector(".sd-group").value;
      var keys = D.axes.map(function (a) { return a.key; }).filter(function (k) { return w[k] > 0; });
      var labelOf = {}; D.axes.forEach(function (a) { labelOf[a.key] = a.short; });

      var rows = D.companies.filter(function (c) {
        if (noHd && c.h) return false;
        if (group && c.g !== group) return false;
        return keys.some(function (k) { return c.p[k] !== undefined; });
      }).map(function (c) {
        var sum = 0, wsum = 0, used = [];
        keys.forEach(function (k) {
          if (c.p[k] === undefined) return;        // 欠損は平均で埋めず、その軸を外す
          sum += c.p[k] * w[k]; wsum += w[k]; used.push(k);
        });
        var score = wsum ? sum / wsum : 0;
        // 「なぜ上位なのか」＝重み×順位が大きい軸を2つ出す
        var why = used.slice().sort(function (a, b) { return c.p[b] * w[b] - c.p[a] * w[a]; }).slice(0, 2);
        return { c: c, score: score, used: used.length, why: why };
      }).sort(function (a, b) { return b.score - a.score; }).slice(0, 50);

      root.querySelector(".sd-table tbody").innerHTML = rows.map(function (r, i) {
        var why = r.why.map(function (k) {
          var v = r.c.v[k];
          var t = (FMT[k] && typeof v === "number") ? FMT[k](v) : "";
          return '<span class="sd-why">' + esc(labelOf[k]) + (t ? " " + esc(t) : "") + "</span>";
        }).join(" ");
        return '<tr style="--i:' + i + '"><td class="rank-no">' + (i + 1) + "</td>" +
          '<th scope="row"><a href="' + base + "kigyou/" + esc(r.c.s) + '.html">' + esc(r.c.n) + "</a></th>" +
          '<td><span class="sd-score"><i style="width:' + Math.round(r.score * 100) + '%"></i></span>' +
          Math.round(r.score * 100) + "</td>" +
          "<td>" + why + '<span class="sd-used">' + r.used + "軸で評価</span></td>" +
          '<td class="small">' + esc(r.c.g) + "</td></tr>";
      }).join("");
    }

    root.querySelector(".sd-start").addEventListener("click", function () {
      fetch(base + "data/shindan.json").then(function (r) { return r.json(); }).then(function (d) {
        D = d; qi = 0; answers = []; show(elQuiz); paintQuestion();
      });
    });
    btns.forEach(function (b) {
      b.addEventListener("click", function () { answer(b.getAttribute("data-side")); });
    });
    back.addEventListener("click", function () { if (qi > 0) { qi--; paintQuestion("back"); } });
    root.querySelector(".sd-retry").addEventListener("click", function () {
      qi = 0; answers = []; show(elQuiz); paintQuestion();
    });
    root.querySelector(".sd-nohd").addEventListener("change", function () { renderTable(weights()); });
    root.querySelector(".sd-group").addEventListener("change", function () { renderTable(weights()); });
  }

  // ---- 学部・興味から絞る診断（tekisei.html でのみ動く） ----
  // shindan.js と同じく、回答はどこにも送らない。fetch するのは自サイトの静的JSONだけ。
  function initTekisei() {
    var root = document.getElementById("tekisei");
    if (!root) return;

    var D = null;
    var faculty = null;                // 1つだけ選ぶ
    var jobs = [];                     // 最大2つ
    var elPick = root.querySelector(".tk-pick");
    var elRes = root.querySelector(".tk-result");
    var goBtn = root.querySelector(".tk-go");

    function syncGo() {
      goBtn.disabled = !faculty;
    }

    root.querySelectorAll('.tk-opt[data-kind="faculty"]').forEach(function (b) {
      b.addEventListener("click", function () {
        faculty = b.dataset.key;
        root.querySelectorAll('.tk-opt[data-kind="faculty"]').forEach(function (x) {
          x.classList.toggle("is-on", x === b);
        });
        syncGo();
      });
    });
    root.querySelectorAll('.tk-opt[data-kind="job"]').forEach(function (b) {
      b.addEventListener("click", function () {
        var key = b.dataset.key;
        var i = jobs.indexOf(key);
        if (i >= 0) {
          jobs.splice(i, 1);
          b.classList.remove("is-on");
        } else {
          if (jobs.length >= 2) {
            var oldest = jobs.shift();
            root.querySelectorAll('.tk-opt[data-kind="job"]').forEach(function (x) {
              if (x.dataset.key === oldest) x.classList.remove("is-on");
            });
          }
          jobs.push(key);
          b.classList.add("is-on");
        }
      });
    });

    function matchScore(c) {
      if (faculty !== "toranai" && c.faculty.indexOf(faculty) === -1) return -1;
      if (!jobs.length) return 0;
      var hit = jobs.filter(function (j) { return c.job.indexOf(j) !== -1; }).length;
      return hit;                      // やりたい仕事を選んだのに1つも一致しないら除外する
    }

    var curMatched = [], curFacLabel = {};

    function render(scroll) {
      var facLabel = {}; D.faculty_options.forEach(function (o) { facLabel[o.key] = o.label; });
      var jobLabel = {}; D.job_options.forEach(function (o) { jobLabel[o.key] = o.label; });

      var matched = D.companies.map(function (c) {
        return { c: c, score: matchScore(c) };
      }).filter(function (r) { return jobs.length ? r.score > 0 : r.score >= 0; });
      curMatched = matched; curFacLabel = facLabel;

      var groupCount = {};
      matched.forEach(function (r) {
        groupCount[r.c.g] = (groupCount[r.c.g] || 0) + 1;
      });
      var groups = Object.keys(groupCount).sort(function (a, b) { return groupCount[b] - groupCount[a]; });

      root.querySelector(".tk-summary").innerHTML =
        "<b>" + esc(facLabel[faculty]) + "</b>" +
        (jobs.length ? "・希望する仕事：<b>" + esc(jobs.map(function (j) { return jobLabel[j]; }).join("、")) + "</b>" : "") +
        " に一致する業界は <b>" + groups.length + "</b>、会社は <b>" + matched.length + "</b> 件です。";

      root.querySelector(".tk-groups tbody").innerHTML = groups.map(function (g) {
        var tagRow = D.group_tags.filter(function (t) { return t.group === g; })[0] || { faculty: [], job: [] };
        return "<tr><th scope=\"row\">" + esc(g) + "</th><td>" + groupCount[g] + "社</td>" +
          "<td class=\"small\">" + esc(tagRow.faculty.join("・")) + "</td>" +
          "<td class=\"small\">" + esc(tagRow.job.join("・") || "―") + "</td></tr>";
      }).join("");

      renderCompanies(matched, facLabel);
      elPick.hidden = true; elRes.hidden = false;
      if (scroll) elRes.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function renderCompanies(matched, facLabel) {
      var noHd = root.querySelector(".tk-nohd").checked;
      var byOverseas = root.querySelector(".tk-globalsort").checked;
      var rows = matched.filter(function (r) { return !noHd || !r.c.h; }).slice();
      rows.sort(function (a, b) {
        if (byOverseas) {
          var ao = a.c.overseas == null ? -1 : a.c.overseas, bo = b.c.overseas == null ? -1 : b.c.overseas;
          if (bo !== ao) return bo - ao;
        }
        if (b.score !== a.score) return b.score - a.score;
        return (b.c.emp || 0) - (a.c.emp || 0);
      });
      rows = rows.slice(0, 200);

      root.querySelector(".tk-table tbody").innerHTML = rows.map(function (r) {
        var c = r.c;
        var faculty_cell;
        if (c.target_faculty_fact) {
          faculty_cell = esc(c.target_faculty_fact) + ' <span class="tk-fact">採用ページに記載</span>';
        } else {
          faculty_cell = '<span class="tk-guess">' + esc(c.faculty.map(function (f) { return facLabel[f]; }).join("・")) +
            "（業界の一般的傾向）</span>";
        }
        return '<tr><th scope="row"><a href="' + base + "kigyou/" + esc(c.s) + '.html">' + esc(c.n) + "</a></th>" +
          "<td>" + esc(c.g) + "</td><td>" + num0(c.emp, "人") + "</td><td>" + faculty_cell + "</td></tr>";
      }).join("");
    }

    goBtn.addEventListener("click", function () {
      fetch(base + "data/tekisei.json").then(function (r) { return r.json(); }).then(function (d) {
        D = d; render();
      });
    });
    root.querySelector(".tk-retry").addEventListener("click", function () {
      elPick.hidden = false; elRes.hidden = true;
      elPick.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    root.querySelector(".tk-nohd").addEventListener("change", function () { if (D) render(); });
    root.querySelector(".tk-globalsort").addEventListener("change", function () { if (D) render(); });
  }

  // ---- 新卒の採用枠（企業ページに実行時で差し込む） v2（新デザイン対応） ----
  // HTMLには焼き込まない。data/saiyo.json をスラッグで引いて、見つかった会社だけ
  // 挿入先を #kihon（基本データ）直前 → <main> 先頭 → .extlinks 直後（旧デザイン互換）
  // の順にフォールバック探索してセクションを追加する。
  // データが増えても data/saiyo.json を更新するだけで全ページに反映される
  // （企業ページ1,500枚超を再生成しなくてよい）。
  var SAIYO_FIELD_LABEL = { conditions: "条件", overview: "概要", timing: "時期" };

  function saiyoEsc(x) {
    return String(x).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function buildSaiyoSectionHTML(s) {
    var order = ["conditions", "overview", "timing"];

    var tracks = s.tracks.map(function (t) {
      var present = order.filter(function (k) { return t[k]; });
      var lead = present[0] || null;
      var rest = present.slice(1);

      var leadHtml = lead
        ? '<p class="saiyo-field"><b>' + SAIYO_FIELD_LABEL[lead] + "：</b>" + saiyoEsc(t[lead]) + "</p>"
        : "";

      var moreHtml = "";
      if (rest.length) {
        var fields = rest.map(function (k) {
          return '<p class="saiyo-field"><b>' + SAIYO_FIELD_LABEL[k] + "：</b>" + saiyoEsc(t[k]) + "</p>";
        }).join("");
        var label = rest.map(function (k) { return SAIYO_FIELD_LABEL[k]; }).join("・") + "を見る";
        moreHtml = '<details class="saiyo-more"><summary>' + label + "</summary>" + fields + "</details>";
      }

      return '<div class="saiyo-track"><h3>' + saiyoEsc(t.name) + "</h3>" + leadHtml + moreHtml + "</div>";
    }).join("");

    var faculty = s.target_faculty ? "　対象：" + saiyoEsc(s.target_faculty) : "";

    var notes = (s.notes || []).map(function (n) { return "<li>" + saiyoEsc(n) + "</li>"; }).join("");
    var notesHtml = notes ? '<ul class="saiyo-notes">' + notes + "</ul>" : "";

    var srcs = (s.source_urls || []).map(function (u) {
      var host = "";
      try { host = new URL(u).hostname; } catch (e) { host = u; }
      return '<a href="' + saiyoEsc(u) + '" rel="nofollow noopener" target="_blank">' + saiyoEsc(host) + "</a>";
    }).join(" ");

    return (
      '<div class="saiyo-head"><h2>新卒の採用枠</h2>' +
      '<span class="saiyo-badge">出典：公式採用ページ（有価証券報告書ではありません）</span></div>' +
      '<p class="saiyo-lead">公式の採用ページに書かれていた内容だけを要約しています。' + faculty + "</p>" +
      '<div class="saiyo-tracks">' + tracks + "</div>" +
      notesHtml +
      '<p class="caveat">採用ページ（' + srcs + "）を" + saiyoEsc(s.fetched_at || "") + "時点でこのセッションが読み、" +
      "書かれていた内容だけを要約したものです。募集要項は年度ごとに変わります。<b>最新の内容は必ず公式の採用ページで確認してください。</b></p>"
    );
  }

  function injectSaiyoSection(s) {
    if (!s || !s.tracks || !s.tracks.length) return;
    if (document.getElementById("saiyo")) return; // 二重挿入ガード

    var section = document.createElement("section");
    section.id = "saiyo";
    section.className = "saiyo-section";
    section.innerHTML = buildSaiyoSectionHTML(s);

    var kihon = document.getElementById("kihon");
    var mainEl = document.querySelector("main");
    var ext = document.querySelector(".extlinks");
    if (kihon && kihon.parentNode) {
      kihon.parentNode.insertBefore(section, kihon);       // 新デザイン：基本データの直前（冒頭）
    } else if (mainEl) {
      mainEl.insertBefore(section, mainEl.firstChild);      // フォールバック：main 先頭
    } else if (ext) {
      ext.insertAdjacentElement("afterend", section);       // 旧デザイン互換
    } else {
      return;
    }

    // sticky ナビに「採用枠」タブを追加（新デザインのみ：#kihon タブの存在で判定）
    var tab = document.querySelector('a[href="#kihon"]');
    if (tab && !document.querySelector('a[href="#saiyo"]')) {
      var t2 = tab.cloneNode(false); // 属性（インラインstyle含む）ごと複製し、見た目を揃える
      t2.href = "#saiyo";
      t2.textContent = "採用枠";
      tab.parentNode.insertBefore(t2, tab);
    }
  }

  function initSaiyoInject() {
    var m = /\/kigyou\/([^/]+)\.html/.exec(location.pathname);
    var slug = m ? m[1] : null;
    if (!slug) return;

    fetch(base + "data/saiyo.json")
      .then(function (r) { return r.json(); })
      .then(function (all) { injectSaiyoSection(all[slug]); })
      .catch(function () { /* データ取得に失敗しても他の表示は止めない */ });
  }

  // ---- 業界ショーケース（トップページ：横に流れる企業帯） ----
  function initShowcase() {
    var root = document.getElementById("showcase");
    if (!root) return;
    var tabs = root.querySelectorAll(".tab-btn");
    var lanes = root.querySelectorAll(".lane");
    var ctaLink = document.getElementById("showcase-cta-link");
    var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var timer = null;
    var pausedLane = null;
    var resumeTimer = null;

    // .lane-track は overflow-x:auto の普通のスクロール領域（ユーザーがドラッグ/スワイプ/
    // ホイールで自由に動かせる）。自動送りはその同じ scrollLeft を一定間隔で
    // チップ1つぶん進めるだけなので、手動スクロールと取り合いにならない。
    // scrollTo({behavior:"smooth"}) はブラウザ・環境によって効かないことがあるため、
    // 自前で requestAnimationFrame により scrollLeft を easing させる（挙動を環境に依存させない）。
    function stride(track) {
      var chip = track.querySelector(".chip");
      if (!chip) return 0;
      var gap = parseFloat(getComputedStyle(track).columnGap) || 0;
      return chip.getBoundingClientRect().width + gap;
    }

    function easeScrollTo(track, target, duration) {
      var startX = track.scrollLeft, delta = target - startX, startTime = null;
      if (!delta) return;
      function step(ts) {
        if (startTime === null) startTime = ts;
        var t = Math.min(1, (ts - startTime) / duration);
        var eased = 1 - Math.pow(1 - t, 3);
        track.scrollLeft = startX + delta * eased;
        if (t < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    }

    function advance(track) {
      var st = stride(track);
      if (!st) return;
      var repeat = parseInt(track.dataset.repeat, 10) || 2;
      var one = track.scrollWidth / repeat;          // 複製前1コピーぶんの幅
      var next = track.scrollLeft + st;
      if (next >= one - 1) {
        track.scrollLeft = next - one;                // 継ぎ目を見せずに先頭へジャンプ
      } else {
        easeScrollTo(track, next, 420);
      }
    }

    function stopAuto() {
      if (timer) { clearInterval(timer); timer = null; }
    }

    function startAuto(lane) {
      stopAuto();
      if (reduceMotion || !lane) return;
      var track = lane.querySelector(".lane-track");
      if (!track) return;
      timer = setInterval(function () {
        if (pausedLane === lane) return;
        advance(track);
      }, 2600);                                       // 1社ぶん進めて、次まで数秒止まる
    }

    function activate(tab) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.classList.toggle("is-active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
      });
      var activeLane = null;
      lanes.forEach(function (l) {
        var on = l.id === tab.dataset.target;
        l.classList.toggle("is-active", on);
        if (on) activeLane = l;
      });
      if (ctaLink) {
        ctaLink.href = tab.dataset.href;
        ctaLink.textContent = tab.dataset.group + tab.dataset.count + "社を1つの表で比較する →";
      }
      startAuto(activeLane);
    }

    tabs.forEach(function (t) {
      t.addEventListener("click", function () { activate(t); });
    });

    // 触っている間は自動送りを止める。動いたままだと狙った企業をクリック/ドラッグしにくいため
    lanes.forEach(function (l) {
      var pause = function () {
        pausedLane = l;
        if (resumeTimer) { clearTimeout(resumeTimer); resumeTimer = null; }
      };
      var resume = function (delay) {
        if (resumeTimer) clearTimeout(resumeTimer);
        resumeTimer = setTimeout(function () {
          if (pausedLane === l) pausedLane = null;
        }, delay || 0);
      };
      l.addEventListener("mouseenter", pause);
      l.addEventListener("mouseleave", function () { resume(0); });
      l.addEventListener("focusin", pause);
      l.addEventListener("focusout", function () { resume(0); });
      l.addEventListener("wheel", pause, { passive: true });
      l.addEventListener("touchstart", pause, { passive: true });
      l.addEventListener("touchend", function () { resume(2500); }, { passive: true });
    });

    startAuto(root.querySelector(".lane.is-active"));
  }

  // ---- スクロールで現れる（見えたときに一度だけ） ----
  // 隠すのは html.js-anim が付いている間だけ。IntersectionObserver が
  // 何らかの理由で発火しない環境（描画されないタブなど）に備えて、
  // 一定時間後に必ず全部を表示する安全網を置く。本文が読めないほうが害が大きい。
  //
  // threshold は必ず 0 にする。**割合で指定すると背の高い要素が永久に隠れる。**
  // トップページの掲載企業欄は高さ7万pxあり、threshold 0.02 は「1,400px以上見えたら」
  // という意味になって、画面の高さを超えるため一度も満たされなかった。
  // 同じ理由で、画面より背の高い要素はそもそも隠さない（現れる演出の意味もない）。
  function initReveal() {
    if (!("IntersectionObserver" in window)) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    var all = document.querySelectorAll("main section, main .scroll, main h1, main .lead");
    if (!all.length) return;
    var vh = window.innerHeight || 800;
    var targets = [];
    for (var i = 0; i < all.length; i++) {
      if (all[i].getBoundingClientRect().height <= vh * 1.5) targets.push(all[i]);
    }
    if (!targets.length) return;
    var root = document.documentElement;
    root.classList.add("js-anim");
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("is-in"); io.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0 });
    targets.forEach(function (t) { t.classList.add("reveal"); io.observe(t); });
    // 安全網。2.5秒後、まだ隠れたままで画面内に入っている要素は Observer が
    // 働いていないということなので表示に倒す（1つでも発火していれば、で判定しない。
    // 上部だけ発火して下が永久に隠れる、という今回の壊れ方を検出できないため）
    setTimeout(function () {
      var stuck = targets.filter(function (t) {
        var r = t.getBoundingClientRect();
        return !t.classList.contains("is-in") && r.top < vh && r.bottom > 0;
      });
      if (!stuck.length) return;
      root.classList.remove("js-anim");
      targets.forEach(function (t) { t.classList.add("is-in"); });
      io.disconnect();
    }, 2500);
  }

  // ---- ヘッダーを縮める・読了バー ----
  function initChrome() {
    var head = document.querySelector("header.site");
    var bar = document.createElement("div");
    bar.className = "read-bar";
    document.body.appendChild(bar);
    var ticking = false;
    function onScroll() {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        var y = window.pageYOffset || document.documentElement.scrollTop;
        if (head) head.classList.toggle("is-stuck", y > 12);
        var h = document.documentElement.scrollHeight - window.innerHeight;
        bar.style.transform = "scaleX(" + (h > 0 ? Math.min(1, y / h) : 0) + ")";
        ticking = false;
      });
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // ---- トップページ：ヒーローカルーセル（注目企業、自動送り＋矢印） ----
  function initHeroCarousel() {
    var track = document.getElementById("hero-carousel");
    if (!track) return;
    var wrap = track.closest(".hero-carousel-wrap");
    var prevBtn = wrap.querySelector(".hero-car-prev");
    var nextBtn = wrap.querySelector(".hero-car-next");
    var paused = false, resumeT = null, raf = null, timer = null;

    function half() {
      var kids = track.children;
      if (!kids.length) return 0;
      var mid = kids[Math.floor(kids.length / 2)];
      return mid ? mid.offsetLeft - kids[0].offsetLeft : track.scrollWidth / 2;
    }

    function step(dir) {
      var kids = track.children;
      if (!kids.length) return;
      var w = kids[0].offsetWidth + 14;
      var h = half();
      cancelAnimationFrame(raf);
      if (dir < 0 && track.scrollLeft < w) track.scrollLeft += h;
      var target = track.scrollLeft + dir * w;
      var best = null;
      for (var i = 0; i < kids.length; i++) {
        var off = kids[i].offsetLeft - kids[0].offsetLeft;
        if (best === null || Math.abs(off - target) < Math.abs(best - target)) best = off;
      }
      if (best !== null) target = best;
      var from = track.scrollLeft, dist = target - from, dur = 900, t0 = performance.now();
      function tick(now) {
        var t = Math.min(1, (now - t0) / dur);
        var eased = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
        track.scrollLeft = from + dist * eased;
        if (t < 1) { raf = requestAnimationFrame(tick); }
        else if (track.scrollLeft >= h) { track.scrollLeft -= h; }
      }
      raf = requestAnimationFrame(tick);
    }

    function stopAuto() { if (timer) clearInterval(timer); timer = null; }
    function startAuto() {
      stopAuto();
      if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      timer = setInterval(function () { if (!paused) step(1); }, 3000);
    }
    function pauseFor(ms) {
      paused = true;
      clearTimeout(resumeT);
      resumeT = setTimeout(function () { paused = false; }, ms);
    }

    if (prevBtn) prevBtn.addEventListener("click", function () { pauseFor(4000); step(-1); });
    if (nextBtn) nextBtn.addEventListener("click", function () { pauseFor(4000); step(1); });
    wrap.addEventListener("mouseenter", function () { paused = true; clearTimeout(resumeT); });
    wrap.addEventListener("mouseleave", function () { paused = false; });
    wrap.addEventListener("touchstart", function () { paused = true; clearTimeout(resumeT); }, { passive: true });
    wrap.addEventListener("touchend", function () { pauseFor(2500); }, { passive: true });

    startAuto();
  }

  // ---- 企業ページ：規模の近い同業他社テーブルの列ソート ----
  function initPeerSort() {
    var table = document.getElementById("cp-peer-table");
    if (!table) return;
    var tbody = table.querySelector("tbody");
    var btns = table.querySelectorAll(".cp-sort-btn");
    var state = { key: "emp", dir: -1 };

    function apply() {
      var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
      rows.sort(function (a, b) {
        var aCell = a.querySelector('td[data-key="' + state.key + '"]');
        var bCell = b.querySelector('td[data-key="' + state.key + '"]');
        var av = aCell && aCell.dataset.val !== "" ? parseFloat(aCell.dataset.val) : null;
        var bv = bCell && bCell.dataset.val !== "" ? parseFloat(bCell.dataset.val) : null;
        if (av === null && bv === null) return 0;
        if (av === null) return 1;
        if (bv === null) return -1;
        return state.dir < 0 ? bv - av : av - bv;
      });
      rows.forEach(function (r) { tbody.appendChild(r); });
      btns.forEach(function (b) {
        var on = b.dataset.key === state.key;
        b.classList.toggle("is-active", on);
        var arrow = b.querySelector(".cp-sort-arrow");
        if (arrow) arrow.textContent = on ? (state.dir < 0 ? " ↓" : " ↑") : "";
      });
    }

    btns.forEach(function (b) {
      b.addEventListener("click", function () {
        var key = b.dataset.key;
        if (state.key === key) state.dir = -state.dir;
        else { state.key = key; state.dir = -1; }
        apply();
      });
    });

    apply();
  }

  document.addEventListener("DOMContentLoaded", function () {
    initSearch();
    initCompare();
    initShindan();
    initTekisei();
    initSaiyoInject();
    initShowcase();
    initHeroCarousel();
    initPeerSort();
    initReveal();
    initChrome();
  });
})();
"""


# CSS/JSのURLに内容ハッシュを付ける。付けないと、**直したはずの不具合が
# 一度サイトを見た人にだけ残り続ける**（ブラウザが古い app.js を使い回すため）。
# 中身が変わったときだけ値が変わるので、変えていないときは再ダウンロードされない。
ASSET_V = hashlib.sha1((CSS + APP_JS).encode("utf-8")).hexdigest()[:8]


def main() -> None:
    companies, fetched = load()
    if not companies:
        raise SystemExit("data/companies/*.json が無い。先に extract.py を実行する。")
    order = order_in_csv()
    companies.sort(key=lambda c: order.get(c["edinet_code"], 999))

    groups: dict[str, list[dict]] = {}
    for c in companies:
        groups.setdefault(c["peer_group"], []).append(c)
    newest = max(_period(c) for c in companies)

    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "kigyou").mkdir(parents=True)
    (SITE / "gyoukai").mkdir(parents=True)
    (SITE / "koumu").mkdir(parents=True)
    (SITE / "data").mkdir(parents=True)

    (SITE / "style.css").write_text(CSS, encoding="utf-8")
    (SITE / "app.js").write_text(APP_JS, encoding="utf-8")
    (SITE / "index.html").write_text(index_page(companies, groups, fetched), encoding="utf-8")
    (SITE / "hikaku.html").write_text(hikaku_page(companies, groups, fetched), encoding="utf-8")

    urls = ["/index.html", "/hikaku.html"]

    # ランキングと条件別一覧。財務データ（data/financials）が無くても、
    # 年収・勤続年数など既存の指標ぶんだけが出る設計にしてある。
    # ここで落ちても企業ページ・業界ページは出したいので、失敗はURLに載せず先へ進む。
    try:
        urls += rankings.build(sys.modules[__name__], companies, fetched, SITE)
    except Exception as exc:  # noqa: BLE001 — 商品である企業ページの生成を止めないための握り
        print(f"  !! ランキングの生成に失敗した（企業ページは生成する）: {type(exc).__name__}: {exc}")
    try:
        urls += shindan.build(sys.modules[__name__], companies, fetched, SITE)
    except Exception as exc:  # noqa: BLE001 — 同上
        print(f"  !! 診断の生成に失敗した（企業ページは生成する）: {type(exc).__name__}: {exc}")
    try:
        urls += tekisei.build(sys.modules[__name__], companies, fetched, SITE)
    except Exception as exc:  # noqa: BLE001 — 同上
        print(f"  !! 学部・興味診断の生成に失敗した（企業ページは生成する）: {type(exc).__name__}: {exc}")
    try:
        urls += koumu.build(sys.modules[__name__], fetched, SITE)
    except Exception as exc:  # noqa: BLE001 — 同上
        print(f"  !! 官公庁・政府系ページの生成に失敗した（企業ページは生成する）: {type(exc).__name__}: {exc}")

    for g, members in groups.items():
        path = f"/gyoukai/{GROUP_SLUG.get(g, g)}.html"
        (SITE / path.lstrip("/")).write_text(group_page(g, members, fetched), encoding="utf-8")
        urls.append(path)
    for c in companies:
        path = f"/kigyou/{c['slug']}.html"
        (SITE / path.lstrip("/")).write_text(company_page(c, groups[c["peer_group"]], companies, fetched, newest), encoding="utf-8")
        urls.append(path)

    # クライアント側の絞り込み用（Layer 2 で使う）。サーバーは要らない。
    summary = [
        {
            "code": c["edinet_code"], "slug": c["slug"], "name": c["short"], "group": c["peer_group"],
            "domain": c["domain"],
            "icon": c["icon"],
            "is_holding": c["is_holding"], "period": c["latest"]["source"]["period_end"],
            "salary": c["latest"]["reporting_company"]["average_annual_salary_yen"],
            "age": c["latest"]["reporting_company"]["average_age_years"],
            "tenure": c["latest"]["reporting_company"]["average_tenure_years"],
            "employees_single": c["latest"]["employees"]["reporting_company"],
            "employees_consolidated": c["latest"]["employees"]["consolidated"],
            "female_manager_ratio": c["latest"]["diversity"]["female_manager_ratio"],
            "wage_ratio_all": c["latest"]["diversity"]["female_to_male_wage_ratio_all"],
            "salary_trend": c["trend"]["average_annual_salary_yen"],
            "salary_trend_comparable": c["salary_trend_comparable"],
        }
        for c in companies
    ]
    (SITE / "data" / "companies.json").write_text(
        json.dumps({"generated": dt.date.today().isoformat(), "fetched": fetched, "companies": summary},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    # 新卒の採用枠（fetch_saiyo.py + このセッションが構造化）。
    # ここではHTMLに焼き込まず、スラッグ引きのJSONだけを書く。企業ページ側のJS（initSaiyoInject）が
    # 自分のスラッグで引いて実行時にDOMへ差し込む。データが増えてもこのJSON1本を更新するだけでよく、
    # 1,500ページ超の再生成が要らない（他セッションと同じgenerate.pyを同時に触るリスクを減らすため）
    saiyo_bundle = {c["slug"]: c["saiyo"] for c in companies if c.get("saiyo")}
    (SITE / "data" / "saiyo.json").write_text(
        json.dumps(saiyo_bundle, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  新卒の採用枠 {len(saiyo_bundle)}社ぶん → data/saiyo.json")

    today = dt.date.today().isoformat()
    entries = "".join(
        f"<url><loc>{BASE_URL}{u}</loc><lastmod>{today}</lastmod></url>\n" for u in urls
    )
    (SITE / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{entries}</urlset>\n',
        encoding="utf-8",
    )
    (SITE / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}/sitemap.xml\n", encoding="utf-8"
    )

    print(f"{len(companies)}社 / {len(groups)}業界 / {len(urls)}ページ → {SITE}")
    for g, m in groups.items():
        print(f"  {g:8s} {len(m)}社  /gyoukai/{GROUP_SLUG.get(g, g)}.html")


if __name__ == "__main__":
    main()
