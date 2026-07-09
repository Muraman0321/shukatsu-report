"""複数社のEDINET CSVを開き、抽出対象タグがどのコンテキストで現れるかを一覧する。

extract.py を推測で書かないための調査用スクリプト。使い捨てではなく、
新しい会計基準の企業を足したときに再実行して差分を見る。
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TARGETS = {
    "従業員数": ":NumberOfEmployees",
    "平均年齢": ":AverageAgeYearsInformationAboutReportingCompanyInformationAboutEmployees",
    "平均勤続(年)": ":AverageLengthOfServiceYearsInformationAboutReportingCompanyInformationAboutEmployees",
    "平均勤続(月)": ":AverageLengthOfServiceMonthsInformationAboutReportingCompanyInformationAboutEmployees",
    "平均年間給与": ":AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees",
    "女性管理職比率": ":RatioOfFemaleEmployeesInManagerialPositionsMetricsOfReportingCompany",
    "男女賃金差異(全)": ":AllEmployeesDifferencesInWagesBetweenMaleAndFemaleEmployeesMetricsOfReportingCompany",
    "男女賃金差異(正規)": ":RegularEmployeesDifferencesInWagesBetweenMaleAndFemaleEmployeesMetricsOfReportingCompany",
}
# 男性育休取得率は算出方式が2通りあり、企業ごとに使うタグが違う
CHILDCARE_SUBSTR = "RatioOfMaleEmployeesTakingChildcareLeave"


def load_asr(zip_path: Path) -> list[list[str]]:
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if "asr" in n and n.endswith(".csv"))
        text = z.read(name).decode("utf-16")
    return list(csv.reader(io.StringIO(text), delimiter="\t"))[1:]


def main(doc_ids: list[str]) -> None:
    index = {}
    with (ROOT / "data" / "doc_index.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            index[r["doc_id"]] = r["filer_name"]

    for doc_id in doc_ids:
        path = ROOT / "cache" / "edinet" / "docs" / f"{doc_id}_type5.zip"
        rows = load_asr(path)
        print("=" * 78)
        print(f"{index.get(doc_id, '?')}  ({doc_id})")
        print("=" * 78)

        for label, suffix in TARGETS.items():
            hits = [r for r in rows if len(r) >= 9 and r[0].endswith(suffix)]
            if not hits:
                print(f"  {label:18s} → 見つからない")
                continue
            for r in hits:
                ctx = r[2]
                if "ReportableSegment" in ctx or "NotIncludedInReportable" in ctx:
                    continue  # セグメント内訳は別扱い
                if r[3] not in ("当期末", "当期"):
                    continue
                print(f"  {label:18s} ctx={ctx:45s} 単位={r[7]:4s} 値={r[8]}")

        childcare = [
            r for r in rows
            if len(r) >= 9 and CHILDCARE_SUBSTR in r[0] and "ReportingCompany" in r[0]
        ]
        for r in childcare:
            kind = "71条の6第2号方式" if "Article714Item2" in r[0] else "原則方式"
            scope = "全労働者" if r[0].startswith("jpcrp_cor:AllEmployees") else "正規/非正規"
            print(f"  {'男性育休取得率':18s} [{kind}/{scope}] 値={r[8]}")
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
