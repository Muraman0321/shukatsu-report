"""EDINETのCSVから数値を機械的に抽出する。**Claude API は一切呼ばない。**

平均年収や男女間賃金差異を言語モデルに要約させると、桁を間違えたときに
気づけない。数値はここでコードが取り、Claude には文章化だけをさせる。

CSVの構造（実物で確認済み）:
  UTF-16 LE / タブ区切り / 9列
  要素ID  項目名  コンテキストID  相対年度  連結・個別  期間・時点  ユニットID  単位  値

読み解くうえで踏んだ地雷を、そのままコードの前提として残す:

1. **従業員数は「連結」と「提出会社（単体）」で別物。**
   三菱商事は連結 63,037人 / 単体 4,456人。そして **平均年間給与は単体ベース**。
   連結の人数と単体の年収を並べると事実として誤りになる。

2. **平均勤続年数・平均年齢の書き方が2通りある。**
   三菱商事の勤続 = 「17年」+「7月」の2タグ。トヨタ = 「15.1」（小数の年）で月タグ無し。
   三菱地所は平均年齢まで「42歳」+「6か月」。年タグだけ読むと月ぶん短く出る。

3. **男性育休取得率のタグが2方式ある。**
   原則方式と、育児介護休業法施行規則71条の6第2号方式（育児目的休暇を含むので高く出る）。
   トヨタ 79.0%（原則）と三菱商事 52.6%（71条の6第2号）を同じ列に並べても比較にならない。
   どちらの方式かを必ず併記する。

4. **持株会社は「提出会社の多様性指標」を持たない。**
   MUFGは提出会社（持株、3,637人）の指標を出さず、連結子会社35社ぶんを並べる。
   子会社名は jpcrp_cor:ConsolidatedSubsidiariesMetricsOfConsolidatedSubsidiaries が
   同じコンテキスト（Row1Member 等）に持っている。Row1Member は株主一覧や配当の表でも
   使い回されるので、要素名でスコープを切ってから対応づける。

5. **「男女の賃金の差異」は男性を100としたときの女性の賃金の割合。**
   0.647 は「64.7%の差がある」ではなく「女性は男性の64.7%」。逆に読まれると
   企業の名誉にも関わるので、フィールド名を female_to_male_wage_ratio とする。

6. **提出会社の従業員数はXBRLから決められない。**
   標準タグ jpcrp_cor:NumberOfEmployees が有報の表の値とは限らず（27社中4社で不一致）、
   表の値がどの拡張タグ・どのコンテキストに入るかも企業ごとに違う。
   よって **有報PDFの表を正本**とし、verify_employees.py が作る
   data/employees_verified.csv から読む。XBRLの標準タグは「就業人員数」として別に持つ。

使い方:
    python extract.py                 # 全社
    python extract.py E02529 E02144   # 指定した企業だけ
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
OUT_DIR = ROOT / "data" / "companies"
REVIEW_CSV = ROOT / "data" / "extracted_review.csv"

NS = "jpcrp_cor:"
CTX_CONSOLIDATED = "CurrentYearInstant"
CTX_REPORTING = "CurrentYearInstant_NonConsolidatedMember"

# 相対年度 → 何年前か（従業員数の5年推移に使う）
YEAR_CONTEXTS = {
    "CurrentYearInstant": 0,
    "Prior1YearInstant": 1,
    "Prior2YearInstant": 2,
    "Prior3YearInstant": 3,
    "Prior4YearInstant": 4,
}

NULL_TOKENS = {"", "－", "-", "―", "N/A", "該当事項なし"}

E_EMPLOYEES = NS + "NumberOfEmployees"
E_AGE_Y = NS + "AverageAgeYearsInformationAboutReportingCompanyInformationAboutEmployees"
E_AGE_M = NS + "AverageAgeMonthsInformationAboutReportingCompanyInformationAboutEmployees"
E_TENURE_Y = NS + "AverageLengthOfServiceYearsInformationAboutReportingCompanyInformationAboutEmployees"
E_TENURE_M = NS + "AverageLengthOfServiceMonthsInformationAboutReportingCompanyInformationAboutEmployees"
E_SALARY = NS + "AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees"
E_SUBSIDIARY_NAME = NS + "ConsolidatedSubsidiariesMetricsOfConsolidatedSubsidiaries"

# 多様性指標。ReportingCompany / ConsolidatedSubsidiaries の2系統がある
DIVERSITY_SUFFIXES = {
    "female_manager_ratio": "RatioOfFemaleEmployeesInManagerialPositions",
    "female_to_male_wage_ratio_all": "AllEmployeesDifferencesInWagesBetweenMaleAndFemaleEmployees",
    "female_to_male_wage_ratio_regular": "RegularEmployeesDifferencesInWagesBetweenMaleAndFemaleEmployees",
    "female_to_male_wage_ratio_nonregular": "NonRegularEmployeesDifferencesInWagesBetweenMaleAndFemaleEmployees",
}
CHILDCARE_SUBSTR = "RatioOfMaleEmployeesTakingChildcareLeave"
CHILDCARE_ARTICLE_MARK = "Article714Item2"  # 施行規則71条の6第2号方式


class Facts:
    """(要素ID, コンテキストID) → 値 の索引。同じ組が重複しても矛盾がなければ許す。"""

    def __init__(self, rows: list[list[str]]) -> None:
        self.by_key: dict[tuple[str, str], str] = {}
        self.rows = rows
        for r in rows:
            if len(r) < 9:
                continue
            self.by_key.setdefault((r[0], r[2]), r[8])

    def raw(self, element: str, ctx: str) -> str | None:
        v = self.by_key.get((element, ctx))
        return None if v is None or v.strip() in NULL_TOKENS else v.strip()

    def num(self, element: str, ctx: str) -> float | None:
        v = self.raw(element, ctx)
        if v is None:
            return None
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return None

    def int_(self, element: str, ctx: str) -> int | None:
        v = self.num(element, ctx)
        return None if v is None else int(v)


def _int_or_none(s: str | None) -> int | None:
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def load_rows(doc_id: str) -> list[list[str]]:
    path = DOC_CACHE / f"{doc_id}_type5.zip"
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if "asr" in n and n.endswith(".csv"))
        text = z.read(name).decode("utf-16")
    return list(csv.reader(io.StringIO(text), delimiter="\t"))[1:]


def years_and_months(f: Facts, years_elem: str, months_elem: str) -> float | None:
    """「17年」+「7月」形式と「15.1年」形式の両方を受ける（地雷2）。

    平均勤続年数だけでなく平均年齢にも「42歳6か月」形式がある（三菱地所）。
    """
    years = f.num(years_elem, CTX_REPORTING)
    if years is None:
        return None
    months = f.num(months_elem, CTX_REPORTING)
    if months is None:
        return round(years, 2)
    return round(years + months / 12.0, 2)


def childcare_leave(f: Facts, scope_suffix: str, ctx: str) -> tuple[float | None, str | None]:
    """男性育休取得率と、その算出方式（地雷3）。"""
    for r in f.rows:
        if len(r) < 9 or r[2] != ctx:
            continue
        eid = r[0]
        if CHILDCARE_SUBSTR not in eid or not eid.endswith(scope_suffix):
            continue
        if not eid.startswith(NS + "AllEmployees"):
            continue  # 全労働者ベースだけを採る。正規/非正規別は別項目
        val = f.num(eid, ctx)
        method = "71条の6第2号方式（育児目的休暇を含む）" if CHILDCARE_ARTICLE_MARK in eid else "原則方式"
        return val, method
    return None, None


def diversity_block(f: Facts, scope_suffix: str, ctx: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field, core in DIVERSITY_SUFFIXES.items():
        out[field] = f.num(NS + core + scope_suffix, ctx)
    ratio, method = childcare_leave(f, scope_suffix, ctx)
    out["male_childcare_leave_ratio"] = ratio
    out["male_childcare_leave_method"] = method
    return out


def subsidiary_metrics(f: Facts) -> list[dict[str, Any]]:
    """持株会社向け。連結子会社ごとの多様性指標を、子会社名に対応づける（地雷4）。

    Row1Member 等のコンテキストは株主一覧・配当の表でも使い回されるため、
    子会社名の要素が存在するコンテキストだけを対象にする。
    """
    names: dict[str, str] = {}
    for r in f.rows:
        if len(r) >= 9 and r[0] == E_SUBSIDIARY_NAME:
            names[r[2]] = r[8].strip()

    results = []
    for ctx, name in names.items():
        block = diversity_block(f, "MetricsOfConsolidatedSubsidiaries", ctx)
        if any(v is not None for k, v in block.items() if k != "male_childcare_leave_method"):
            results.append({"name": name, **block})
    return results


def extract_year(
    company: dict[str, str], filing: dict[str, str], verified: dict[str, dict[str, str]]
) -> dict[str, Any]:
    """1事業年度ぶんの有報から数値を取る。"""
    f = Facts(load_rows(filing["doc_id"]))
    ver = verified.get(filing["doc_id"], {})

    reporting = diversity_block(f, "MetricsOfReportingCompany", CTX_REPORTING)
    has_reporting = any(
        v is not None for k, v in reporting.items() if k != "male_childcare_leave_method"
    )

    history = {}
    for ctx, back in YEAR_CONTEXTS.items():
        count = f.int_(E_EMPLOYEES, ctx)
        if count is not None:
            history[f"{back}年前" if back else "当期末"] = count

    record: dict[str, Any] = {
        "source": {
            "doc_type": "有価証券報告書",
            "doc_id": filing["doc_id"],
            "period_end": filing["period_end"],
            "submit_datetime": filing["submit_datetime"],
            "description": filing["doc_description"],
        },
        "employees": {
            "consolidated": f.int_(E_EMPLOYEES, CTX_CONSOLIDATED),
            # 有報の表の値。verify_employees.py がPDFから読み、XBRLと突き合わせた結果（地雷6）
            "reporting_company": _int_or_none(ver.get("resolved_employees")),
            "reporting_company_source": ver.get("verdict", ""),
            # 標準タグの値。多くの企業では表の値と同じだが、違う企業では「就業人員数」を指す
            "reporting_company_xbrl_standard_tag": f.int_(E_EMPLOYEES, CTX_REPORTING),
            "consolidated_history": history,
        },
        # 平均年間給与・平均年齢・平均勤続年数はすべて「提出会社（単体）」の数値（地雷1）
        "reporting_company": {
            "average_age_years": years_and_months(f, E_AGE_Y, E_AGE_M),
            "average_tenure_years": years_and_months(f, E_TENURE_Y, E_TENURE_M),
            "average_annual_salary_yen": f.int_(E_SALARY, CTX_REPORTING),
        },
        "diversity": {
            "scope": "提出会社" if has_reporting else None,
            **(reporting if has_reporting else {k: None for k in reporting}),
        },
        "subsidiaries": [],
        "notes": [],
    }
    record["is_holding"] = company["is_holding"] == "yes"

    if not has_reporting:
        subs = subsidiary_metrics(f)
        record["subsidiaries"] = subs
        if subs:
            record["diversity"]["scope"] = "連結子会社（提出会社の指標は有報に記載なし）"
            record["notes"].append(
                "提出会社（持株会社）の多様性指標は有報に記載がないため、連結子会社ごとの値を掲載する。"
            )
        else:
            record["notes"].append("多様性指標は有報に記載なし（非公表）。")

    # 年度比較を壊す注記。verify_employees.py が有報PDFの注記から拾う。
    # 基準変更は給与の推移を、組織再編は従業員数の推移を、それぞれ不連続にする。混ぜない。
    record["basis_change_note"] = ver.get("basis_change_note", "").strip()
    record["reorganization_note"] = ver.get("reorganization_note", "").strip()
    if record["basis_change_note"]:
        record["notes"].append(
            f"この年度に平均年間給与の算定基準が変わった：{record['basis_change_note']}"
        )
    if record["reorganization_note"]:
        record["notes"].append(
            f"この年度に従業員数が組織再編で動いた：{record['reorganization_note']}"
        )

    emp = record["employees"]
    if emp["reporting_company"] is None:
        record["notes"].append("提出会社の従業員数を有報PDFから確定できていない（要確認）。")
    elif emp["reporting_company"] != emp["reporting_company_xbrl_standard_tag"]:
        record["notes"].append(
            f"提出会社の従業員数は有報の表の値 {emp['reporting_company']:,}人。"
            f"XBRLの標準タグは {emp['reporting_company_xbrl_standard_tag']:,}人で、"
            "これは出向者の出入りを調整した就業人員数を指す。平均年間給与の分母は前者。"
        )

    if record["is_holding"]:
        record["notes"].append(
            "提出会社は持株会社。平均年間給与・平均年齢・平均勤続年数は持株会社本体"
            f"（{emp['reporting_company']}人）の数値であり、事業会社のものではない。"
        )

    if record["diversity"]["female_manager_ratio"] is None and has_reporting:
        record["notes"].append("管理職に占める女性労働者の割合は有報に記載なし（非公表）。")

    return record


def build_company(
    company: dict[str, str], filings: list[dict[str, str]], verified: dict[str, dict[str, str]]
) -> dict[str, Any]:
    """1社ぶん。決算期の古い順に years を並べ、最新年度を latest として複製する。"""
    years = [extract_year(company, f, verified) for f in sorted(filings, key=lambda r: r["period_end"])]

    # 平均年間給与の算定基準が変わった年度。ここをまたいだ増減率を「給与の伸び」と呼んではいけない。
    #   三井不動産2025・三菱地所2026 …「基準を従来の就業人員から正社員へ変更」
    salary_breaks = {
        y["source"]["period_end"]: y["basis_change_note"] for y in years if y["basis_change_note"]
    }
    # 従業員数だけが動いた年度（給与の算定基準は変わらない）。
    #   住友不動産2026 …吸収分割 / KDDI2023 …会社分割 / ソニーG2023,2024 …機能移管
    headcount_breaks = {
        y["source"]["period_end"]: y["reorganization_note"] for y in years if y["reorganization_note"]
    }
    return {
        "edinet_code": company["edinet_code"],
        "name": company["name"],
        "sec_code": company["sec_code"],
        "industry": company["industry"],
        "peer_group": company["peer_group"],
        "is_holding": company["is_holding"] == "yes",
        "latest": years[-1],
        "years": years,
        "salary_breaks": salary_breaks,
        "headcount_breaks": headcount_breaks,
        # 断絶をまたぐ増減率は「給与の伸び」ではない。伸び率を出さず、注記を併記する
        "salary_trend_comparable": not salary_breaks,
        "headcount_trend_comparable": not (salary_breaks or headcount_breaks),
        # 推移。値が無い年度は入れない（0で埋めない）
        "trend": {
            "average_annual_salary_yen": {
                y["source"]["period_end"]: y["reporting_company"]["average_annual_salary_yen"]
                for y in years
                if y["reporting_company"]["average_annual_salary_yen"] is not None
            },
            "employees_reporting_company": {
                y["source"]["period_end"]: y["employees"]["reporting_company"]
                for y in years
                if y["employees"]["reporting_company"] is not None
            },
            "female_manager_ratio": {
                y["source"]["period_end"]: y["diversity"]["female_manager_ratio"]
                for y in years
                if y["diversity"]["female_manager_ratio"] is not None
            },
            "female_to_male_wage_ratio_all": {
                y["source"]["period_end"]: y["diversity"]["female_to_male_wage_ratio_all"]
                for y in years
                if y["diversity"]["female_to_male_wage_ratio_all"] is not None
            },
        },
    }


def main(only: list[str]) -> None:
    with (ROOT / "companies.csv").open(encoding="utf-8", newline="") as fh:
        companies = list(csv.DictReader(fh))
    with (ROOT / "data" / "doc_index.csv").open(encoding="utf-8", newline="") as fh:
        by_code: dict[str, list[dict[str, str]]] = {}
        for r in csv.DictReader(fh):
            by_code.setdefault(r["edinet_code"], []).append(r)

    verified_path = ROOT / "data" / "employees_verified.csv"
    if not verified_path.exists():
        raise SystemExit(
            "data/employees_verified.csv がありません。\n"
            "提出会社の従業員数はXBRLから一意に決まらないため、先に有報PDFとの照合が必要です。\n"
            "  python fetch_edinet.py get --types 2\n"
            "  python verify_employees.py"
        )
    with verified_path.open(encoding="utf-8-sig", newline="") as fh:
        verified = {r["doc_id"]: r for r in csv.DictReader(fh)}

    if only:
        companies = [c for c in companies if c["edinet_code"] in set(only)]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    review = []
    for c in companies:
        filings = by_code.get(c["edinet_code"])
        if not filings:
            print(f"  !! {c['name']}: 有報が doc_index.csv にない")
            continue
        rec = build_company(c, filings, verified)
        (OUT_DIR / f"{c['edinet_code']}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        latest = rec["latest"]
        d, rc = latest["diversity"], latest["reporting_company"]
        salary = rec["trend"]["average_annual_salary_yen"]
        first, last = (list(salary.values())[0], list(salary.values())[-1]) if salary else (None, None)
        review.append(
            {
                "name": rec["name"],
                "peer_group": rec["peer_group"],
                "年度数": len(rec["years"]),
                "従業員_連結": latest["employees"]["consolidated"],
                "従業員_提出会社": latest["employees"]["reporting_company"],
                "平均年間給与_円": rc["average_annual_salary_yen"],
                "平均年間給与_5年前": first,
                "5年増減率": (
                    round((last / first - 1) * 100, 1)
                    if first and last and rec["salary_trend_comparable"]
                    else None
                ),
                "給与の基準変更": ", ".join(rec["salary_breaks"]) or "",
                "従業員数の組織再編": ", ".join(rec["headcount_breaks"]) or "",
                "平均年齢": rc["average_age_years"],
                "平均勤続年数": rc["average_tenure_years"],
                "多様性指標のスコープ": d["scope"],
                "女性管理職比率": d["female_manager_ratio"],
                "男性育休取得率": d["male_childcare_leave_ratio"],
                "育休算出方式": d["male_childcare_leave_method"],
                "女性賃金/男性賃金_全労働者": d["female_to_male_wage_ratio_all"],
                "連結子会社の掲載数": len(latest["subsidiaries"]),
                "決算期": latest["source"]["period_end"],
            }
        )
        print(f"  {rec['name']:24s} {len(rec['years'])}年度")

    with REVIEW_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(review[0]))
        w.writeheader()
        w.writerows(review)
    print(f"\n{len(review)}社 → {OUT_DIR}\n照合用の一覧 → {REVIEW_CSV}")


if __name__ == "__main__":
    main(sys.argv[1:])
