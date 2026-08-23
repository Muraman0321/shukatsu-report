"""data/koumu/*.json から「官公庁・政府系」カテゴリのページを作る。

省庁・独立行政法人・政府系金融機関（+日本銀行）は EDINET の有価証券報告書を出さないため、
company_page() が読む data/companies/*.json とは別の情報源になる。このモジュールが読むのは
人事院の資料・各機関の採用ページ・「役職員の報酬・給与等について」公表資料など、機関ごとに
このセッションが直接取得した一次情報（data/koumu/{slug}.json）だけである。

守っていることは本体（generate.py冒頭のATTRIBUTION.md方針）と同じ：
- Claude APIは呼ばない。文章はJSONにある値をそのまま出すだけ
- 採用ページが見つからなかった機関も tracks を空のまま掲載し、「非公表」「募集ページ未確認」と
  明記する。他機関の数字で埋めたり、機関ごと省略したりしない
- 出典URL・データ取得日を全ページに明記する

data/koumu/_master.json が無い（＝データ収集をまだ行っていない）場合は、このモジュールは
何もせず空リストを返す。main() 側は他の補助機能と同じく例外を握りつぶすので、
このモジュールが失敗しても企業ページの生成は止まらない。
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KOUMU_DIR = ROOT / "data" / "koumu"

KIND_LABEL = {
    "省庁": "省庁・国の機関",
    "独立行政法人": "独立行政法人",
    "政府系金融機関": "政府系金融機関・日本銀行",
}
# サイト内検索で「政府機関」「官公庁」のような総称語を打っても該当種別がヒットするための別名。
# 個別の機関名一致に加えて、この語のどれかが部分一致すればヒットする（generate.py の initSearch が使う）
KIND_ALIAS = {
    "省庁": "政府機関 官公庁 省庁",
    "独立行政法人": "政府機関 官公庁 独立行政法人 独法",
    "政府系金融機関": "政府機関 官公庁 政府系金融機関 日本銀行",
}
FIELD_LABEL = {"conditions": "条件", "overview": "概要", "timing": "時期"}
PROCESSING_NOTICE = (
    "本ページの内容は、人事院・各府省・独立行政法人等が公表している採用情報・"
    "給与公表資料を当サイトが機械的に整形したものです。"
)


# ---------------------------------------------------------------- 読み込み

def load_koumu() -> tuple[list[dict], dict] | None:
    master_path = KOUMU_DIR / "_master.json"
    if not master_path.exists():
        return None
    master = json.loads(master_path.read_text(encoding="utf-8"))
    common_path = KOUMU_DIR / "_common.json"
    common = json.loads(common_path.read_text(encoding="utf-8")) if common_path.exists() else {}

    order = [ent["slug"] for ent in master.get("entities", [])]
    by_slug = {}
    for p in sorted(KOUMU_DIR.glob("*.json")):
        if p.stem.startswith("_"):
            continue
        by_slug[p.stem] = json.loads(p.read_text(encoding="utf-8"))

    entities = [by_slug[s] for s in order if s in by_slug]
    # マスターに載っていないファイルも取りこぼさない（末尾に追加）
    entities += [by_slug[s] for s in by_slug if s not in order]
    return entities, common


# ---------------------------------------------------------------- 部品

def track_html(g, tracks: list[dict]) -> str:
    if not tracks:
        return (
            '<p class="lead">現時点で新卒定期採用の募集ページを確認できていません。'
            "採用の有無・最新情報は下記の公式サイトでご確認ください。</p>"
        )
    parts = []
    for t in tracks:
        fields = "".join(
            f'<p class="saiyo-field"><b>{g.e(FIELD_LABEL[k])}：</b>{g.e(t[k])}</p>'
            for k in ("conditions", "overview", "timing")
            if t.get(k)
        )
        parts.append(f'<div class="saiyo-track"><h3>{g.e(t["name"])}</h3>{fields}</div>')
    return f'<div class="saiyo-tracks">{"".join(parts)}</div>'


def salary_html(g, entity: dict, common: dict) -> str:
    if entity.get("salary_ref") == "common":
        kyuyo = common.get("kyuyo", {})
        sk = kyuyo.get("shoninkyu_hompusho", {})
        rows = "".join(
            f"<tr><th scope='row'>{g.e(label)}</th><td>{g.e(sk.get(key) or '非公表')}</td></tr>"
            for key, label in (
                ("sougou_inso", "初任給：総合職（院卒者）"),
                ("sougou_daisotsu", "初任給：総合職（大卒程度）"),
                ("ippan_daisotsu", "初任給：一般職（大卒程度）"),
                ("ippan_kousotsu", "初任給：一般職（高卒者）"),
            )
        )
        gh = kyuyo.get("gyousei1_heikin", {})
        rows += (
            f"<tr><th scope='row'>行政職(一)適用職員 平均給与月額・平均年齢</th>"
            f"<td>{g.e(gh.get('heikin_getsugaku') or '非公表')}／{g.e(gh.get('heikin_nenrei') or '非公表')}</td></tr>"
        )
        note = kyuyo.get("shoninkyu_hompusho", {}).get("label", "")
        source = kyuyo.get("shoninkyu_hompusho", {}).get("source", "人事院・内閣人事局資料")
        return (
            f'<table class="kv">{rows}</table>'
            f'<p class="caveat">{g.e(note)}。府省による差は原則ない（俸給表・勤務地・配属で決まる）。'
            f"出典：{g.e(source)}。</p>"
        )
    if entity.get("salary_note"):
        return f'<p class="lead">{g.e(entity["salary_note"])}</p>'
    s = entity.get("salary")
    if not s:
        return f'<p class="lead">{g.NA}</p>'
    rows = []
    if s.get("heikin_nenkan_kyuyo"):
        rows.append(("平均年間給与額", s["heikin_nenkan_kyuyo"]))
    if s.get("heikin_nenrei"):
        rows.append(("平均年齢", s["heikin_nenrei"]))
    if s.get("kokka_shisu"):
        rows.append(("対国家公務員指数（ラスパイレス比較）", s["kokka_shisu"]))
    table = "".join(f"<tr><th scope='row'>{g.e(l)}</th><td>{g.e(v)}</td></tr>" for l, v in rows) or (
        f"<tr><th scope='row'>平均年間給与額</th><td>{g.NA}</td></tr>"
    )
    note = f'<p class="caveat">{g.e(s["note"])}</p>' if s.get("note") else ""
    return f'<table class="kv">{table}</table>{note}'


def links_html(g, entity: dict) -> str:
    """公式サイト・採用ホームページのボタン。company_page()（generate.py 815〜828行目）と
    同じCSSクラス（cp-official/cp-recruit/cp-recruit-note）をそのまま使う。"""
    official = entity.get("official")
    if not official:
        return ""
    host = urllib.parse.urlsplit(official).netloc.removeprefix("www.")
    parts = [
        f'<a class="cp-official" href="{g.e(official)}" rel="nofollow noopener" target="_blank">'
        f'公式サイト<span class="cp-action-host">{g.e(host)}</span></a>'
    ]
    recruit = entity.get("recruit")
    if recruit:
        rhost = urllib.parse.urlsplit(recruit).netloc.removeprefix("www.")
        parts.append(
            f'<a class="cp-recruit" href="{g.e(recruit)}" rel="nofollow noopener" target="_blank">'
            f'採用ホームページ<span class="cp-action-host">{g.e(rhost)}</span></a>'
        )
    else:
        parts.append('<p class="cp-recruit-note">採用ページは見つけられませんでした。</p>')
    return f'<div class="cp-identity-actions">{"".join(parts)}</div>'


def source_list_html(g, entity: dict) -> str:
    urls = list(entity.get("source_urls") or [])
    s = entity.get("salary")
    if s and s.get("source_url") and s["source_url"] not in urls:
        urls.append(s["source_url"])
    if not urls:
        return "<li>非公表</li>"
    return "".join(f'<li><a href="{g.e(u)}" rel="nofollow">{g.e(u)}</a></li>' for u in urls)


# ---------------------------------------------------------------- 機関ページ

def entity_page(g, entity: dict, common: dict, fetched: str) -> str:
    name = entity["name"]
    kind = entity["kind"]
    kicker = entity.get("category") or entity.get("parent") or KIND_LABEL.get(kind, kind)
    houjin_type = entity.get("houjin_type")
    if houjin_type:
        kicker = f"{kicker}／{houjin_type}"

    title = f"{name}の新卒採用トラック・給与水準｜{g.SITE_NAME}"
    desc = (
        f"{name}（{kind}）の新卒採用トラック・応募条件・給与水準を、人事院や{name}の公式発表など"
        "一次情報だけをもとにまとめています。"
    )

    tf = entity.get("target_faculty")
    tf_html = f'<p class="lead">対象学部：{g.e(tf)}</p>' if tf else ""

    notes = entity.get("notes") or []
    notes_html = (
        f'<h2>補足</h2><ul class="saiyo-notes">{"".join(f"<li>{g.e(n)}</li>" for n in notes)}</ul>'
        if notes
        else ""
    )

    fetch_errors = entity.get("fetch_errors") or []
    err_html = (
        f'<p class="caveat">未取得の項目：{g.e("／".join(fetch_errors))}。最新情報は公式サイトでご確認ください。</p>'
        if fetch_errors
        else ""
    )

    body = f"""
