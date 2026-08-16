"""就活の軸診断。**サーバーもAPIも使わず、静的JSONとブラウザだけで動く。**

答えるのは2つの問い:
    「自分が就活で大事にしていることは何か」 → 12問の二者択一から重みを出す
    「自分に合っている企業はどこか」       → その重みで1,549社を並べ替える

## なぜ二者択一12問なのか
「年収は重要ですか」と尋ねると誰でも「重要」と答える。**何かを選べば何かを捨てる**形にしないと
優先順位は出てこない。8つの軸が3回ずつ現れる12問にして、選ばれた回数を重みにする。

## なぜ性格診断にしないのか
このサイトが持っているのは有価証券報告書の数値だけで、人の性格は測れない。
**これは診断の顔をした絞り込みである**と画面にも書く。当たるように見せる占いを作ると、
数字で嘘をつかないという原則と正面から衝突する。

## 欠損の扱い
ある軸の数値が無い会社は、**その軸を平均で埋めない**。使えた軸だけで計算し、
何軸で評価したかを画面に出す。0で埋めると非公表の会社が不当に下がり、
平均で埋めると非公表が有利になる。どちらも嘘になる。

## 順位付けをしない軸
男性育休取得率は原則方式と71条の6第2号方式が混在し、方式をまたいで比べられないので
診断の軸に入れない。企業ページ・業界ページ・ランキングと同じ扱いである。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent


class Axis:
    def __init__(self, key: str, label: str, short: str, get: Callable[[dict], Any],
                 unit_note: str, higher_is: str) -> None:
        self.key, self.label, self.short = key, label, short
        self.get, self.unit_note, self.higher_is = get, unit_note, higher_is


def _salary_growth(c: dict):
    if not c.get("salary_trend_comparable"):
        return None
    s = sorted(c["trend"]["average_annual_salary_yen"].items())
    if len(s) < 2 or not s[0][1]:
        return None
    return s[-1][1] / s[0][1] - 1


AXES: list[Axis] = [
    Axis("pay", "給与の高さ", "給与",
         lambda c: c["latest"]["reporting_company"]["average_annual_salary_yen"],
         "提出会社の平均年間給与。賞与・時間外手当を含む", "高いほど上位"),
    Axis("growth", "給与の伸び", "伸び", _salary_growth,
         "直近5期の平均年間給与の増減率。算定基準が変わった会社は対象外", "伸びているほど上位"),
    Axis("tenure", "長く働けるか", "定着",
         lambda c: c["latest"]["reporting_company"]["average_tenure_years"],
         "平均勤続年数。3年以内離職率は大手が開示していないための代理指標", "長いほど上位"),
    Axis("women", "女性の管理職登用", "女性登用",
         lambda c: c["latest"]["diversity"]["female_manager_ratio"],
         "管理職に占める女性労働者の割合", "高いほど上位"),
    Axis("wagegap", "男女の賃金差の小ささ", "賃金差",
         lambda c: c["latest"]["diversity"]["female_to_male_wage_ratio_all"],
         "男性を100としたときの女性の賃金の割合。100に近いほど差が小さい", "100に近いほど上位"),
    Axis("global", "海外で働く可能性", "海外",
         lambda c: (c.get("overseas") or {}).get("overseas_ratio"),
         "連結の売上のうち日本以外が占める割合。売上の出どころであって勤務地の内訳ではない",
         "高いほど上位"),
    Axis("scale", "会社の規模", "規模",
         lambda c: c["latest"]["employees"]["consolidated"],
         "連結従業員数", "多いほど上位"),
    Axis("stability", "財務の堅さ", "財務",
         lambda c: ((c.get("fin") or {}).get("latest") or {}).get("values", {}).get("equity_ratio"),
         "自己資本比率。銀行業は業態上この値が小さくなる", "高いほど上位"),
]

# 12問。8軸がちょうど3回ずつ現れる（12×2÷8＝3）
QUESTIONS = [
    ("pay", "給料が高い会社", "tenure", "長く働き続けられる会社"),
    ("global", "海外と関わる仕事がある会社", "scale", "規模が大きく名前の通った会社"),
    ("women", "女性が管理職になっている会社", "stability", "財務が堅く潰れにくい会社"),
    ("growth", "給料がこれから伸びている会社", "wagegap", "男女の賃金差が小さい会社"),
    ("pay", "いまの給与水準が高い会社", "global", "海外売上の比率が高い会社"),
    ("tenure", "社員が辞めずに長く勤める会社", "stability", "自己資本が厚い会社"),
    ("scale", "従業員が多い大きな会社", "growth", "給与が年々上がっている会社"),
    ("wagegap", "男女で賃金の差が小さい会社", "women", "女性の管理職比率が高い会社"),
    ("pay", "同期より高い給料をもらえる会社", "women", "女性が昇進している会社"),
    ("global", "売上の多くを海外で立てている会社", "wagegap", "男女の待遇が近い会社"),
    ("tenure", "腰を据えて働ける会社", "growth", "変化が速く給与も動く会社"),
    ("scale", "組織が大きい会社", "stability", "自己資本比率が高い会社"),
]


def percentiles(values: list[float | None]) -> list[float | None]:
    """順位を0〜1に直す。値の分布が軸ごとに桁違い（人数と比率）なので、生値では足せない。

    同順位は平均順位を与える。欠損は None のまま返し、**平均で埋めない**。
    """
    idx = [(v, i) for i, v in enumerate(values) if v is not None]
    idx.sort(key=lambda x: x[0])
    out: list[float | None] = [None] * len(values)
    n = len(idx)
    if n <= 1:
        for _, i in idx:
            out[i] = 0.5
        return out
    j = 0
    while j < n:
        k = j
        while k + 1 < n and idx[k + 1][0] == idx[j][0]:
            k += 1
        rank = (j + k) / 2.0
        for t in range(j, k + 1):
            out[idx[t][1]] = round(rank / (n - 1), 4)
        j = k + 1
    return out


def build_data(companies: list[dict]) -> dict:
    cols = {}
    for ax in AXES:
        raw = [ax.get(c) for c in companies]
        cols[ax.key] = percentiles(raw)
    items = []
    for i, c in enumerate(companies):
        scores = {ax.key: cols[ax.key][i] for ax in AXES if cols[ax.key][i] is not None}
        items.append({
            "s": c["slug"],
            "n": c["short"],
            "g": c["peer_group"],
            "h": 1 if c.get("is_holding") else 0,
            "p": scores,
            # 表示用の実数値。診断結果に「なぜ上位なのか」を出すために持たせる
            "v": {
                "pay": c["latest"]["reporting_company"]["average_annual_salary_yen"],
                "tenure": c["latest"]["reporting_company"]["average_tenure_years"],
                "women": c["latest"]["diversity"]["female_manager_ratio"],
                "wagegap": c["latest"]["diversity"]["female_to_male_wage_ratio_all"],
                "scale": c["latest"]["employees"]["consolidated"],
                "global": (c.get("overseas") or {}).get("overseas_ratio"),
                "stability": ((c.get("fin") or {}).get("latest") or {}).get("values", {}).get("equity_ratio"),
                "growth": _salary_growth(c),
            },
        })
    return {
        "axes": [{"key": a.key, "label": a.label, "short": a.short,
                  "note": a.unit_note, "higher": a.higher_is} for a in AXES],
        "questions": [{"a": q[0], "at": q[1], "b": q[2], "bt": q[3]} for q in QUESTIONS],
        "companies": items,
    }


def page(g, companies: list[dict], fetched: str) -> str:
    n = len(companies)
    title = f"就活の軸診断｜12問であなたが大事にしていることを出し、{n:,}社から合う会社を並べる"
    desc = (
        f"12問の二者択一に答えると、就活で自分が何を優先しているかが8つの軸で出ます。"
        f"その重みで有価証券報告書の数値だけを使い、{n:,}社を並べ替えます。性格診断ではありません。"
    )
    body = f"""
