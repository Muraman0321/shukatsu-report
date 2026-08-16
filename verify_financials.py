"""抽出した財務値が「取り違え」を起こしていないかを機械的に検算する。

extract_financials.py は要素IDとラベルで値を選ぶ。それでも起こりうる事故は、
**別の科目を同じ箱に入れてしまうこと**である。銀行の経常収益と経常利益は
要素IDが Loss の4文字しか違わず、持株会社の営業収益は受取配当金であり、
IFRS の「営業利益」は日本基準と定義が違う。

そこで、会計上ほぼ必ず成り立つ関係だけを使って検算する。**推測はしない。
関係が破れた値は「怪しい」と印を付けて出し、公開の判断材料にする。**

検算するもの:
    1. 総資産 >= 純資産            （必ず成り立つ）
    2. 自己資本比率 ≒ 純資産/総資産  （IFRSは分子が親会社所有者持分なので緩めに見る）
    3. 売上 >= 営業利益            （破れたら売上と利益の取り違えを疑う）
    4. 一人当たり売上高が常識の範囲か（極端な外れ値はスコープ混在の兆候）
    5. 5期の売上が桁で飛んでいないか（単位取り違えの検出）

使い方:
    python verify_financials.py            # data/financials/*.json を全件検算
    python verify_financials.py --strict    # 1件でも致命的な破れがあれば異常終了
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIN_DIR = ROOT / "data" / "financials"
OUT_CSV = ROOT / "data" / "financials_review.csv"

# 一人当たり売上高の常識の範囲。総合商社は数十億円/人、労働集約なサービス業で数百万円/人。
# この外に出たものは「スコープが混ざっている」疑いとして人が見る。
PER_CAPITA_LO = 1_000_000            # 100万円/人
PER_CAPITA_HI = 20_000_000_000       # 200億円/人


def check(rec: dict) -> list[tuple[str, str]]:
    """(重大度, 説明) のリスト。重大度は fatal / warn。"""
    out: list[tuple[str, str]] = []
    lat = rec["latest"]
    v = lat["values"]
    ta, na = v.get("total_assets"), v.get("net_assets")
    rev, op = v.get("revenue"), v.get("operating_income")
    er = v.get("equity_ratio")

    if ta is not None and na is not None and na > ta * 1.001:
        out.append(("fatal", f"純資産({na:,.0f}) > 総資産({ta:,.0f})"))

    if er is not None and ta and na is not None:
        implied = na / ta
        # IFRSの持分比率は分子が「親会社の所有者に帰属する持分」なので非支配持分ぶん小さく出る。
        # 逆転や桁違いだけを拾いたいので、比は 0.5〜1.5 を許す
        if not (0.5 <= (er / implied if implied else 0) <= 1.5):
            out.append(("warn", f"自己資本比率{er:.3f} と 純資産/総資産{implied:.3f} が乖離"))

    if rev is not None and op is not None and op > rev:
        out.append(("fatal", f"営業利益({op:,.0f}) > 売上({rev:,.0f})：科目の取り違えを疑う"))

    pc = lat["per_capita"].get("revenue_per_employee")
    if pc is not None and not (PER_CAPITA_LO <= pc <= PER_CAPITA_HI):
        out.append(("warn", f"一人当たり売上 {pc:,.0f}円 が常識の範囲外（分母={lat.get('per_capita_denominator')}）"))

    # 5期で売上が10倍以上動いたら単位の取り違えを疑う（実際の事業成長でこの幅は稀）
    series = [y["values"].get("revenue") for y in rec["years"]]
    series = [s for s in series if s]
    if len(series) >= 2:
        lo, hi = min(series), max(series)
        if lo and hi / lo >= 10:
            out.append(("warn", f"売上が5期で{hi/lo:.0f}倍動いている（単位取り違えの疑い）"))

    # スコープが年度で入れ替わると推移が不連続になる
    scopes = {y.get("scope") for y in rec["years"]}
    if len(scopes) > 1:
        out.append(("warn", f"年度でスコープが混在：{sorted(s for s in scopes if s)}"))

    return out


def main(argv: list[str]) -> None:
    strict = "--strict" in argv
    files = sorted(FIN_DIR.glob("*.json"))
    if not files:
        raise SystemExit("data/financials/ が空です。先に python extract_financials.py を実行してください。")

    tally = Counter()
    rows = ["edinet_code,name,peer_group,severity,detail"]
    fatal_names = []
    for f in files:
        rec = json.loads(f.read_text(encoding="utf-8"))
        for sev, msg in check(rec):
            tally[(sev, msg.split("：")[0].split("（")[0][:24])] += 1
            tally[sev] += 1
            safe = msg.replace(",", "").replace('"', "")
            rows.append(f'{rec["edinet_code"]},"{rec["name"]}",{rec.get("peer_group","")},{sev},"{safe}"')
            if sev == "fatal":
                fatal_names.append((rec["name"], msg))

    OUT_CSV.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"検算 {len(files)} 社")
    print(f"  fatal {tally['fatal']} 件 / warn {tally['warn']} 件")
    print("\n内訳:")
    for (sev, kind), c in sorted(((k, v) for k, v in tally.items() if isinstance(k, tuple)), key=lambda x: -x[1]):
        print(f"  {sev:6s} {kind:32s} {c}")
    if fatal_names:
        print("\nfatal の例:")
        for nm, msg in fatal_names[:15]:
            print(f"  {nm[:26]:28s} {msg}")
    print(f"\n→ {OUT_CSV}")

    if strict and tally["fatal"]:
        raise SystemExit(1)


main(sys.argv[1:])