<nav class="crumb"><a href="../index.html">トップ</a> › <a href="index.html">官公庁・政府系</a> › {g.e(name)}</nav>

<h1>{g.logo_img(entity.get("icon"), 28)}{g.e(name)}</h1>
<p class="lead small">{g.e(kicker)}</p>
{links_html(g, entity)}
{tf_html}

<h2>新卒採用トラック</h2>
{track_html(g, entity.get("tracks") or [])}
{err_html}

<h2>給与水準</h2>
{salary_html(g, entity, common)}

{notes_html}

<section class="source">
<h2>出典</h2>
<ul>{source_list_html(g, entity)}</ul>
<p>当サイトのデータ取得日 {g.e(entity.get("fetched_at") or fetched)}</p>
<p class="processing">{g.e(PROCESSING_NOTICE)}</p>
</section>
"""
    return g.page(title, desc, body, depth=1, canonical=f"/koumu/{entity['slug']}.html")


# ---------------------------------------------------------------- 一覧ページ

def entity_link(g, e: dict) -> str:
    """一覧での機関1件ぶん。機関名は詳細ページへのリンク、右側に公式サイト・採用ページへの
    小さな直リンクを添える。<a>は入れ子にできないため、行全体を.koumu-rowにして
    名前用の<a>と外部リンク用の<a>を並べる構成にしている（以前は行全体が1つの<a>だった）。"""
    logo = g.logo_img(e.get("icon"), 16)
    flag = '<span class="koumu-flag">募集ページ未確認</span>' if not e.get("tracks") else ""
    name_link = (
        f'<a class="koumu-row-link" href="{g.e(e["slug"])}.html">'
        f'<span class="koumu-name">{logo}{g.e(e["name"])}</span>{flag}</a>'
    )
    inline = []
    if e.get("official"):
        inline.append(
            f'<a class="koumu-inline-link" href="{g.e(e["official"])}" '
            f'rel="nofollow noopener" target="_blank">公式</a>'
        )
    if e.get("recruit"):
        inline.append(
            f'<a class="koumu-inline-link" href="{g.e(e["recruit"])}" '
            f'rel="nofollow noopener" target="_blank">採用</a>'
        )
    inline_html = f'<span class="koumu-inline-links">{"".join(inline)}</span>' if inline else ""
    return f'<div class="koumu-row">{name_link}{inline_html}</div>'


def group_block(g, title: str, items: list[dict]) -> str:
    links = "".join(entity_link(g, e) for e in items)
    return f"""<details>
