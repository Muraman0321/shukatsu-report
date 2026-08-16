"""有報のXBRLから連結の財務数値を取る。**extract.py とは独立に動く。**

なぜ別ファイルなのか:
    既存の extract.py は「平均年収・従業員数・多様性」というサイトの商品そのものを作る。
    そこに財務の抽出を混ぜると、財務側のバグで商品が出せなくなる。出力も
    data/financials/ に分け、generate.py は「あれば載せる」扱いにする。留学中に
    壊れる部品を増やさないための分離である。

取るもの（すべて**連結**。提出会社単体ではない）:
    売上高 / 営業利益 / 経常利益 / 純利益 / 総資産 / 純資産 / 自己資本比率 / ROE

実物で確認した地雷:

1. **要素IDだけを信じると IFRS 企業の自己資本比率が壊れる。**
   日本基準 jpcrp_cor:EquityToAssetRatioSummaryOfBusinessResults      = 0.845（自己資本比率）
   IFRS     jpcrp_cor:EquityToAssetRatioIFRSSummaryOfBusinessResults  = 3062.82
                                                     ↑ ラベルは「１株当たり親会社所有者帰属持分」
   IFRS で自己資本比率に当たるのは RatioOfOwnersEquityToGrossAssetsIFRS...（0.378）。
   よって **要素ID・ラベル・値域の3点**で受け入れを判定する。比率は 0 < x <= 1 のみ通す。

2. **営業利益は「主要な経営指標等の推移」に無い**（日本基準の様式に欄が無いため。実測3%）。
   財務諸表本体の jppfs_cor:OperatingIncome / jpigp_cor:OperatingProfitLossIFRS から取る。

3. **コンテキストを厳密に指定しないと個別・前期の数字を掴む。**
   日立・日本郵政では Prior1YearDuration_NonCons が先に現れた。
   CurrentYearDuration / CurrentYearInstant に完全一致するものだけを採用する。
   CSVの「連結・個別」列は使えない——経営指標等の行はすべて「その他」になっている。

3b. **ただし連結財務諸表を作らない会社がある。** コジマ・オービックビジネスコンサルタント・
   ジョイフル本田は連結従業員数が無く、経営指標等が CurrentYearDuration_NonConsolidatedMember
   にしか入らない。連結を探して無ければ単体に降り、**どちらを採ったかを scope に残す**。
   連結のある会社は両方持つので、必ず連結を優先する（七十七銀行は連結2,112億／単体1,949億）。

3c. **銀行・保険に「売上高」は無い。経常収益である。**
   jpcrp_cor:OrdinaryIncomeSummaryOfBusinessResults      = 経常収益（売上に相当）
   jpcrp_cor:OrdinaryIncomeLossSummaryOfBusinessResults  = 経常利益
   **Loss の4文字しか違わない。**取り違えると銀行の売上と利益が入れ替わるので、
   要素IDの完全一致に加えてラベルでも検証する。表示名も「売上高」ではなく「経常収益」にする。

4. **売上高のタグは3系統ある。** 日本基準 NetSales / IFRS Revenue / 企業拡張タグ。
   トヨタは jpcrp030000-asr_E02144-000:TotalNetRevenuesIFRS（拡張タグ）にしか入っていない。
   標準タグ → 準標準タグ → 拡張タグ の順に降りる梯子で解決し、どれで取れたかを必ず記録する。

5. **会計基準をまたいで営業利益を並べない。** IFRS の営業利益は日本基準と定義が違う
   （非経常項目の扱い）。accounting_standard を必ず持たせ、表示側で基準を併記する。
   男性育休取得率で方式をまたいで順位を付けないのと同じ理由。

6. **外貨建てで有報を出す会社がある。** 三井海洋開発は2022年12月期から米ドル表示で、
   CSVの「単位」列は**空欄**、ユニットIDが USD になる。単位名の空欄を許すと
   45.8億ドルを45.8億円として載せることになる。**ユニットIDが JPY のものだけ**を通す。

7. **連結の「主要な経営指標等の推移」に総資産が無い会社がある。**
   ファーストリテイリングの2021年8月期がそれで、総資産1つを試金石にスコープを決めると
   「連結が無い＝単体」と誤判定し、持株会社としての単体営業収益2,786億円を
   売上として拾ってしまう（正しくは連結の売上収益2兆1,330億円）。
   **複数の科目のどれか1つでも連結側にあれば連結**と判定する。

使い方:
    python extract_financials.py                 # 全社
    python extract_financials.py --sample 200    # 標本で歩留まりだけ見る
    python extract_financials.py E02144 E02529   # 指定企業だけ
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DOC_CACHE = ROOT / "cache" / "edinet" / "docs"
COMPANY_DIR = ROOT / "data" / "companies"
OUT_DIR = ROOT / "data" / "financials"

CTX_DURATION = "CurrentYearDuration"
CTX_INSTANT = "CurrentYearInstant"
NULL_TOKENS = {"", "－", "-", "―", "N/A", "該当事項なし"}

# 列（実物で確認）: 要素ID 項目名 コンテキストID 相対年度 連結・個別 期間・時点 ユニットID 単位 値
C_ELEM, C_LABEL, C_CTX, C_REL, C_CONS, C_PERIOD, C_UNIT, C_UNITNAME, C_VALUE = range(9)


class Ladder:
    """1つの指標を「どの要素IDで取れるか」の優先順で解決する。

    要素IDの完全一致 → 部分一致（拡張タグを拾う）の順。採用した要素IDを返すので、
    出力にそのまま監査証跡として残せる。
    """

    def __init__(self, name: str, exact: list[str], loose: str | None = None,
                 label_must: str | None = None, label_must_not: str | None = None,
                 lo: float | None = None, hi: float | None = None) -> None:
        self.name = name
        self.exact = exact
        self.loose = re.compile(loose) if loose else None
        self.label_must = re.compile(label_must) if label_must else None
        self.label_must_not = re.compile(label_must_not) if label_must_not else None
        self.lo, self.hi = lo, hi

    def accepts(self, label: str, val: float) -> bool:
        # 企業拡張タグは項目名が空のことがある（トヨタの TotalNetRevenuesIFRS）。
        # そのときはラベル検証を課さず、要素IDの一致だけで判断する。
        label = (label or "").strip()
        if label:
            if self.label_must and not self.label_must.search(label):
                return False
            if self.label_must_not and self.label_must_not.search(label):
                return False
        if self.lo is not None and val < self.lo:
            return False
        if self.hi is not None and val > self.hi:
            return False
        return True


def _num(s: str) -> float | None:
    s = (s or "").strip()
    if s in NULL_TOKENS:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


# --- 指標の定義 ------------------------------------------------------------
# ラベル検証: 「１株当たり」を含むものは金額指標ではないので必ず弾く（地雷1）
NOT_PER_SHARE = r"１株当たり|1株当たり|一株当たり"

MONEY_LADDERS = [
    Ladder(
        "revenue",
        exact=[
            "jpcrp_cor:NetSalesSummaryOfBusinessResults",
            "jpcrp_cor:RevenueIFRSSummaryOfBusinessResults",
            "jpcrp_cor:OperatingRevenuesIFRSKeyFinancialData",
            "jpcrp_cor:NetSalesKeyFinancialData",
            "jpcrp_cor:RevenuesUSGAAPSummaryOfBusinessResults",
            "jpcrp_cor:OperatingRevenue1SummaryOfBusinessResults",
            # 銀行・保険はここ。経常収益であって売上高ではない（地雷3c）
            "jpcrp_cor:OrdinaryIncomeSummaryOfBusinessResults",
            "jppfs_cor:NetSales",
            "jppfs_cor:OperatingRevenue1",
        ],
        loose=r"(TotalNetRevenues|SalesRevenues|NetSales|OperatingRevenue|Revenue)(IFRS|USGAAP)?$",
        # 「経常利益」を売上として拾わないための最後の砦。利益・原価は名前に含まれたら弾く
        label_must=r"売上高|売上収益|営業収益|経常収益|営業収入|収益",
        label_must_not=NOT_PER_SHARE + r"|利益|損失|原価|費用",
        lo=0,
    ),
    Ladder(
        "operating_income",
        exact=[
            "jppfs_cor:OperatingIncome",
            "jpigp_cor:OperatingProfitLossIFRS",
            "jpcrp_cor:OperatingIncomeLossSummaryOfBusinessResults",
            "jpcrp_cor:OperatingIncomeIFRSSummaryOfBusinessResults",
        ],
        # ファーストリテイリングは営業利益を企業拡張タグ
        # jpcrp030000-asr_E03217-000:OperatingIncomeIFRSSummaryOfBusinessResults に入れている
        loose=r"(OperatingIncome|OperatingProfitLoss)(IFRS|USGAAP)?(SummaryOfBusinessResults|KeyFinancialData)?$",
        label_must=r"営業利益|営業損失",
        label_must_not=NOT_PER_SHARE,
    ),
    Ladder(
        "ordinary_income",
        exact=[
            "jpcrp_cor:OrdinaryIncomeLossSummaryOfBusinessResults",
            "jppfs_cor:OrdinaryIncome",
        ],
        label_must_not=NOT_PER_SHARE,
    ),
    Ladder(
        "net_income",
        exact=[
            "jpcrp_cor:ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults",
            "jpcrp_cor:ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
            "jpcrp_cor:NetIncomeLossUSGAAPSummaryOfBusinessResults",
            "jppfs_cor:ProfitLossAttributableToOwnersOfParent",
            "jpigp_cor:ProfitLossAttributableToOwnersOfParentIFRS",
        ],
        label_must_not=NOT_PER_SHARE,
    ),
]

INSTANT_LADDERS = [
    Ladder(
        "total_assets",
        exact=[
            "jpcrp_cor:TotalAssetsSummaryOfBusinessResults",
            "jpcrp_cor:TotalAssetsIFRSSummaryOfBusinessResults",
            "jpcrp_cor:TotalAssetsUSGAAPSummaryOfBusinessResults",
            "jppfs_cor:Assets",
        ],
        label_must_not=NOT_PER_SHARE,
        lo=0,
    ),
    Ladder(
        "net_assets",
        exact=[
            "jpcrp_cor:NetAssetsSummaryOfBusinessResults",
            "jpcrp_cor:EquityAttributableToOwnersOfParentIFRSSummaryOfBusinessResults",
            "jpcrp_cor:TotalEquityUSGAAPSummaryOfBusinessResults",
            "jppfs_cor:NetAssets",
        ],
        label_must_not=NOT_PER_SHARE,
    ),
]

# 比率。**0 < x <= 1 のみ通す**。IFRS の EquityToAssetRatio... は 3062.82 を返すのでここで落ちる（地雷1）
RATIO_LADDERS = [
    Ladder(
        "equity_ratio",
        exact=[
            "jpcrp_cor:EquityToAssetRatioSummaryOfBusinessResults",
            "jpcrp_cor:RatioOfOwnersEquityToGrossAssetsIFRSSummaryOfBusinessResults",
            "jpcrp_cor:RatioOfOwnersEquityToGrossAssetsUSGAAPSummaryOfBusinessResults",
        ],
        label_must=r"自己資本比率|持分比率",
        label_must_not=NOT_PER_SHARE,
        lo=0.0001,
        hi=1.0,
    ),
    Ladder(
        "roe",
        exact=[
            "jpcrp_cor:RateOfReturnOnEquitySummaryOfBusinessResults",
            "jpcrp_cor:RateOfReturnOnEquityIFRSSummaryOfBusinessResults",
            "jpcrp_cor:RateOfReturnOnEquityUSGAAPSummaryOfBusinessResults",
        ],
        label_must=r"利益率",
        label_must_not=NOT_PER_SHARE,
        lo=-10.0,
        hi=10.0,
    ),
]


def load_rows(doc_id: str) -> list[list[str]]:
    path = DOC_CACHE / f"{doc_id}_type5.zip"
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if "asr" in n and n.endswith(".csv"))
        text = z.read(name).decode("utf-16")
    return list(csv.reader(io.StringIO(text), delimiter="\t"))[1:]


NON_CONSOLIDATED_SUFFIX = "_NonConsolidatedMember"


def _resolve_in_ctx(rows: list[list[str]], ladder: Ladder, ctx: str, want_money: bool) -> dict[str, Any] | None:
    """1つのコンテキストの中だけで梯子を降りる。"""
    cand: list[list[str]] = []
    for r in rows:
        if len(r) < 9 or r[C_CTX] != ctx:
            continue
        # 金額は円建てだけを通す。外貨建て有報はユニットIDが USD 等で単位名が空欄になる（地雷6）
        if want_money and r[C_UNIT].strip().upper() != "JPY":
            continue
        cand.append(r)
    if not cand:
        return None

    by_elem: dict[str, list[str]] = {}
    for r in cand:
        by_elem.setdefault(r[C_ELEM], r)

    def try_row(r: list[str], how: str) -> dict[str, Any] | None:
        v = _num(r[C_VALUE])
        if v is None or not ladder.accepts(r[C_LABEL], v):
            return None
        return {"value": v, "element": r[C_ELEM], "label": r[C_LABEL].strip(), "resolved_by": how}

    for eid in ladder.exact:
        r = by_elem.get(eid)
        if r is not None:
            got = try_row(r, "標準タグ")
            if got:
                return got
    if ladder.loose:
        for eid, r in by_elem.items():
            if ladder.loose.search(eid):
                got = try_row(r, "拡張タグ" if "_" in eid.split(":")[0] else "準標準タグ")
                if got:
                    return got
    return None


def document_scope(rows: list[list[str]]) -> str:
    """この書類が連結ベースか単体ベースかを**書類単位で1回だけ**決める（地雷3b）。

    項目ごとに連結→単体へ落とすと、1社の中で「連結の売上」と「単体の純利益」が
    混ざる。それは提出会社の年収を連結の人数で割るのと同じ誤りなので、
    スコープを先に固定し、以後すべての項目を同じスコープの中だけで解決する。

    試金石は1つでは足りない（地雷7）。総資産だけを見ると、連結欄に総資産を
    載せていないファーストリテイリング2021年8月期を「連結なし」と誤判定する。
    **売上・総資産・純利益・純資産のどれか1つでも連結側にあれば連結**とする。
    """
    anchors = [
        (INSTANT_LADDERS[0], CTX_INSTANT),   # total_assets
        (MONEY_LADDERS[0], CTX_DURATION),    # revenue
        (MONEY_LADDERS[3], CTX_DURATION),    # net_income
        (INSTANT_LADDERS[1], CTX_INSTANT),   # net_assets
    ]
    for lad, ctx in anchors:
        if _resolve_in_ctx(rows, lad, ctx, want_money=True) is not None:
            return "連結"
    return "単体"


def accounting_standard(rows: list[list[str]]) -> str:
    ids = "\n".join(r[C_ELEM] for r in rows if r)
    if "jpigp_cor:" in ids or "IFRS" in ids:
        return "IFRS"
    if "USGAAP" in ids:
        return "米国基準"
    return "日本基準"


def extract_doc(doc_id: str) -> dict[str, Any]:
    rows = load_rows(doc_id)
    scope = document_scope(rows)
    suffix = "" if scope == "連結" else NON_CONSOLIDATED_SUFFIX
    dur, inst = CTX_DURATION + suffix, CTX_INSTANT + suffix

    out: dict[str, Any] = {
        "accounting_standard": accounting_standard(rows),
        "scope": scope,
        "items": {},
    }
    plan = (
        [(lad, dur, True) for lad in MONEY_LADDERS]
        + [(lad, inst, True) for lad in INSTANT_LADDERS]
        + [(lad, inst if lad.name == "equity_ratio" else dur, False) for lad in RATIO_LADDERS]
    )
    for lad, ctx, want_money in plan:
        got = _resolve_in_ctx(rows, lad, ctx, want_money)
        if got:
            got["scope"] = scope
            out["items"][lad.name] = got
    return out


def build(company_json: Path) -> dict[str, Any] | None:
    d = json.loads(company_json.read_text(encoding="utf-8"))
    years_out = []
    for y in d.get("years", []):
        did = (y.get("source") or {}).get("doc_id")
        if not did or not (DOC_CACHE / f"{did}_type5.zip").exists():
            continue
        try:
            fin = extract_doc(did)
        except (zipfile.BadZipFile, StopIteration, UnicodeDecodeError) as e:
            print(f"  !! {d['name']} {did}: {type(e).__name__}")
            continue
        items = fin["items"]
        vals = {k: v["value"] for k, v in items.items()}

        # 財務の値が単体ベースなら、一人当たりの分母も単体（提出会社）に揃える。
        # 連結の利益を単体の人数で割ると、その企業だけ数倍に見える（スコープ混在は嘘になる）
        emp_c = (y.get("employees") or {}).get("consolidated")
        emp_r = (y.get("employees") or {}).get("reporting_company")
        fin_scope = fin["scope"]
        if fin_scope == "連結":
            denom, denom_label = emp_c, "連結従業員数"
        else:
            denom, denom_label = (emp_r or emp_c), ("提出会社従業員数" if emp_r else "連結従業員数")

        per_capita = {}
        if denom:
            for src, dst in (("revenue", "revenue_per_employee"),
                             ("operating_income", "operating_income_per_employee")):
                if src in vals:
                    per_capita[dst] = round(vals[src] / denom)

        years_out.append({
            "period_end": y["source"]["period_end"],
            "doc_id": did,
            "accounting_standard": fin["accounting_standard"],
            "scope": fin_scope,
            # 銀行・保険は「売上高」ではなく「経常収益」。有報のラベルをそのまま持ち回る（地雷3c）
            "revenue_label": items.get("revenue", {}).get("label", "").replace("、経営指標等", "") or None,
            "consolidated_employees": emp_c,
            "per_capita_denominator": denom,
            "per_capita_denominator_label": denom_label if denom else None,
            "values": vals,
            "per_capita": per_capita,
            "provenance": {k: {"element": v["element"], "resolved_by": v["resolved_by"],
                               "scope": v.get("scope"), "label": v["label"]}
                           for k, v in items.items()},
        })
    if not years_out:
        return None
    return {
        "edinet_code": d["edinet_code"],
        "name": d["name"],
        "peer_group": d.get("peer_group", ""),
        "is_holding": d.get("is_holding", False),
        "latest": years_out[-1],
        "years": years_out,
    }


def main(argv: list[str]) -> None:
    sample = 0
    if "--sample" in argv:
        i = argv.index("--sample")
        sample = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    only = set(a for a in argv if a.startswith("E"))

    files = sorted(COMPANY_DIR.glob("*.json"))
    if only:
        files = [f for f in files if f.stem in only]
    if sample:
        import random
        random.seed(20260815)
        files = random.sample(files, min(sample, len(files)))

    if not sample:
        OUT_DIR.mkdir(parents=True, exist_ok=True)

    from collections import Counter
    cov = Counter()
    std = Counter()
    resolved_how = Counter()
    scopes = Counter()
    rev_labels = Counter()
    n = 0
    for f in files:
        rec = build(f)
        if rec is None:
            continue
        n += 1
        lat = rec["latest"]
        std[lat["accounting_standard"]] += 1
        scopes[lat["scope"]] += 1
        rev_labels[lat["revenue_label"] or "（取れず）"] += 1
        for k in lat["values"]:
            cov[k] += 1
        for k in lat["per_capita"]:
            cov[k] += 1
        for k, v in lat["provenance"].items():
            resolved_how[v["resolved_by"]] += 1
        if not sample:
            (OUT_DIR / f"{rec['edinet_code']}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== {n}社（最新期）===")
    print("会計基準:", dict(std))
    print("スコープ:", dict(scopes))
    print("売上の科目名:", dict(rev_labels.most_common(8)))
    print("\n歩留まり:")
    order = ["revenue", "operating_income", "ordinary_income", "net_income",
             "total_assets", "net_assets", "equity_ratio", "roe",
             "revenue_per_employee", "operating_income_per_employee"]
    for k in order:
        print(f"  {k:32s} {cov[k]:5d}/{n}  ({cov[k]/n*100:5.1f}%)")
    print("\n解決の内訳:", dict(resolved_how))
    if not sample:
        print(f"\n書き出し: {OUT_DIR}")


main(sys.argv[1:])
