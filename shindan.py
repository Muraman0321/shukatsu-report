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

## 学部・仕事タイプのプロフィール（旧 tekisei.py を統合）
12問より前に「学部の系統」と「やりたい仕事のタイプ」を選ぶ。これはこのサイトが持つ2種類の
情報をはっきり分けて使う。

1. **業界レベルの一般的な傾向**（`GROUP_TAGS`）。「化学は理系（薬学・農学・生命科学系）を
   多く採る傾向がある」という、就活で広く言われる水準の対応づけ。参考情報として画面に出すが、
   会社の除外には使わない。
2. **会社ごとの事実**（`data/saiyo/{code}.json` の`target_faculty`。採用ページに実際に
   「学部・学科不問」などと書かれていた文言と、それをGeminiで構造化した`target_faculty_eligibility`）。
   **これがあり、かつ選んだ学部が明確に対象外と読み取れる会社だけを結果から除外する。**
   構造化がまだの会社・そもそも記載が無い会社は、対象外と決めつけず結果に残す
   （全学部可の会社が多いはずで、無い＝不可ではない）。

## 海外の軸を「海外駐在」寄りに言い換えた理由
有報には海外駐在者数のような開示項目が無い。使えるのは連結売上の海外比率だけなので、
軸のキー・データソースは変えず、ラベルと注記だけを「駐在の可能性」の目安だと分かるように書き換えた。
対象は120/1,549社のみで、勤務地の内訳ではなく売上の出どころだという注記は残す。
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
    Axis("global", "海外駐在の可能性", "海外駐在",
         lambda c: (c.get("overseas") or {}).get("overseas_ratio"),
         "連結売上のうち日本以外が占める割合を目安に使っている。海外駐在者数の開示は無いための代理指標で、"
         "勤務地の内訳ではなく売上の出どころ。対象は120/1,549社のみ",
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


FACULTY = [
    ("bunkei", "文系", "法・経済・商・経営・文学部など"),
    ("rikei_kougaku", "理系（工学・情報系）", "工学・情報・機械・電気・電子など"),
    ("rikei_seimei", "理系（生命科学系）", "薬学・農学・理学・生命科学・化学など"),
    ("toranai", "決めていない／学部はあまり関係ない", "文理不問で探したい"),
]

JOB_TYPE = [
    ("global", "海外・グローバルに関わる仕事", "海外売上高比率が高い会社を優先して並べる"),
    ("monodukuri", "ものづくり・技術で新しい価値をつくる仕事", ""),
    ("okane", "数字・お金を扱う仕事（金融・財務）", ""),
    ("hito", "人と接する・サービスをつくる仕事", ""),
    ("kenkyu", "研究・専門性を極める仕事", ""),
    ("infra", "社会の基盤（インフラ・物流・エネルギー）を支える仕事", ""),
]

# 業界（peer_group）ごとの一般的な傾向。**採用実態の断定ではない。**参考情報として画面に出すだけで、
# 会社の除外には使わない（除外は会社ごとの事実＝target_faculty_eligibility だけで行う）。
GROUP_TAGS: dict[str, dict[str, list[str]]] = {
    "総合商社":     {"faculty": ["bunkei", "rikei_kougaku", "rikei_seimei", "toranai"], "job": ["global", "hito"]},
    "銀行業":       {"faculty": ["bunkei", "toranai"], "job": ["okane"]},
    "保険業":       {"faculty": ["bunkei", "toranai"], "job": ["okane", "hito"]},
    "証券":         {"faculty": ["bunkei", "toranai"], "job": ["okane"]},
    "その他金融":   {"faculty": ["bunkei", "toranai"], "job": ["okane"]},
    "電気機器":     {"faculty": ["rikei_kougaku", "bunkei"], "job": ["monodukuri", "global"]},
    "精密機器":     {"faculty": ["rikei_kougaku"], "job": ["monodukuri", "kenkyu"]},
    "機械":         {"faculty": ["rikei_kougaku"], "job": ["monodukuri", "infra"]},
    "輸送用機器":   {"faculty": ["rikei_kougaku", "bunkei"], "job": ["monodukuri", "global"]},
    "金属製品":     {"faculty": ["rikei_kougaku"], "job": ["monodukuri"]},
    "鉄鋼":         {"faculty": ["rikei_kougaku"], "job": ["monodukuri", "infra"]},
    "非鉄金属":     {"faculty": ["rikei_kougaku"], "job": ["monodukuri", "infra"]},
    "ガラス・土石": {"faculty": ["rikei_kougaku", "rikei_seimei"], "job": ["monodukuri"]},
    "ゴム製品":     {"faculty": ["rikei_kougaku"], "job": ["monodukuri"]},
    "パルプ・紙":   {"faculty": ["rikei_kougaku", "rikei_seimei"], "job": ["monodukuri"]},
    "繊維製品":     {"faculty": ["rikei_seimei", "bunkei"], "job": ["monodukuri", "hito"]},
    "化学":         {"faculty": ["rikei_seimei", "rikei_kougaku"], "job": ["kenkyu", "monodukuri"]},
    "医薬品":       {"faculty": ["rikei_seimei"], "job": ["kenkyu", "hito"]},
    "食料品":       {"faculty": ["bunkei", "rikei_seimei"], "job": ["hito", "monodukuri"]},
    "水産農林":     {"faculty": ["rikei_seimei", "bunkei"], "job": ["hito", "infra"]},
    "石油・石炭":   {"faculty": ["rikei_kougaku", "bunkei"], "job": ["infra", "global"]},
    "鉱業":         {"faculty": ["rikei_kougaku"], "job": ["infra", "global"]},
    "ガス業":       {"faculty": ["rikei_kougaku", "bunkei"], "job": ["infra"]},
    "電力":         {"faculty": ["rikei_kougaku", "bunkei"], "job": ["infra"]},
    "建設業":       {"faculty": ["rikei_kougaku", "bunkei"], "job": ["monodukuri", "infra"]},
    "不動産業":     {"faculty": ["bunkei", "toranai"], "job": ["hito", "infra"]},
    "陸運":         {"faculty": ["bunkei", "toranai"], "job": ["infra", "hito"]},
    "海運":         {"faculty": ["bunkei", "rikei_kougaku"], "job": ["global", "infra"]},
    "空運":         {"faculty": ["bunkei", "toranai"], "job": ["global", "hito"]},
    "倉庫運輸":     {"faculty": ["bunkei", "toranai"], "job": ["infra", "global"]},
    "卸売業":       {"faculty": ["bunkei", "toranai"], "job": ["hito", "global"]},
    "小売":         {"faculty": ["bunkei", "toranai"], "job": ["hito"]},
    "情報・通信業": {"faculty": ["rikei_kougaku", "bunkei", "toranai"], "job": ["monodukuri", "kenkyu"]},
    "サービス業":   {"faculty": ["bunkei", "toranai"], "job": ["hito"]},
    "その他製品":   {"faculty": ["bunkei", "rikei_kougaku"], "job": ["monodukuri", "hito"]},
}

FACULTY_LABEL = {k: lbl for k, lbl, _ in FACULTY}
JOB_LABEL = {k: lbl for k, lbl, _ in JOB_TYPE}


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
        g = c["peer_group"]
        tags = GROUP_TAGS.get(g, {"faculty": ["toranai"], "job": []})
        saiyo = c.get("saiyo") or {}
        items.append({
            "s": c["slug"],
            "n": c["short"],
            "g": g,
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
            # 業界レベルの一般的な傾向。参考情報であり除外には使わない
            "faculty": tags["faculty"],
            "job": tags["job"],
            # 会社ごとの事実（採用ページに実際に書かれていた文言）。無ければ null
            "target_faculty_fact": saiyo.get("target_faculty") or None,
            # target_faculty_fact をGeminiで構造化したもの。{"bunkei": true/false/null, ...}。
            # 未構造化・未記載の会社は null（＝除外しない）
            "target_faculty_elig": saiyo.get("target_faculty_eligibility") or None,
        })
    return {
        "axes": [{"key": a.key, "label": a.label, "short": a.short,
                  "note": a.unit_note, "higher": a.higher_is} for a in AXES],
        "questions": [{"a": q[0], "at": q[1], "b": q[2], "bt": q[3]} for q in QUESTIONS],
        "faculty_options": [{"key": k, "label": lbl, "note": note} for k, lbl, note in FACULTY],
        "job_options": [{"key": k, "label": lbl, "note": note} for k, lbl, note in JOB_TYPE],
        "group_tags": [
            {"group": g, "faculty": [FACULTY_LABEL[f] for f in t["faculty"]],
             "job": [JOB_LABEL[j] for j in t["job"]]}
            for g, t in sorted(GROUP_TAGS.items())
        ],
        "companies": items,
    }


def page(g, companies: list[dict], fetched: str) -> str:
    n = len(companies)
    title = f"就活の軸診断｜学部・仕事タイプと12問から、あなたに合う会社を{n:,}社から並べる"
    desc = (
        f"学部の系統とやりたい仕事のタイプを選び、12問の二者択一に答えると、就活で自分が何を優先しているかが"
        f"8つの軸で出ます。その重みで有価証券報告書の数値だけを使い、{n:,}社を並べ替えます。性格診断ではありません。"
    )
    faculty_html = "".join(
        f'<button class="tk-opt" type="button" data-kind="faculty" data-key="{g.e(k)}">'
        f'<b>{g.e(lbl)}</b>{f"<small>{g.e(note)}</small>" if note else ""}</button>'
        for k, lbl, note in FACULTY
    )
    job_html = "".join(
        f'<button class="tk-opt" type="button" data-kind="job" data-key="{g.e(k)}">'
        f'<b>{g.e(lbl)}</b>{f"<small>{g.e(note)}</small>" if note else ""}</button>'
        for k, lbl, note in JOB_TYPE
    )
    body = f"""
<nav class="crumb"><a href="index.html">トップ</a> › 就活の軸診断</nav>

<h1>就活の軸診断</h1>
<p class="lead">
<b>学部の系統・やりたい仕事のタイプ</b>を選び、<b>12問の二者択一</b>に答えるだけで、
自分が就活で何を優先しているのかが8つの軸で出ます。そのうえで、その重みを使って{n:,}社を並べ替えます。
</p>
<p class="lead small">
これは性格診断ではありません。<b>このサイトが持っているのは有価証券報告書の数値と、採用ページの原文だけ</b>で、
人の性格や社風は測れません。やっているのは「あなたが選んだ条件・重みで公的な数値を絞り込み、並べ替える」ことだけです。
当たる占いに見せるより、何をしているかが分かるほうがいいと考えています。
</p>

<div id="shindan" class="shindan" data-state="intro">

  <section class="sd-intro">
    <p class="sd-meta">プロフィール選択＋全12問・所要2分ほど／回答はブラウザの中だけで処理され、どこにも送信されません</p>
    <button class="sd-start cta-btn" type="button">診断をはじめる</button>
  </section>

  <section class="sd-profile" hidden>
    <h2>1. あなたについて</h2>
    <p class="lead small">
    選ぶと、結果の会社一覧から<b>採用ページに実際に学部制限が書かれている会社のうち、明確に対象外の会社</b>を除きます。
    記載が無い・まだ判定できていない会社は対象外と決めつけず、そのまま一覧に残します。
    </p>
    <h3>学部の系統</h3>
    <div class="tk-optgrid sd-faculty">{faculty_html}</div>
    <h3>やりたい仕事のタイプ（任意・最大2つ）</h3>
    <div class="tk-optgrid sd-jobtype">{job_html}</div>
    <button class="sd-profile-go tk-go cta-btn" type="button" disabled>次へ（12問の質問に進む）</button>
  </section>

  <section class="sd-quiz" hidden>
    <h2>2. 12問の質問</h2>
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
    <p class="caveat sd-profile-note"></p>

    <h2>その条件で並べた会社</h2>
    <div class="sd-filters">
      <label><input type="checkbox" class="sd-nohd" checked> 持株会社を除く</label>
      <label>業界 <select class="sd-group"><option value="">すべて</option></select></label>
    </div>
    <div class="scroll"><table class="grid rank sd-table">
      <thead><tr><th>順位</th><th>会社</th><th>適合度</th><th>上位の理由</th><th>学部</th><th>業界</th></tr></thead>
      <tbody></tbody>
    </table></div>
    <p class="caveat">
      適合度は「あなたが選んだ軸での順位（0〜100）」を重みで加重平均したものです。<b>会社の優劣ではありません。</b>
      数値が有価証券報告書に無い軸はその会社の計算から外しています（0や平均で埋めていません）。
      評価に使えた軸の数を各行に出しています。
    </p>
    <p class="caveat">
      「学部」欄で<b>出典付きのもの</b>は採用ページに実際に書かれていた文言です。出典が無いものは業界レベルの
      一般的な傾向、または記載そのものが無いことを示すだけで、その会社の事実ではありません。
    </p>
    <div class="sd-actions">
      <button class="sd-retry" type="button">12問だけやり直す</button>
      <button class="sd-profile-retry" type="button">学部・仕事タイプを選び直す</button>
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

<h2>業界ごとの学部・仕事タイプの一般的な傾向</h2>
<p class="lead small">恣意的な絞り込みに見えないよう、判断の根拠をそのまま出します。技術系採用の有無・研究開発の比重・
資本集約か労働集約かなど業種の実態にもとづく機械的な対応づけで、個々の企業の採用方針を示すものではなく、
結果の会社一覧を除外するためにも使っていません（除外は会社ごとの事実がある場合だけです）。</p>
<div class="scroll"><table class="grid">
<thead><tr><th>業界</th><th>対象学部の傾向</th><th>やりたい仕事との一致</th></tr></thead>
<tbody>{"".join(
    f'<tr><th scope="row">{g.e(grp)}</th>'
    f'<td>{g.e("・".join(FACULTY_LABEL[f] for f in t["faculty"]))}</td>'
    f'<td>{g.e("・".join(JOB_LABEL[j] for j in t["job"])) or "―"}</td></tr>'
    for grp, t in sorted(GROUP_TAGS.items()))}</tbody></table></div>
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
