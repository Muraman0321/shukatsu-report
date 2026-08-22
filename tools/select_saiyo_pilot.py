"""新卒の採用枠を作る対象企業を選ぶ。**このファイルは実行するたびに対象リストを作り直す道具**。

## 選び方とその理由
既定は主要12業界（就活で関心が集まりやすい業界）の**全社**（2026-08-17、パイロット100社
から実用版に拡大）。`--all` を付けると **35業界の記載企業すべて** に広がる（2026-08-21、
ユーザー指示「記載企業すべてでクロールしたい」による全社化）。

連結従業員数の多い順に並べておくと、クロール・構造化を規模の大きい
（＝関心が集まりやすい）会社から進められる。

URLの優先順位（2026-08-21）:
1. company_links.json の recruit（公式トップから辿れた採用ページ）
2. data/links/manual.json の recruit / official（自動で見つからなかった会社を
   Web検索で手動特定した上書き。company_links.json は fetch_links merge で
   再生成されるため、手動分は別ファイルに分離してここで重ねる）
3. recruit が無くても official があれば対象に含める。クロール側（fetch_saiyo.py）が
   公式トップから採用リンクを引き直す（貼られたリンクだけを辿る方式のまま）

recruit も official も無い会社はクロールの始点が無いので除外する（URLの推測はしない）。
除外した会社のうち平均年収900万円以上のものは、手動特定の候補として実行時に一覧を出す。

出力: data/saiyo_pilot.json （edinet_code のリストと、選んだ理由を書いた要約）。
既存の data/saiyo/*.json はこのファイルを再実行しても消えない（別ファイル）。

使い方:
    python tools/select_saiyo_pilot.py          # 従来どおり主要12業界
    python tools/select_saiyo_pilot.py --all    # 35業界の記載企業すべて
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 既定の対象12業界（--all を付けない場合。この業界に属する会社は全社を対象にする）
TARGET_GROUPS: list[str] = [
    "総合商社", "銀行業", "保険業", "証券", "電気機器", "情報・通信業",
    "食料品", "医薬品", "化学", "輸送用機器", "小売", "建設業",
]

HIGH_SALARY_YEN = 9_000_000  # URL不明でも一覧に出して手動特定を促す閾値


def load_links() -> dict:
    links = json.loads((ROOT / "data" / "company_links.json").read_text(encoding="utf-8"))
    manual_path = ROOT / "data" / "links" / "manual.json"
    if manual_path.exists():
        manual = json.loads(manual_path.read_text(encoding="utf-8"))
        for code, ent in manual.items():
            if code.startswith("_"):
                continue  # _comment
            base = links.setdefault(code, {})
            for k in ("official", "recruit"):
                if ent.get(k) and not base.get(k):
                    base[k] = ent[k]
                    base[f"{k}_source"] = ent.get("source", "manual")
    return links


def main() -> None:
    everything = "--all" in sys.argv[1:]
    with (ROOT / "companies.csv").open(encoding="utf-8", newline="") as f:
        companies = list(csv.DictReader(f))
    links = load_links()

    by_group: dict[str, list[dict]] = {}
    skipped_no_url = 0
    high_salary_no_url: list[tuple[str, str, int]] = []
    for row in companies:
        g = row["peer_group"]
        if not everything and g not in TARGET_GROUPS:
            continue
        code = row["edinet_code"]
        ent = links.get(code, {})
        recruit = ent.get("recruit")
        official = ent.get("official")
        cfile = ROOT / "data" / "companies" / f"{code}.json"
        if not cfile.exists():
            continue
        c = json.loads(cfile.read_text(encoding="utf-8"))
        if not recruit and not official:
            skipped_no_url += 1
            sal = ((c.get("latest") or {}).get("reporting_company") or {}
                   ).get("average_annual_salary_yen") or 0
            if sal >= HIGH_SALARY_YEN:
                high_salary_no_url.append((code, row["name"].strip(), sal))
            continue
        emp = ((c.get("latest") or {}).get("employees") or {}).get("consolidated")
        by_group.setdefault(g, []).append({
            "edinet_code": code,
            "name": row["name"],
            "peer_group": g,
            "employees": emp or 0,
            "recruit_url": recruit,          # 無ければ null（fetch_saiyo が official から引き直す）
            "official_url": official,
            "is_holding": c.get("is_holding", False),
        })

    group_order = list(by_group) if everything else [g for g in TARGET_GROUPS if g in by_group]
    selected: list[dict] = []
    for g in group_order:
        selected.extend(sorted(by_group[g], key=lambda r: r["employees"], reverse=True))

    out = {
        "criteria": ("35業界の記載企業すべて（連結従業員数の多い順）。採用ページURLまたは"
                     "公式サイトURL判明分。公式のみの会社はクロール時に採用リンクを引き直す"
                     if everything else
                     "主要12業界の全社（連結従業員数の多い順）。採用ページURL判明済みのみ"),
        "groups": group_order,
        "total": len(selected),
        "skipped_no_url": skipped_no_url,
        "companies": selected,
    }
    (ROOT / "data" / "saiyo_pilot.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    n_direct = sum(1 for r in selected if r["recruit_url"])
    n_official = len(selected) - n_direct
    print(f"選定 {len(selected)} 社（採用URLあり {n_direct} / 公式のみ {n_official}。"
          f"URL不明で除外 {skipped_no_url} 社）")
    for g in group_order:
        rows = [r for r in selected if r["peer_group"] == g]
        names = "、".join(r["name"].strip() for r in rows[:3])
        print(f"  {g}: {len(rows)}社  例）{names}")
    if high_salary_no_url:
        print(f"\n※URL不明のまま除外した平均年収{HIGH_SALARY_YEN//10_000}万円以上の会社"
              f"（data/links/manual.json への追記候補）:")
        for code, name, sal in sorted(high_salary_no_url, key=lambda x: -x[2]):
            print(f"  {code} {name} {sal:,}円")


if __name__ == "__main__":
    main()
