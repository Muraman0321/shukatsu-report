"""「提出会社の従業員数」を有報PDFから読み、XBRLの候補と突き合わせる。

なぜ必要か
----------
XBRLの標準タグ jpcrp_cor:NumberOfEmployees @NonConsolidatedMember は、
**有報の「(2) 従業員の状況 ②提出会社の状況」の表に載る従業員数とは限らない。**

  伊藤忠商事   表 4,125 = 標準タグ    （拡張タグ 3,078 は就業人員数）
  三井不動産   表 1,981 = 標準タグ    （拡張タグ 2,209 は就業人数）
  三菱商事     表 5,328 = 拡張タグ    （標準タグ 4,456 は就業人員数）
  三菱地所     表 1,729 = 拡張タグ    （標準タグ 1,286 は就業人員数）
  SOMPO HD     表   489 = 拡張タグ    （標準タグ   497）

どちらが表の値かを決める構造的なルールはない（セグメント合計と一致するのが
標準タグの企業もあれば拡張タグの企業もある）。そして **平均年間給与の分母は
表の従業員数** なので、取り違えると「◯人で平均年収△円」が事実として狂う。

そこで有報PDFを正本とし、平均年間給与の値をアンカーにして表を読む。
XBRLは照合相手にすぎない。一致しなければ human review に回す。

    python verify_employees.py            # 27社を判定し data/employees_verified.csv を書く
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "cache" / "edinet" / "docs"
OUT = ROOT / "data" / "employees_verified.csv"

STD_ELEM = "jpcrp_cor:NumberOfEmployees"
CTX = "CurrentYearInstant_NonConsolidatedMember"
SALARY_ELEM = "jpcrp_cor:AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees"

# 拡張タグのうち従業員数を表すもの（金額タグを拾わないよう名前を絞る）
EXT_EMPLOYEE_RE = re.compile(r":(NumberOfEmployees\w*|Employees\w*)$")
EXT_EXCLUDE_RE = re.compile(r"Provision|Loans|Receivable|Payable|Award|Bonus|Liabilit", re.I)

# 括弧内（臨時従業員など）を除いた整数トークン
INT_TOKEN_RE = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d{2,7})(?![\d,.])")
BRACKETED_RE = re.compile(r"[\[\［(（][^\]\］)）]*[\]\］)）]")


def xbrl_rows(doc_id: str) -> list[list[str]]:
    with zipfile.ZipFile(DOCS / f"{doc_id}_type5.zip") as z:
        name = next(n for n in z.namelist() if "asr" in n and n.endswith(".csv"))
        return list(csv.reader(io.StringIO(z.read(name).decode("utf-16")), delimiter="\t"))[1:]


def xbrl_candidates(rows: list[list[str]]) -> tuple[int | None, int | None, str, int | None]:
    std = ext = salary = None
    ext_name = ""
    for r in rows:
        if len(r) < 9 or r[2] != CTX:
            continue
        if r[0] == STD_ELEM:
            std = _int(r[8])
        elif r[0] == SALARY_ELEM:
            salary = _int(r[8])
    # 拡張タグは要素名もコンテキストも企業次第。双日は CurrentYearDuration_NonConsolidatedMember に
    # EmployeesIncludingExpatriates として置いている（Instant だけを見ると取り逃す）。
    for r in rows:
        if len(r) < 9 or not r[2].endswith("_NonConsolidatedMember"):
            continue
        if not r[2].startswith("CurrentYear"):
            continue
        if not (r[0].startswith("jpcrp030000-asr_") and EXT_EMPLOYEE_RE.search(r[0])):
            continue
        if EXT_EXCLUDE_RE.search(r[0]):
            continue
        v = _int(r[8])
        if v is not None and 1 <= v <= 1_000_000:
            ext, ext_name = v, r[0].split(":")[1]
    return std, ext, ext_name, salary


def _int(s: str) -> int | None:
    try:
        return int(s.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


# 年度間の比較を壊す注記。2種類あり、壊すものが違う。混同してはいけない。
#
# (a) 基準変更 … 平均年間給与そのものの算定対象が変わる。**給与の推移が語れなくなる**
#     三菱地所2026「従業員数・平均年齢・平均勤続年数・平均年間給与の基準を従来の就業人員から正社員…としております」
#     三井不動産2025「従業員数、平均年齢、平均勤続年数および平均年間給与の基準を従来の就業人員から正社員へ変更」
#
# (b) 組織再編 … 従業員数が動いた理由。給与の算定基準は変わらない。**人数の推移だけが不連続になる**
#     住友不動産2026「…吸収分割により承継したことによるものであります」
#     KDDI2023「店舗販売支援事業を会社分割の方法により…承継させた」
#     ソニーG2023「グループ会社間の機能移管により、前年度末に比べ394名減少し」
BASIS_CHANGE_PATTERNS = [
    r"平均年間給与[^。]{0,60}基準[^。]{0,40}変更",
    r"基準を[^。]{0,60}(?:正社員|就業人員)[^。]{0,40}(?:変更|としております)",
]
REORGANIZATION_PATTERNS = [r"吸収分割", r"会社分割", r"合併により", r"組織再編", r"機能移管", r"承継"]


def _sentence_around(tail: str, m: re.Match) -> str:
    start = max(0, tail.rfind("。", 0, m.start()) + 1)
    end = tail.find("。", m.end())
    return tail[start : end + 1 if end > 0 else m.end() + 60]


def change_notes(pdf: Path, salary: int) -> tuple[str, str]:
    """従業員の状況の注記から (基準変更, 組織再編) の該当文を拾う。無ければ空文字。"""
    doc = fitz.open(pdf)
    for needle in (f"{salary:,}", f"{salary // 1000:,}"):
        for page in doc:
            text = page.get_text()
            pos = text.find(needle)
            if pos < 0 or "平均年間給与" not in text[max(0, pos - 800) : pos]:
                continue
            tail = re.sub(r"\s+", "", text[pos : pos + 1200])
            basis = next(
                (_sentence_around(tail, m) for p in BASIS_CHANGE_PATTERNS if (m := re.search(p, tail))), ""
            )
            reorg = next(
                (_sentence_around(tail, m) for p in REORGANIZATION_PATTERNS if (m := re.search(p, tail))), ""
            )
            return basis, reorg
    return "", ""


def pdf_table_employees(pdf: Path, salary: int) -> tuple[int | None, str]:
    """平均年間給与の値をアンカーに、同じ表の従業員数を読む。

    表の並びは 従業員数 / 平均年齢 / 平均勤続年数 / 平均年間給与 / 増減率。
    直前の「従業員数」見出しから給与の値までを切り出し、括弧内（臨時従業員）を
    落としたうえで最初の整数を採る。

    切り出した範囲に「平均年間給与」という見出しが含まれることを必須にする。
    これが無いと、1ページ目の「主要な経営指標等の推移」にある連結従業員数を
    誤って拾う（三菱電機で実際に起きた。千円表記のフォールバック needle が
    経営指標表の別の数字に一致した）。

    日付は「従業員数」見出しより前にあるので切り出し範囲に入らない。
    年号らしき整数を除外してはならない——1,981人（三井不動産）や
    2,021人（住友不動産）が消える。
    """
    yen = f"{salary:,}"
    thousand_yen = f"{salary // 1000:,}"  # 有報が「平均年間給与（千円）」で表記する場合
    doc = fitz.open(pdf)
    for needle in (yen, thousand_yen):
        for page in doc:
            text = page.get_text()
            start = 0
            while (pos := text.find(needle, start)) >= 0:
                start = pos + 1
                head = text.rfind("従業員数", 0, pos)
                if head < 0:
                    continue
                block = text[head:pos]
                if "平均年間給与" not in block:
                    continue  # 提出会社の従業員表ではない
                ints = [
                    int(m.group(1).replace(",", ""))
                    for m in INT_TOKEN_RE.finditer(BRACKETED_RE.sub(" ", block))
                ]
                ints = [v for v in ints if v != salary]
                if ints:
                    return ints[0], needle
    return None, ""


def main() -> None:
    with (ROOT / "companies.csv").open(encoding="utf-8", newline="") as f:
        names = {r["edinet_code"]: r["name"] for r in csv.DictReader(f)}
    with (ROOT / "data" / "doc_index.csv").open(encoding="utf-8", newline="") as f:
        filings = [r for r in csv.DictReader(f) if r["edinet_code"] in names]

    out_rows, flagged = [], []
    for filing in sorted(filings, key=lambda r: (r["edinet_code"], r["period_end"])):
        c = {"edinet_code": filing["edinet_code"], "name": names[filing["edinet_code"]]}
        doc_id = filing["doc_id"]
        if not (DOCS / f"{doc_id}_type5.zip").exists() or not (DOCS / f"{doc_id}_type2.pdf").exists():
            print(f"  skip {c['name']} {filing['period_end']}: CSVかPDFが未取得")
            continue
        rows = xbrl_rows(doc_id)
        std, ext, ext_name, salary = xbrl_candidates(rows)
        pdf_path = DOCS / f"{doc_id}_type2.pdf"
        pdf_val, _ = (None, "") if salary is None else pdf_table_employees(pdf_path, salary)
        basis_note, reorg_note = ("", "") if salary is None else change_notes(pdf_path, salary)

        # 有報PDFの表が正本。XBRLはどの要素・どのコンテキストに置くかが企業ごとに違うため、
        # 照合相手として使うにとどめる。
        resolved = pdf_val
        if pdf_val is None:
            verdict, source = "PDFから読めず（要 human review）", ""
        elif pdf_val == std:
            verdict, source = "XBRL標準タグと一致", "有報PDF（標準タグと一致）"
        elif pdf_val == ext:
            verdict, source = f"XBRL拡張タグと一致({ext_name})", "有報PDF（拡張タグと一致）"
        else:
            verdict, source = "XBRLのどのタグとも不一致", "有報PDF"

        if resolved is None:
            flagged.append(f"{c['name']}({filing['period_end']})")

        out_rows.append(
            {
                "edinet_code": c["edinet_code"],
                "name": c["name"],
                "period_end": filing["period_end"],
                "doc_id": doc_id,
                "pdf_employees": pdf_val,
                "xbrl_standard": std,
                "xbrl_extension": ext,
                "extension_element": ext_name,
                "salary_yen": salary,
                "verdict": verdict,
                "resolved_employees": resolved,
                "resolved_source": source,
                "basis_change_note": basis_note,
                "reorganization_note": reorg_note,
            }
        )
        print(f"  {verdict[:20]:22s} {c['name'][:20]:22s} {filing['period_end']}  PDF={pdf_val}  標準={std}")

    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        w.writeheader()
        w.writerows(out_rows)

    ok = sum(1 for r in out_rows if r["resolved_employees"] is not None)
    print(f"\n{ok}/{len(out_rows)} 社で確定 → {OUT}")
    if flagged:
        print("要 human review:", ", ".join(flagged))


if __name__ == "__main__":
    main()
