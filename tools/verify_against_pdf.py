"""抽出したJSONの数値を、有報PDFの「従業員の状況」の文面と突き合わせる。

自動照合ではなく **目視照合の補助**。PDFの該当箇所を切り出して並べて表示し、
人間が読んで一致を確認する。ここを飛ばすと、全企業の数値が信用できなくなる。

    python tools/verify_against_pdf.py E02529 E02144 E01967 E03606 E03907
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]


def pdf_excerpt(pdf: Path, anchor: str, before: int = 700, after: int = 500) -> str | None:
    doc = fitz.open(pdf)
    for page in doc:
        text = page.get_text()
        i = text.find(anchor)
        if i >= 0:
            chunk = text[max(0, i - before) : i + after]
            return re.sub(r"\n{2,}", "\n", chunk)
    return None


def yen(v):
    return f"{v:,} 円" if isinstance(v, int) else "—"


def main(codes: list[str]) -> None:
    for code in codes:
        rec = json.loads((ROOT / "data" / "companies" / f"{code}.json").read_text(encoding="utf-8"))
        pdf = ROOT / "cache" / "edinet" / "docs" / f"{rec['source']['doc_id']}_type2.pdf"
        if not pdf.exists():
            print(f"!! {rec['name']}: PDF未取得 ({pdf.name})")
            continue

        rc, emp, div = rec["reporting_company"], rec["employees"], rec["diversity"]
        print("=" * 78)
        print(f"{rec['name']}   決算期 {rec['source']['period_end']}   {rec['source']['doc_id']}")
        print("=" * 78)
        print("【抽出値】")
        print(f"  従業員数(連結)      {emp['consolidated']:,}" if emp["consolidated"] else "  従業員数(連結)      —")
        print(f"  従業員数(提出会社)   {emp['reporting_company']:,}" if emp["reporting_company"] else "  従業員数(提出会社)   —")
        print(f"  平均年間給与        {yen(rc['average_annual_salary_yen'])}")
        print(f"  平均年齢            {rc['average_age_years']}")
        print(f"  平均勤続年数        {rc['average_tenure_years']}")
        print(f"  多様性のスコープ     {div['scope']}")
        print(f"  女性管理職比率       {div['female_manager_ratio']}")
        print(f"  女性賃金/男性賃金    {div['female_to_male_wage_ratio_all']}")
        print(f"  男性育休取得率       {div['male_childcare_leave_ratio']}  [{div['male_childcare_leave_method']}]")
        if rec["subsidiaries"]:
            print(f"  連結子会社 {len(rec['subsidiaries'])}社。先頭3社:")
            for s in rec["subsidiaries"][:3]:
                print(
                    f"    - {s['name']}: 女性管理職={s['female_manager_ratio']} "
                    f"女/男賃金={s['female_to_male_wage_ratio_all']}"
                )
        for n in rec["notes"]:
            print(f"  ※ {n}")

        print("\n【有報PDF 該当箇所】")
        excerpt = pdf_excerpt(pdf, "平均年間給与")
        print(excerpt if excerpt else "  (「平均年間給与」がPDF本文に見つからない)")
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