<nav class="crumb"><a href="index.html">トップ</a> › 就活の軸診断</nav>

<h1>就活の軸診断</h1>
<p class="lead">
12問の二者択一に答えるだけで、<b>自分が就活で何を優先しているのか</b>が8つの軸で出ます。
そのうえで、その重みを使って{n:,}社を並べ替えます。
</p>
<p class="lead small">
これは性格診断ではありません。<b>このサイトが持っているのは有価証券報告書の数値だけ</b>で、
人の性格や社風は測れません。やっているのは「あなたが選んだ重みで公的な数値を並べ替える」ことだけです。
当たる占いに見せるより、何をしているかが分かるほうがいいと考えています。
</p>

<div id="shindan" class="shindan" data-state="intro">

  <section class="sd-intro">
    <p class="sd-meta">全12問・所要1分ほど／回答はブラウザの中だけで処理され、どこにも送信されません</p>
    <button class="sd-start cta-btn" type="button">診断をはじめる</button>
  </section>

  <section class="sd-quiz" hidden>
    <div class="sd-progress"><span class="sd-bar"></span></div>
    <p class="sd-count"><b class="sd-no">1</b> / 12</p>
    <p class="sd-lead">どちらにより惹かれますか</p>
    <div class="sd-choices">
      <button class="sd-choice" data-side="a" type="button"><span></span></button>
      <span class="sd-vs">か</span>
      <button class="sd-choice" data-side="b" type="button"><span></span></button>
    </div>
    <button class="sd-back" type="button" hidden>← 前の問いに戻る</button>
  </section>

  <section class="sd-result" hidden>
    <h2>あなたが就活で大事にしていること</h2>
    <div class="sd-axes"></div>
    <p class="caveat sd-axes-note"></p>

    <h2>その重みで並べた{n:,}社</h2>
    <div class="sd-filters">
      <label><input type="checkbox" class="sd-nohd" checked> 持株会社を除く</label>
      <label>業界 <select class="sd-group"><option value="">すべて</option></select></label>
    </div>
    <div class="scroll"><table class="grid rank sd-table">
      <thead><tr><th>順位</th><th>会社</th><th>適合度</th><th>上位の理由</th><th>業界</th></tr></thead>
      <tbody></tbody>
    </table></div>
    <p class="caveat">
      適合度は「あなたが選んだ軸での順位（0〜100）」を重みで加重平均したものです。<b>会社の優劣ではありません。</b>
      数値が有価証券報告書に無い軸はその会社の計算から外しています（0や平均で埋めていません）。
      評価に使えた軸の数を各行に出しています。
    </p>
    <div class="sd-actions">
      <button class="sd-retry" type="button">やり直す</button>
    </div>
  </section>
