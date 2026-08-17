"""全社横断のランキングと条件別の一覧ページ。**generate.py から呼ばれる。**

なぜ要るのか:
    サイトには企業ページ1,549枚と業界ページ35枚しかなく、出口が「1社ずつ」と
    「業界1つずつ」の2種類しかなかった。就活生の問いのうち「自分の条件に合う会社はどれか」
    に答える経路が無い。ここはその逆引きを、DBもJavaScriptも使わずに静的ページで作る。

このサイトの原則をランキングで破らないための決めごと:

1. **平均年収のランキングから持株会社を外す。**
   持株会社の平均年間給与は本体（少数精鋭の管理部門）の数字であって、実際に入る
   事業会社の水準ではない。混ぜると上位が持株会社で埋まり、読者を誤解させる。
   外した事実と社数はページに明記し、隠さない。

2. **男性育休取得率のランキングは作らない。**
   原則方式と71条の6第2号方式が混在し、方式をまたいで順位を付けられないため。
   これは企業ページ・業界ページと同じ扱いである。

3. **営業利益の順位には会計基準を必ず併記する。**
   IFRSの営業利益は日本基準と定義が違う。列に基準を出し、注記でその旨を述べる。

4. **決算期の列を必ず置く。** 3月期・12月期・2月期・8月期が混在するので、
   「2026年の数字」という単一の見出しは嘘になる。

5. **値が無い会社は載せない。0で埋めない。** 母数（何社中何社に記載があったか）を書く。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
FIN_DIR = ROOT / "data" / "financials"
OVERSEAS_DIR = ROOT / "data" / "overseas"

TOP_N = 100


def _load_dir(d: Path) -> dict[str, dict]:
    """無ければ空で返す——このデータが無くてもサイトは成立する。"""
    if not d.exists():
        return {}
    out = {}
    for p in d.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out[rec["edinet_code"]] = rec
    return out


def load_financials() -> dict[str, dict]:
    return _load_dir(FIN_DIR)


def load_overseas() -> dict[str, dict]:
    """海外売上高比率。開示義務が単一セグメントの会社に及ばないので、載る会社は限られる。"""
    return _load_dir(OVERSEAS_DIR)


# ---------------------------------------------------------------- 指標の定義

class Metric:
    """1つの指標。ランキングにも条件別一覧にも同じ定義を使う。"""

    def __init__(
        self,
        slug: str,
        label: str,
        get: Callable[[dict, dict | None], Any],
        fmt_key: str,
        reverse: bool = True,
        exclude_holding: bool = False,
        unit_note: str = "",
        needs_financials: bool = False,
        show_standard: bool = False,
    ) -> None:
        self.slug = slug
        self.label = label
        self.get = get
        self.fmt_key = fmt_key
        self.reverse = reverse
        self.exclude_holding = exclude_holding
        self.unit_note = unit_note
        self.needs_financials = needs_financials
        self.show_standard = show_standard


def _salary(c, _f):
    return c["latest"]["reporting_company"]["average_annual_salary_yen"]


def _salary_growth(c, _f):
    """5年増減率。算定基準が変わった会社は対象外（既存の salary_trend_comparable を尊重）。"""
    if not c.get("salary_trend_comparable"):
        return None
    s = sorted(c["trend"]["average_annual_salary_yen"].items())
    if len(s) < 2 or not s[0][1]:
        return None
    return s[-1][1] / s[0][1] - 1


def _fin(key: str, per_capita: bool = False):
    def g(_c, f):
        if not f:
            return None
        box = f["latest"]["per_capita"] if per_capita else f["latest"]["values"]
        return box.get(key)
    return g


METRICS: list[Metric] = [
    Metric("nenshu", "平均年間給与", _salary, "man", exclude_holding=True,
           unit_note="提出会社（単体）の平均年間給与。賞与・時間外手当を含む。"),
    Metric("nenshu-nobi", "平均年間給与の5年増減率", _salary_growth, "pct_signed", exclude_holding=True,
           unit_note="最も古い期から直近の期への増減率。算定基準が変わった会社は対象から外している。"),
    Metric("kinzoku", "平均勤続年数",
           lambda c, f: c["latest"]["reporting_company"]["average_tenure_years"], "years",
           exclude_holding=True,
           unit_note="定着の代理指標。3年以内離職率は大手企業が公的に開示していないため掲載できない。"),
    Metric("josei-kanrishoku", "管理職に占める女性労働者の割合",
           lambda c, f: c["latest"]["diversity"]["female_manager_ratio"], "pct",
           unit_note="提出会社の値。持株会社は本体の値であり事業会社のものではない。"),
    Metric("danjo-chingin", "男女の賃金の差異（全労働者）",
           lambda c, f: c["latest"]["diversity"]["female_to_male_wage_ratio_all"], "pct",
           unit_note="男性の賃金を100としたときの女性の賃金の割合。100に近いほど差が小さい。"),
    Metric("jugyoin", "従業員数（連結）",
           lambda c, f: c["latest"]["employees"]["consolidated"], "nin"),
    Metric("hitori-uriage", "従業員1人当たり売上高", _fin("revenue_per_employee", True), "yen_oku",
           needs_financials=True,
           unit_note="連結の売上高を連結従業員数で割った値。商社のように仲介取引の多い業態は大きく出る。"),
    Metric("hitori-eigyorieki", "従業員1人当たり営業利益",
           _fin("operating_income_per_employee", True), "yen_man", needs_financials=True,
           show_standard=True,
           unit_note="連結の営業利益を連結従業員数で割った値。稼ぎに対する人数の効率を見る。"),
    # 海外売上高比率は財務ではなく data/overseas から来る（generate.py が c["overseas"] に載せる）。
    # 値の無い会社は _rows_for が自動で落とすので、専用の分岐は要らない。
    Metric("kaigai-uriage", "海外売上高比率",
           lambda c, f: (c.get("overseas") or {}).get("overseas_ratio"), "pct",
           unit_note="連結の売上のうち日本以外の地域が占める割合。有価証券報告書の「地域ごとの情報」から抽出し、"
                     "同じ有報のXBRLの売上高と一致した会社だけを載せている。単一セグメントの会社には開示義務が無く、"
                     "掲載できるのは主に大手に限られる。"),
    Metric("jikoshihon", "自己資本比率", _fin("equity_ratio"), "pct", needs_financials=True,
           show_standard=True,
           unit_note="総資産に占める自己資本の割合。銀行業は業態上この値が小さくなる。"),
]

METRIC_BY_SLUG = {m.slug: m for m in METRICS}


class Condition:
    """条件別の一覧ページ。ランキングと違い、上位N件ではなく該当する全社を出す。"""

    def __init__(self, slug: str, metric_slug: str, threshold: float, above: bool,
                 title: str, lead: str) -> None:
        self.slug = slug
        self.metric = METRIC_BY_SLUG[metric_slug]
        self.threshold = threshold
        self.above = above
        self.title = title
        self.lead = lead

    def matches(self, v) -> bool:
        if v is None:
            return False
        return v >= self.threshold if self.above else v <= self.threshold


CONDITIONS: list[Condition] = [
    Condition("nenshu-1000man", "nenshu", 10_000_000, True,
              "平均年収1,000万円以上の企業一覧",
              "有価証券報告書に記載された提出会社の平均年間給与が1,000万円以上の企業です。"
              "持株会社は本体の数字になるため除いています。"),
    Condition("nenshu-800man", "nenshu", 8_000_000, True,
              "平均年収800万円以上の企業一覧",
              "有価証券報告書に記載された提出会社の平均年間給与が800万円以上の企業です。"),
    Condition("josei-kanrishoku-20", "josei-kanrishoku", 0.20, True,
              "女性管理職比率20%以上の企業一覧",
              "管理職に占める女性労働者の割合が20%以上の企業です。提出会社の値を使っています。"),
    Condition("kinzoku-20nen", "kinzoku", 20.0, True,
              "平均勤続年数20年以上の企業一覧",
              "平均勤続年数が20年以上の企業です。3年以内離職率は大手企業が公的に開示していないため、"
              "定着の度合いはこの指標で代替しています。"),
    Condition("kaigai-uriage-50", "kaigai-uriage", 0.50, True,
              "海外売上高比率50%以上の企業一覧",
              "連結の売上の半分以上を日本以外で稼いでいる企業です。「海外で働けるか」を公的な一次情報で見る"
              "唯一の手がかりとして使えます。ただし海外売上があることと海外駐在の機会があることは別です。"),
    Condition("danjo-chingin-85", "danjo-chingin", 0.85, True,
              "男女の賃金の差異が小さい企業一覧（85%以上）",
              "男性の賃金を100としたときの女性の賃金が85以上の企業です。100に近いほど差が小さいことを意味します。"),
]


# ---------------------------------------------------------------- 出力

def _fmt_factory(g) -> dict[str, Callable]:
    """generate.py の書式関数を借りる。表記ゆれを1箇所に閉じ込めるため自前では書かない。"""
    return {
        "man": g.man,
        "pct": g.pct,
        "years": lambda v: g.dec1(v, "年"),
        "nin": lambda v: g.num(v, "人"),
        "pct_signed": lambda v: f'<span class="{"up" if v > 0 else "down"}">{v * 100:+.1f}%</span>',
        "yen_oku": lambda v: f"{v / 100_000_000:,.1f}億円",
        "yen_man": lambda v: f"{v / 10_000:,.0f}万円",
    }


def _rows_for(metric: Metric, companies: list[dict], fin: dict[str, dict]) -> list[tuple]:
    rows = []
    for c in companies:
        if metric.exclude_holding and c.get("is_holding"):
            continue
        f = fin.get(c["edinet_code"])
        if metric.needs_financials and not f:
            continue
        v = metric.get(c, f)
        if v is None:
            continue
        std = (f["latest"]["accounting_standard"] if f else "") if metric.show_standard else ""
        rows.append((c, v, std))
    rows.sort(key=lambda r: r[1], reverse=metric.reverse)
    return rows


def _table(g, rows: list[tuple], metric: Metric, fmts: dict, start_rank: int = 1) -> str:
    std_head = "<th>会計基準</th>" if metric.show_standard else ""
    body = []
    for i, (c, v, std) in enumerate(rows, start=start_rank):
        mark = ' <span class="hd" title="持株会社">持株</span>' if c.get("is_holding") else ""
        std_cell = f"<td>{g.e(std)}</td>" if metric.show_standard else ""
        body.append(
            f'<tr><td class="rank-no">{i}</td>'
            f'<th scope="row"><a href="../kigyou/{g.e(c["slug"])}.html">'
            f'{g.logo_img(c["icon"])}{g.e(c["short"])}</a>{mark}</th>'
            f'<td>{fmts[metric.fmt_key](v)}</td>'
            f'<td class="small">{g.e(c["peer_group"])}</td>'
            f'{std_cell}'
            f'<td class="small">{g.e(c["latest"]["source"]["period_end"][:7])}期</td></tr>'
        )
    return (
        '<div class="scroll"><table class="grid rank">'
        f'<thead><tr><th>順位</th><th>会社</th><th>{g.e(metric.label)}</th><th>業界</th>{std_head}<th>決算期</th></tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def _caveats(g, metric: Metric, total: int, listed: int, excluded_holding: int) -> str:
    parts = []
    if metric.unit_note:
        parts.append(f'<p class="caveat">{g.e(metric.unit_note)}</p>')
    parts.append(
        f'<p class="caveat">対象{total:,}社のうち、この項目が有価証券報告書に記載されていたのは'
        f'<b>{listed:,}社</b>です。記載の無い会社は載せていません（0や平均値で埋めていません）。</p>'
    )
    if excluded_holding:
        parts.append(
            f'<p class="caveat"><b>持株会社{excluded_holding:,}社をこの順位から外しています。</b>'
            "持株会社の平均年間給与・平均勤続年数は本体（管理部門）の数字であり、"
            "実際に勤務することになる事業会社の水準ではないためです。各社の数値は企業ページに載せています。</p>"
        )
    if metric.show_standard:
        parts.append(
            '<p class="caveat">IFRSの営業利益は日本基準と定義が異なります（非経常項目の扱い）。'
            "基準の異なる会社どうしを同じ順位表で見るときは、この列を必ず確認してください。</p>"
        )
    return "".join(parts)


def ranking_page(g, metric: Metric, companies: list[dict], fin: dict, fetched: str) -> str:
    rows = _rows_for(metric, companies, fin)
    excluded = sum(1 for c in companies if c.get("is_holding")) if metric.exclude_holding else 0
    shown = rows[:TOP_N]
    title = f"{metric.label}ランキング｜{len(rows):,}社を有価証券報告書だけで並べる"
    desc = (
        f"{metric.label}の高い順に{min(TOP_N, len(rows)):,}社。金融庁EDINETの有価証券報告書だけを出典に、"
        f"{len(companies):,}社から機械的に抽出しました。掲載企業から広告費を受け取っていません。"
    )
    body = f"""
