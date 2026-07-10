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
import html
import json
import math
import os
import shutil
from pathlib import Path

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


def slug_of(c: dict) -> str:
    return SLUG.get(c["edinet_code"], c["edinet_code"])


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
    companies = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(DATA.glob("*.json"))]
    for c in companies:
        c["slug"] = slug_of(c)
        c["short"] = short_name(c["name"])
        pp = PROSE / f"{c['edinet_code']}.json"
        c["prose"] = json.loads(pp.read_text(encoding="utf-8")) if pp.exists() else {}
    idx = ROOT / "data" / "doc_index.csv"
    fetched = dt.date.fromtimestamp(idx.stat().st_mtime).isoformat()
    return companies, fetched


def order_in_csv() -> dict[str, int]:
    with (ROOT / "companies.csv").open(encoding="utf-8", newline="") as f:
        return {r["edinet_code"]: i for i, r in enumerate(csv.DictReader(f))}


# ---------------------------------------------------------------- 部品

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
<link rel="stylesheet" href="{up}style.css">
</head>
<body>
<header class="site">
  <a class="brand" href="{up}index.html">
    <svg width="28" height="28" viewBox="0 0 22 22" aria-hidden="true">
      <rect x="1" y="12" width="5" height="9" rx="1" fill="#fff"/>
      <rect x="8.5" y="6" width="5" height="15" rx="1" fill="#fff" opacity=".78"/>
      <rect x="16" y="1" width="5" height="20" rx="1" fill="#fff" opacity=".5"/>
    </svg>
    <span class="brand-word">{e(SITE_NAME)}</span>
  </a>
  <span class="tagline">{e(TAGLINE)}</span>
</header>
<main>
{body}
</main>
<footer class="site">
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