<summary>{g.e(title)}（{len(items)}）</summary>
<div class="koumu-list">{links}</div>
</details>"""


def index_page(g, entities: list[dict], fetched: str) -> str:
    shocho = [e for e in entities if e["kind"] == "省庁"]
    dokuho = [e for e in entities if e["kind"] == "独立行政法人"]
    kinyu = [e for e in entities if e["kind"] == "政府系金融機関"]

    # 省庁はcategoryで、独法はparent（所管府省）でグルーピングする。
    # 出現順は人事院・総務省の原資料に沿ったマスターの並び順をそのまま使う。
    def group_by(items: list[dict], key: str) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for e in items:
            groups.setdefault(e.get(key) or "その他", []).append(e)
        return groups

    shocho_groups = group_by(shocho, "category")
    dokuho_groups = group_by(dokuho, "parent")

    shocho_html = "".join(group_block(g, k, v) for k, v in shocho_groups.items())
    dokuho_html = "".join(group_block(g, k, v) for k, v in dokuho_groups.items())
    kinyu_html = "".join(entity_link(g, e) for e in kinyu)

    n_no_track = sum(1 for e in entities if not e.get("tracks"))

    body = f"""
<nav class="crumb"><a href="index.html">トップ</a> › 官公庁・政府系</nav>