<nav class="crumb"><a href="../index.html">トップ</a> › <a href="index.html">ランキング</a> › {g.e(metric.label)}</nav>

<h1>{g.e(metric.label)}ランキング</h1>
<p class="lead">
金融庁EDINETの有価証券報告書だけを出典に、{len(companies):,}社から{g.e(metric.label)}を機械的に抽出し、高い順に並べました。
上位{min(TOP_N, len(rows)):,}社を掲載しています。掲載企業から広告費は受け取っていません。
</p>
{_caveats(g, metric, len(companies), len(rows), excluded)}
{_table(g, shown, metric, _fmt_factory(g))}
<p class="caveat">数値の出典（EDINETの当該書類）は各企業ページに掲載しています。会社名をクリックしてください。</p>
<p class="caveat">データ取得日：{g.e(fetched)}</p>
"""
    return g.page(title, desc, body, depth=1, canonical=f"/ranking/{metric.slug}.html")


def condition_page(g, cond: Condition, companies: list[dict], fin: dict, fetched: str) -> str:
    metric = cond.metric
    rows = [r for r in _rows_for(metric, companies, fin) if cond.matches(r[1])]
    all_rows = _rows_for(metric, companies, fin)
    excluded = sum(1 for c in companies if c.get("is_holding")) if metric.exclude_holding else 0
    desc = (
        f"{cond.title}。金融庁EDINETの有価証券報告書だけを出典に{len(companies):,}社から抽出した"
        f"該当{len(rows):,}社を、{metric.label}の高い順に並べています。"
    )
    body = f"""
