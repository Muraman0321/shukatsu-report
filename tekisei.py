"""学部・興味から業界を絞る診断。**shindan.py（就活の軸診断）とは別物**。

shindan.py は「給与・定着・海外比率など**有報の数値**で会社を並べ替える」診断だった。
これはその逆で、「**学部・専攻の系統**」と「**やりたい仕事のタイプ**」という、
有報には出てこない属性から、まず**業界**を絞り込む。全35業界・1,549社を対象にする。

## この診断が「事実」と「一般的な傾向」を混同しないための設計
このサイトの唯一の資産は「数字で嘘をつかない」という信頼である。だが学部と業界の対応は
有報のような一次情報が無く、**採用の実務慣行にもとづく一般的な傾向**でしかない。
そこで2種類の情報をはっきり分けて出す。

1. **業界レベルの一般的な傾向**（`GROUP_TAGS`）。「化学は理系（薬学・農学・生命科学系）を
   多く採る傾向がある」という、就活情報で広く言われる水準の対応づけ。表を画面に出し、
   「これは事実の一覧ではなく一般的な傾向です」と明記する。個々の会社の採否とは無関係。
2. **会社ごとの事実**（`data/saiyo/{code}.json` があれば）。採用ページに実際に
   「学部・学科不問」などと書かれていた会社だけ、その文言をそのまま引用し出典を付ける。
   1と2が両方出るときは2を主役にする——**一般論より一次情報を信じる**、という
   このサイト全体の原則をここでも守る。

## なぜ「性格診断」ではなく「絞り込み」と呼ぶか
shindan.py と同じ理由。学部やjob_typeの選択だけで「あなたに向いている会社」を
言い当てることはできない。やっているのは「あなたが選んだ条件に一致する業界・会社を
機械的に一覧すること」だけである。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

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

# 業界（peer_group）ごとの一般的な傾向。**採用実態の断定ではない。**
# 対象学部は「その業界の求人で多く見る系統」、仕事タイプはこのサイトの6分類との対応づけ。
# 判断根拠を画面にそのまま出すので、恣意的に見えないよう業種の実態（技術系か非技術系か、
# 研究開発の有無、労働集約か資本集約か）にもとづいて機械的につけている。
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


def build_data(companies: list[dict]) -> dict:
    items = []
    for c in companies:
        g = c["peer_group"]
        tags = GROUP_TAGS.get(g, {"faculty": ["toranai"], "job": []})
        saiyo = c.get("saiyo") or {}
        items.append({
            "s": c["slug"], "n": c["short"], "g": g, "h": 1 if c.get("is_holding") else 0,
            "emp": c["latest"]["employees"]["consolidated"],
            "overseas": (c.get("overseas") or {}).get("overseas_ratio"),
            "faculty": tags["faculty"], "job": tags["job"],
            # 会社ごとの事実（採用ページに実際に書かれていた文言）。無ければ null
            "target_faculty_fact": saiyo.get("target_faculty") or None,
        })
    return {
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
    title = f"学部・興味から業界を絞る診断｜文系/理系・やりたい仕事から{n:,}社を絞り込む"
    desc = (
        f"学部の系統とやりたい仕事のタイプを選ぶと、一般的な傾向にもとづいて業界を絞り込みます。"
        f"採用ページに対象学部が明記されている会社は、その原文も一緒に出します。{n:,}社が対象です。"
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
<nav class="crumb"><a href="index.html">トップ</a> › 学部・興味から絞る診断</nav>

<h1>学部・興味から業界を絞る診断</h1>
<p class="lead">
<b>学部の系統</b>と<b>やりたい仕事のタイプ</b>を選ぶと、一般的な傾向にもとづいて業界・会社を絞り込みます。
</p>
<p class="lead small">
これは性格診断でも適性検査でもありません。<b>学部と業界の対応づけは、就活で広く言われる一般的な傾向</b>であり、
個々の会社の採用実態を保証するものではありません。採用ページに「学部・学科不問」などと実際に書かれている会社は、
その原文をそのまま引用して出典付きで出します。一般論より、会社が公式に書いた言葉を優先してください。
</p>

<div id="tekisei" class="shindan tekisei" data-state="pick">
  <section class="tk-pick">
    <h2>1. 学部の系統</h2>
    <div class="tk-optgrid tk-faculty">{faculty_html}</div>
    <h2>2. やりたい仕事のタイプ（最大2つ）</h2>
    <div class="tk-optgrid tk-job">{job_html}</div>
    <button class="tk-go cta-btn" type="button" disabled>この条件で絞り込む</button>
  </section>

  <section class="tk-result" hidden>
    <h2>条件に合う業界</h2>
    <p class="tk-summary"></p>
    <div class="scroll"><table class="grid tk-groups">
      <thead><tr><th>業界</th><th>該当社数</th><th>対象学部の傾向</th><th>やりたい仕事との一致</th></tr></thead>
      <tbody></tbody>
    </table></div>

    <h2>会社一覧</h2>
    <div class="tk-filters">
      <label><input type="checkbox" class="tk-nohd" checked> 持株会社を除く</label>
      <label><input type="checkbox" class="tk-globalsort"> 海外売上高比率が高い順に並べる</label>
    </div>
    <div class="scroll"><table class="grid tk-table">
      <thead><tr><th>会社</th><th>業界</th><th>規模（連結従業員数）</th><th>対象学部</th></tr></thead>
      <tbody></tbody>
    </table></div>
    <p class="caveat">
      「対象学部」欄で<b>出典が付いているもの</b>は、その会社の採用ページに実際に書かれていた文言です。
      出典が無いものは、業界レベルの一般的な傾向から機械的に表示しているだけで、その会社の事実ではありません。
    </p>
    <div class="sd-actions"><button class="tk-retry" type="button">条件を選び直す</button></div>
  </section>
</div>

<section>
<h2>業界ごとの一般的な傾向（この診断が使っている対応表）</h2>
<p class="lead small">恣意的な絞り込みに見えないよう、判断の根拠をそのまま出します。技術系採用の有無・研究開発の比重・
資本集約か労働集約かなど業種の実態にもとづく機械的な対応づけで、個々の企業の採用方針を示すものではありません。</p>
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
    return g.page(title, desc, body, depth=0, canonical="/tekisei.html")


def build(g, companies: list[dict], fetched: str, site: Path) -> list[str]:
    data = build_data(companies)
    (site / "data").mkdir(parents=True, exist_ok=True)
    (site / "data" / "tekisei.json").write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (site / "tekisei.html").write_text(page(g, companies, fetched), encoding="utf-8")
    have_fact = sum(1 for c in data["companies"] if c["target_faculty_fact"])
    print(f"  学部・興味診断 1件（対象学部の一次情報あり {have_fact}社）")
    return ["/tekisei.html"]