</div>

<section>
<h2>この診断が使っている8つの軸</h2>
<div class="scroll"><table class="grid">
<thead><tr><th>軸</th><th>使っている数値</th><th>並べ方</th></tr></thead>
<tbody>{"".join(
    f'<tr><th scope="row">{g.e(a.label)}</th><td>{g.e(a.unit_note)}</td><td>{g.e(a.higher_is)}</td></tr>'
    for a in AXES)}</tbody></table></div>
<p class="caveat">
<b>男性育休取得率は軸に入れていません。</b>原則方式と育児介護休業法施行規則71条の6第2号方式
（育児目的休暇を含むため高く出る）の2方式が混在しており、方式をまたいで比べられないためです。
<b>3年以内離職率も入れていません。</b>大手企業が公的に開示しておらず、代わりに平均勤続年数を使っています。
</p>
<p class="caveat">データ取得日：{g.e(fetched)}</p>
</section>
"""
    return g.page(title, desc, body, depth=0, canonical="/shindan.html")


def build(g, companies: list[dict], fetched: str, site: Path) -> list[str]:
    data = build_data(companies)
    (site / "data").mkdir(parents=True, exist_ok=True)
    (site / "data" / "shindan.json").write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (site / "shindan.html").write_text(page(g, companies, fetched), encoding="utf-8")
    covered = {a["key"]: sum(1 for c in data["companies"] if a["key"] in c["p"]) for a in data["axes"]}
    print(f"  診断ページ 1件（軸ごとの母数 {covered}）")
    return ["/shindan.html"]