<nav class="crumb"><a href="../index.html">トップ</a> › <a href="index.html">ランキング</a> › {g.e(cond.title)}</nav>

<h1>{g.e(cond.title)}</h1>
<p class="lead">{g.e(cond.lead)}</p>
<p class="lead small">
{len(companies):,}社を調べ、{metric.label}が記載されていた{len(all_rows):,}社のうち
<b>{len(rows):,}社</b>が該当しました。{metric.label}の高い順です。
</p>
{_caveats(g, metric, len(companies), len(all_rows), excluded)}
{_table(g, rows, metric, _fmt_factory(g))}
<p class="caveat">数値の出典（EDINETの当該書類）は各企業ページに掲載しています。</p>
<p class="caveat">データ取得日：{g.e(fetched)}</p>
"""
    return g.page(cond.title, desc, body, depth=1, canonical=f"/ranking/{cond.slug}.html")


def hub_page(g, companies: list[dict], fin: dict, available: list[Metric],
             conditions: list[Condition], fetched: str) -> str:
    r_items = "".join(
        f'<li><a href="{m.slug}.html">{g.e(m.label)}ランキング</a>'
        f'<span class="small">{g.e(m.unit_note.split("。")[0])}</span></li>'
        for m in available
    )
    c_items = "".join(
        f'<li><a href="{c.slug}.html">{g.e(c.title)}</a></li>' for c in conditions
    )
    title = f"ランキングと条件別の企業一覧｜有価証券報告書{len(companies):,}社"
    desc = (
        f"平均年収・平均勤続年数・女性管理職比率・男女の賃金の差異・1人当たり売上高を、"
        f"有価証券報告書だけを出典に{len(companies):,}社で並べたランキングと、条件別の企業一覧。"
    )
    body = f"""
