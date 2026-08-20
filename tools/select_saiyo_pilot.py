"""新卒の採用枠を作る対象企業を選ぶ。**このファイルは実行するたびに対象リストを作り直す道具**。

## 選び方とその理由
主要12業界（就活で関心が集まりやすい業界）の**全社**を対象にする（2026-08-17、パイロット100社
から実用版に拡大）。連結従業員数の多い順に並べておくと、クロール・構造化を規模の大きい
（＝関心が集まりやすい）会社から進められる。

採用ページのURLが分かっていない会社（company_links.json に recruit が無い会社）は
クロールしようがないので、この時点で除外する。

出力: data/saiyo_pilot.json （edinet_code のリストと、選んだ理由を書いた要約）。
既存の data/saiyo/*.json はこのファイルを再実行しても消えない（別ファイル）。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 対象12業界（この業界に属する会社は全社を対象にする。上限は付けない）
TARGET_GROUPS: list[str] = [
    "総合商社", "銀行業", "保険業", "証券", "電気機器", "情報・通信業",
    "食料品", "医薬品", "化学", "輸送用機器", "小売", "建設業",
]


def main() -> None:
    with (ROOT / "companies.csv").open(encoding="utf-8", newline="") as f:
        companies = list(csv.DictReader(f))
    links = json.loads((ROOT / "data" / "company_links.json").read_text(encoding="utf-8"))

    by_group: dict[str, list[dict]] = {g: [] for g in TARGET_GROUPS}
    skipped_no_recruit = 0
    for row in companies:
        g = row["peer_group"]
        if g not in TARGET_GROUPS:
            continue
        code = row["edinet_code"]
        if not links.get(code, {}).get("recruit"):
            skipped_no_recruit += 1
            continue
        cfile = ROOT / "data" / "companies" / f"{code}.json"
        if not cfile.exists():
            continue
        c = json.loads(cfile.read_text(encoding="utf-8"))
        emp = ((c.get("latest") or {}).get("employees") or {}).get("consolidated")
        by_group[g].append({
            "edinet_code": code,
            "name": row["name"],
            "peer_group": g,
            "employees": emp or 0,
            "recruit_url": links[code]["recruit"],
            "is_holding": c.get("is_holding", False),
        })

    selected: list[dict] = []
    for g in TARGET_GROUPS:
        rows = sorted(by_group[g], key=lambda r: r["employees"], reverse=True)
        selected.extend(rows)

    out = {
        "criteria": "主要12業界の全社（連結従業員数の多い順）。採用ページURL判明済みのみ",
        "groups": TARGET_GROUPS,
        "total": len(selected),
        "skipped_no_recruit_url": skipped_no_recruit,
        "companies": selected,
    }
    (ROOT / "data" / "saiyo_pilot.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"選定 {len(selected)} 社（採用ページURL不明で除外 {skipped_no_recruit} 社）")
    for g in TARGET_GROUPS:
        rows = [r for r in selected if r["peer_group"] == g]
        names = "、".join(r["name"].strip() for r in rows[:5])
        print(f"  {g}: {len(rows)}社  例）{names}")


if __name__ == "__main__":
    main()