def company_page(c: dict, peers: list[dict], fetched: str, newest: dt.date) -> str:
    L = c["latest"]
    emp, rc, div = L["employees"], L["reporting_company"], L["diversity"]
    src = L["source"]
    period = src["period_end"][:7]

    rows = [
        ("平均年間給与", f'<b>{yen(rc["average_annual_salary_yen"])}</b>（{man(rc["average_annual_salary_yen"])}）'),
        ("平均年齢", dec1(rc["average_age_years"], "歳")),
        ("平均勤続年数", dec1(rc["average_tenure_years"], "年")),
        ("従業員数（提出会社・単体）", num(emp["reporting_company"], "人")),
        ("従業員数（連結）", num(emp["consolidated"], "人")),
    ]
    basic = "".join(f'<tr><th scope="row">{e(k)}</th><td>{v}</td></tr>' for k, v in rows)

    # 提出会社の多様性指標が1つも無い＝持株会社が本体の指標を開示していない。
    # 「非公表」だけの表を5行並べても情報がないので、そう書いて子会社へ送る。
    div_keys = ["female_manager_ratio", "female_to_male_wage_ratio_all", "female_to_male_wage_ratio_regular",
                "female_to_male_wage_ratio_nonregular", "male_childcare_leave_ratio"]
    div_empty = all(div.get(k) is None for k in div_keys)
    if div_empty:
        to_subs = (
            '実際の勤務先となる<a href="#subsidiaries">連結子会社の指標</a>を参照してください。'
            if L.get("subsidiaries") else ""
        )
        diversity_section = f"""<section>
<h2>多様性の指標</h2>
<p class="note">提出会社（{e("持株会社本体" if c.get("is_holding") else "本体")}）の女性管理職比率・男女の賃金の差異・男性育休取得率は、
有価証券報告書に記載がありません。{to_subs}</p>
</section>"""
    else:
        div_rows = [
            ("女性管理職比率", pct(div["female_manager_ratio"])),
            ("男女の賃金の差異（全労働者）", pct(div["female_to_male_wage_ratio_all"])),
            ("　うち正規雇用労働者", pct(div["female_to_male_wage_ratio_regular"])),
            ("　うち非正規雇用労働者", pct(div["female_to_male_wage_ratio_nonregular"])),
            ("男性育休取得率", childcare_cell(div["male_childcare_leave_ratio"], div["male_childcare_leave_method"])),
        ]
        diversity = "".join(f'<tr><th scope="row">{e(k)}</th><td>{v}</td></tr>' for k, v in div_rows)
        scope = div.get("scope") or "提出会社"
        scope_note = "" if scope == "提出会社" else f'<p class="small">対象範囲：{e(scope)}</p>'

        childcare_ratio = div["male_childcare_leave_ratio"]
        childcare_pct = childcare_ratio * 100 if childcare_ratio is not None else None
        method_tag = ""
        if div["male_childcare_leave_method"]:
            tag = "第2号方式" if "2号" in div["male_childcare_leave_method"] else "原則方式"
            mcls = "method2" if tag == "第2号方式" else "method1"
            method_tag = f'<span class="method {mcls} donut-method">{tag}</span>'
        donuts = f"""<div class="donuts">
<div class="donut-item">{donut(div["female_manager_ratio"] * 100 if div["female_manager_ratio"] is not None else None)}
<div class="donut-label">女性管理職比率</div></div>
<div class="donut-item">{donut(div["female_to_male_wage_ratio_all"] * 100 if div["female_to_male_wage_ratio_all"] is not None else None)}
<div class="donut-label">男女の賃金の差異<br>（全労働者）</div></div>
<div class="donut-item">{donut(childcare_pct)}
<div class="donut-label">男性育休取得率{method_tag}</div></div>
</div>"""

        diversity_section = f"""<section>
<h2>多様性の指標</h2>
{scope_note}
{donuts}
<table class="kv">{diversity}</table>
<p class="caveat">{WAGE_CAVEAT}</p>
<p class="caveat">{CHILDCARE_CAVEAT}</p>
</section>"""

    subs = L.get("subsidiaries") or []
    sub_html = ""
    if subs:
        body = "".join(
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
<tbody>{body}</tbody></table></div>
<p class="caveat">{CHILDCARE_CAVEAT}</p>
</section>"""

    salary_trend = (
        f'<p class="rate">{change_rate(c["trend"]["average_annual_salary_yen"])}</p>'
        if c["salary_trend_comparable"] else ""
    )

    div_trend = ""
    if c["trend"].get("female_manager_ratio") or c["trend"].get("female_to_male_wage_ratio_all"):
        div_trend = f"""<div class="col">
  <h3>女性管理職比率</h3>
  {line_chart(c["trend"].get("female_manager_ratio", {}), lambda v: pct(v))}
</div>
<div class="col">
  <h3>男女の賃金の差異（全労働者）</h3>
  {line_chart(c["trend"].get("female_to_male_wage_ratio_all", {}), lambda v: pct(v))}
</div>"""

    notes = L.get("notes") or []
    notes_html = ""
    if notes:
        items = "".join(f"<li>{e(n)}</li>" for n in notes)
        notes_html = f'<section class="notes"><h2>抽出上の注記</h2><ul>{items}</ul></section>'

    p = c.get("prose") or {}
    prose_html = ""
    if p.get("business"):
        prose_html = f'<section><h2>事業の内容</h2><p>{e(p["business"])}</p></section>'

    peer_links = " ".join(
        f'<a href="{e(x["slug"])}.html">{e(x["short"])}</a>' for x in peers if x["edinet_code"] != c["edinet_code"]
    )
    gslug = GROUP_SLUG.get(c["peer_group"], c["peer_group"])

    body = f"""
<nav class="crumb"><a href="../index.html">トップ</a> › <a href="../gyoukai/{e(gslug)}.html">{e(c["peer_group"])}</a> › {e(c["short"])}</nav>

<h1>{e(c["short"])}の平均年収・男女の賃金の差異・従業員数</h1>
<p class="lead">有価証券報告書（{e(period)}期）から機械的に抽出した数値です。業種：{e(c["industry"])}／証券コード {e(c["sec_code"][:4])}</p>

{stale_note(c, newest)}
{holding_warning(c)}
{trend_breaks(c)}
{prose_html}

<section>
<h2>基本データ（{e(period)}期・提出会社）</h2>
<table class="kv">{basic}</table>
<p class="caveat">{SALARY_CAVEAT}</p>
</section>

<section>
<h2>平均年間給与の推移</h2>
{salary_trend}
{line_chart(c["trend"]["average_annual_salary_yen"], man)}
<h3>従業員数（提出会社）の推移</h3>
{line_chart(c["trend"]["employees_reporting_company"], lambda v: num(v, "人"))}
</section>

{diversity_section}

<div class="cols">{div_trend}</div>

{sub_html}
{notes_html}

<section class="peers">
<h2>同業他社と比べる</h2>
<p><a class="cta" href="../gyoukai/{e(gslug)}.html">{e(c["peer_group"])}{len(peers)}社を1つの表で比較する →</a></p>
<p class="small">{peer_links}</p>
</section>

{source_block(src, fetched)}
"""
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
    rows = []
    for c in members:
        cells = []
        for _, get, fmt, _hi in PEER_COLS:
            v = get(c)
            cells.append(f"<td>{fmt(v) if v is not None else NA}</td>")
        mark = ' <span class="hd" title="持株会社">持株</span>' if c.get("is_holding") else ""
        if (newest - _period(c)).days >= STALE_DAYS:
            mark += ' <span class="stale" title="他社より古い期">古い期</span>'
            stale.append(c["short"])
        rows.append(
            f'<tr><th scope="row"><a href="../kigyou/{e(c["slug"])}.html">{e(c["short"])}</a>{mark}</th>'
            + "".join(cells) + "</tr>"
        )
    stale_caveat = (
        f'<p class="caveat"><b>{e("、".join(stale))}</b> は最新の有価証券報告書がまだ提出されていないため、'
        "一世代前の期の数値です。決算期の列を確認してください。</p>" if stale else ""
    )
    main_table = f"""<div class="scroll"><table class="grid rank">
<thead><tr><th>会社</th>{head}</tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>{stale_caveat}"""

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
        f'<tr><th scope="row"><a href="../kigyou/{e(c["slug"])}.html">{e(c["short"])}</a></th>'
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
        f'<tr><th scope="row"><a href="../kigyou/{e(c["slug"])}.html">{e(c["short"])}</a></th>'
        f'<td>{childcare_cell(c["latest"]["diversity"]["male_childcare_leave_ratio"], c["latest"]["diversity"]["male_childcare_leave_method"])}</td></tr>'
        for c in members
    )
    childcare_table = f"""<div class="scroll"><table class="grid">
<thead><tr><th>会社</th><th>男性育休取得率（算出方式つき）</th></tr></thead>
<tbody>{cc}</tbody></table></div>
<p class="caveat">{CHILDCARE_CAVEAT}<b>そのため、この表は高い順に並べていません。</b></p>"""

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

    body = f"""
<nav class="crumb"><a href="../index.html">トップ</a> › {e(group)}</nav>

<h1>{e(group)}{len(members)}社の平均年収・男女の賃金の差異 横比較</h1>
<p class="lead">有価証券報告書だけを出典に、{e(group)}{len(members)}社を1つの表に並べました。
広告主の都合が入らないよう、掲載企業に費用は請求していません。並び順は平均年間給与の高い順です。</p>

<section>
<h2>基本データ（提出会社）</h2>
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


# ---------------------------------------------------------------- トップ

def index_page(companies: list[dict], groups: dict[str, list[dict]], fetched: str) -> str:
    cards = []
    for g, members in groups.items():
        links = "".join(
            f'<li><a href="kigyou/{e(c["slug"])}.html">{e(c["short"])}</a>'
            f'<span class="sal">{man(c["latest"]["reporting_company"]["average_annual_salary_yen"])}</span></li>'
            for c in sorted(members, key=lambda c: sort_key(c["latest"]["reporting_company"]["average_annual_salary_yen"], True))
        )
        cards.append(f"""<section class="card">
<h2><a href="gyoukai/{e(GROUP_SLUG.get(g, g))}.html">{e(g)}（{len(members)}社）</a></h2>
<ul class="complist">{links}</ul>
<p><a class="cta" href="gyoukai/{e(GROUP_SLUG.get(g, g))}.html">{len(members)}社を横比較する →</a></p>
</section>""")

    body = f"""
<h1>有価証券報告書の数字だけで、同業他社を並べる</h1>
<p class="lead">
平均年収も、男女の賃金の差異も、1社ずつなら調べれば出てきます。しかし<b>同業7社を1つの表に並べたもの</b>は、どこにもありません。
就活情報サイトは企業から広告費を受け取る側なので、企業を不利に並べられないからです。
このサイトは掲載企業から1円も受け取らず、<b>金融庁EDINETの有価証券報告書だけ</b>を出典にして、{len(companies)}社を並べます。
</p>
<p class="lead small">
数値はプログラムがXBRLから機械的に抽出し、有報のPDFと1件ずつ突き合わせて検証しています（{len(companies)}社・計{sum(len(c["years"]) for c in companies)}件の有価証券報告書）。
AIに数字を書かせていません。データが無い項目は推測で埋めず「非公表」と書きます。
</p>
<div class="cards">{"".join(cards)}</div>

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
"""
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
  --radius:12px;--radius-sm:8px
}
*{box-sizing:border-box}
body{margin:0;font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,system-ui,sans-serif;color:var(--fg);background:var(--bg);line-height:1.75;font-size:16px;-webkit-font-smoothing:antialiased;letter-spacing:.01em}
main{max-width:960px;margin:0 auto;padding:0 20px 64px}
header.site{background:linear-gradient(120deg,#10122a 0%,#1d2864 100%)}
header.site>*{max-width:960px;margin:0 auto;padding:0 20px}
.brand{display:flex;align-items:center;gap:10px;padding-top:24px;text-decoration:none}
.brand svg{flex:none}
.brand-word{font-weight:800;font-size:26px;color:#fff;letter-spacing:-.02em}
.tagline{display:block;color:#9aa4d9;font-size:13px;padding:4px 0 22px;margin-left:38px}
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

.cols{display:flex;gap:32px;flex-wrap:wrap}
.col{flex:1 1 320px;min-width:0}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;margin:26px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:6px 18px 16px;box-shadow:var(--shadow);transition:border-color .15s ease,box-shadow .15s ease}
.card:hover{border-color:#c7cff0;box-shadow:0 2px 4px rgba(18,20,31,.05),0 12px 28px rgba(18,20,31,.09)}
.card h2{font-size:16px;border:0;margin:16px 0 8px;padding:0}
.complist{list-style:none;margin:0;padding:0;font-size:14px}
.complist li{display:flex;justify-content:space-between;gap:8px;padding:5px 0;border-bottom:1px dotted var(--line)}
.complist li:last-child{border-bottom:none}
.sal{color:var(--mut);font-size:12px;white-space:nowrap;font-variant-numeric:tabular-nums}
.cta{display:inline-block;margin-top:12px;font-weight:700;font-size:14px;background:var(--fg);color:#fff!important;padding:10px 20px;border-radius:6px;text-decoration:none}
.cta:hover{background:var(--accent-dk)}
.peers .small a{margin-right:10px;white-space:nowrap}
.source{margin-top:48px;font-size:13px;color:var(--mut);background:#f7f8fb;border:1px solid var(--line);border-radius:var(--radius-sm);padding:16px 18px}
.source ul{padding-left:1.2em}
.source h2{font-size:15px;border-bottom-width:1px}
.notes ul{font-size:13px;color:#3d4453}
footer.site{border-top:1px solid var(--line);padding:24px 20px;font-size:12px;color:var(--mut);background:var(--card)}
footer.site>p{max-width:960px;margin:6px auto}
.disclaimer{font-weight:700;color:var(--fg)}
@media(max-width:600px){h1{font-size:21px}main{padding:0 14px 48px}.kv th{width:9em;font-size:13px}.donuts{gap:16px;justify-content:space-between}.donut-item{width:88px}.brand-word{font-size:21px}.tagline{margin-left:32px}}
"""


# ---------------------------------------------------------------- 書き出し

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
    (SITE / "data").mkdir(parents=True)

    (SITE / "style.css").write_text(CSS, encoding="utf-8")
    (SITE / "index.html").write_text(index_page(companies, groups, fetched), encoding="utf-8")

    urls = ["/index.html"]
    for g, members in groups.items():
        path = f"/gyoukai/{GROUP_SLUG.get(g, g)}.html"
        (SITE / path.lstrip("/")).write_text(group_page(g, members, fetched), encoding="utf-8")
        urls.append(path)
    for c in companies:
        path = f"/kigyou/{c['slug']}.html"
        (SITE / path.lstrip("/")).write_text(company_page(c, groups[c["peer_group"]], fetched, newest), encoding="utf-8")
        urls.append(path)

    # クライアント側の絞り込み用（Layer 2 で使う）。サーバーは要らない。
    summary = [
        {
            "code": c["edinet_code"], "slug": c["slug"], "name": c["short"], "group": c["peer_group"],
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