<nav class="crumb"><a href="../index.html">トップ</a> › ランキング</nav>

<h1>ランキングと条件別の企業一覧</h1>
<p class="lead">
{len(companies):,}社の有価証券報告書から機械的に抽出した数値で並べています。
掲載企業から広告費を受け取っていないので、企業に不利な順位もそのまま出します。
</p>

<section>
<h2>指標で並べる</h2>
<ul class="linklist">{r_items}</ul>
</section>

<section>
<h2>条件で絞る</h2>
<ul class="linklist">{c_items}</ul>
</section>

<section>
<h2>ランキングにしていない項目</h2>
<p class="caveat">
<b>男性育休取得率</b>は順位を付けていません。原則方式と育児介護休業法施行規則71条の6第2号方式
（育児目的休暇を含むため高く出る）の2方式が混在しており、方式をまたいで比べられないためです。
各社の値と方式は企業ページと業界ページに掲載しています。
</p>
<p class="caveat">
<b>3年以内離職率</b>は掲載していません。大手企業は公的に開示しておらず、
厚生労働省「しょくばらぼ」で数値を埋めているのは全掲載148,228社のうち1.8%（中小企業層）だけでした。
推測で埋めるくらいなら載せないという方針です。
</p>
</section>
<p class="caveat">データ取得日：{g.e(fetched)}</p>
"""
    return g.page(title, desc, body, depth=1, canonical="/ranking/index.html")


def build(g, companies: list[dict], fetched: str, site: Path) -> list[str]:
    """ページを書き、**実際に書けたURLだけ**を返す。sitemap に嘘を載せないため。"""
    fin = load_financials()
    out_dir = site / "ranking"
    out_dir.mkdir(parents=True, exist_ok=True)

    available = [m for m in METRICS if not m.needs_financials or fin]
    urls: list[str] = []

    for m in available:
        if not _rows_for(m, companies, fin):
            continue
        (out_dir / f"{m.slug}.html").write_text(
            ranking_page(g, m, companies, fin, fetched), encoding="utf-8")
        urls.append(f"/ranking/{m.slug}.html")

    conds = [c for c in CONDITIONS if not c.metric.needs_financials or fin]
    written_conds = []
    for c in conds:
        rows = [r for r in _rows_for(c.metric, companies, fin) if c.matches(r[1])]
        if not rows:
            continue
        (out_dir / f"{c.slug}.html").write_text(
            condition_page(g, c, companies, fin, fetched), encoding="utf-8")
        urls.append(f"/ranking/{c.slug}.html")
        written_conds.append(c)

    (out_dir / "index.html").write_text(
        hub_page(g, companies, fin, available, written_conds, fetched), encoding="utf-8")
    urls.insert(0, "/ranking/index.html")

    print(f"  ランキング {len(urls)}ページ（財務データ {len(fin)}社ぶん）")
    return urls