<h1>官公庁・政府系の新卒採用</h1>
<p class="lead">
省庁（官庁訪問の対象となる全{len(shocho)}機関）・新卒採用を行う独立行政法人（{len(dokuho)}法人）・
政府系金融機関＋日本銀行（{len(kinyu)}機関）の、新卒採用トラックと給与水準を一次情報だけでまとめています。
</p>
<p class="lead small">
試験に合格しても採用先が決まるわけではなく、各府省・法人が行う「官庁訪問」等の選考を経て内定する仕組みです。
制度の詳細は各機関ページでご確認ください。募集ページが確認できなかった{n_no_track}機関も、
掲載を省略せず「募集ページ未確認」と明記して掲載しています。
</p>

<h2>省庁・国の機関</h2>
<div class="koumu-groups">{shocho_html}</div>

<h2>独立行政法人</h2>
<div class="koumu-groups">{dokuho_html}</div>

<h2>政府系金融機関・日本銀行</h2>
<div class="koumu-list koumu-flat">{kinyu_html}</div>

<section class="source">
<h2>出典</h2>
<p>各機関の出典・取得日は<a href="index.html">個別の機関ページ</a>に記載しています。
共通の採用制度・給与データは人事院・内閣人事局の公表資料によります。当サイトのデータ取得日 {g.e(fetched)}</p>
<p class="processing">{g.e(PROCESSING_NOTICE)}</p>
</section>
"""
    title = f"官公庁・政府系の新卒採用トラック・給与水準一覧｜{g.SITE_NAME}"
    desc = (
        f"省庁{len(shocho)}機関・独立行政法人{len(dokuho)}法人・政府系金融機関{len(kinyu)}機関の"
        "新卒採用トラックと給与水準を、人事院や各機関の公式発表だけをもとに一覧できます。"
    )
    return g.page(title, desc, body, depth=1, canonical="/koumu/index.html")


# ---------------------------------------------------------------- 検索用バンドル

def search_bundle(entities: list[dict]) -> list[dict]:
    """サイト内検索（generate.py の APP_JS 内 initSearch）が読む軽量な一覧。
    HTMLには焼き込まず、site/data/koumu.json として別出しする（saiyo.json と同じ考え方）。"""
    return [
        {
            "slug": e["slug"],
            "name": e["name"],
            "icon": e.get("icon"),
            "group": KIND_LABEL.get(e["kind"], e["kind"]),
            "alias": KIND_ALIAS.get(e["kind"], ""),
        }
        for e in entities
    ]


# ---------------------------------------------------------------- build

def build(g, fetched: str, site: Path) -> list[str]:
    loaded = load_koumu()
    if loaded is None:
        print("  官公庁・政府系ページ：data/koumu/_master.json が無いためスキップ")
        return []
    entities, common = loaded
    (site / "koumu").mkdir(parents=True, exist_ok=True)

    urls = ["/koumu/index.html"]
    (site / "koumu" / "index.html").write_text(index_page(g, entities, fetched), encoding="utf-8")
    n_no_track = 0
    for ent in entities:
        (site / "koumu" / f"{ent['slug']}.html").write_text(
            entity_page(g, ent, common, fetched), encoding="utf-8"
        )
        urls.append(f"/koumu/{ent['slug']}.html")
        if not ent.get("tracks"):
            n_no_track += 1

    (site / "data" / "koumu.json").write_text(
        json.dumps(search_bundle(entities), ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(f"  官公庁・政府系 {len(entities)}機関（募集ページ未確認 {n_no_track}機関）→ /koumu/")
    return urls
