"""採用枠パイロットの対象企業を選ぶ。**このファイルは一度実行して結果を固定するための道具**。

## 選び方とその理由
主要12業界（就活で関心が集まりやすい業界）から、**連結従業員数の多い順**に会社を選ぶ。
知名度で人手選びをすると恣意的になるので、既存のランキングページと同じ「客観的に測れる量」
（規模）を基準にする。総合商社・保険業のように業界自体が小さいところは全社を対象にする。

採用ページのURLが分かっていない会社（company_links.json に recruit が無い会社）は
クロールしようがないので、この時点で除外する。

出力: data/saiyo_pilot.json （edinet_code のリストと、選んだ理由を書いた要約）
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 業界名 → 上位何社を採るか。業界自体が小さい所は大きな数字にして全社を拾う
TARGET_GROUPS: dict[str, int] = {
    "総合商社": 99,
    "銀行業": 8,
    "保険業": 99,
    "証券": 8,
    "電気機器": 10,
    "情報・通信業": 10,
    "食料品": 10,
    "医薬品": 10,
    "化学": 8,
    "輸送用機器": 8,
    "小売": 8,
    "建設業": 8,
}


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
    for g, n in TARGET_GROUPS.items():
        rows = sorted(by_group[g], key=lambda r: r["employees"], reverse=True)[:n]
        selected.extend(rows)

    out = {
        "criteria": "主要12業界、連結従業員数の多い順（総合商社・保険業は全社）。採用ページURL判明済みのみ",
        "groups": {g: TARGET_GROUPS[g] for g in TARGET_GROUPS},
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
