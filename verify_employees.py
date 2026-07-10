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

    python verify_employees.py            # 未判定の書類だけを読み、data/employees_verified.csv を更新
    python verify_employees.py --full     # 抽出ロジックを直したときは全件やり直す
"""

from __future__ import annotations

import csv
import io
import re
import sys
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


# 内訳タグの合計が標準タグと一致する会社
# ---------------------------------------------------------------------------
# かんぽ生命保険は「内部管理職員」「営業職員」で従業員数・平均給与とも別々にしか開示せず、
# 合算した「平均年間給与」というXBRLタグ自体が存在しない。給与という検索アンカーが無いので
# PDFの表を探せない。ただし内訳タグ（*InternalStaff / *SalesStaff 等）を合計すると
# 標準タグ（NumberOfEmployees）と6期すべてで完全一致する。これはPDFへのアンカー探索と
# 同じ役目の検算であり、一致するときだけ標準タグを信頼する。一致しなければ推測せず
# human review に回す（内訳の呼び名は会社によって違うので、Internal/Sales以外は拾わない）。
_BREAKDOWN_SUFFIXES = ("InternalStaff", "SalesStaff")


def breakdown_sum(rows: list[list[str]]) -> int | None:
    parts = []
    for r in rows:
        if len(r) < 9 or r[2] != CTX:
            continue
        elem = r[0].split(":")[-1]
        if any(elem.endswith(suf) for suf in _BREAKDOWN_SUFFIXES) and elem.startswith("NumberOfEmployees"):
            v = _int(r[8])
            if v is not None:
                parts.append(v)
    return sum(parts) if len(parts) == len(_BREAKDOWN_SUFFIXES) else None


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


# ---------------------------------------------------------------------------
# 第二の読み方：表のセル位置で読む
#
# 上の pdf_table_employees は「従業員数の見出しから給与の値までの最初の整数」を採る。
# これは1行1社の表を前提にしており、次の2つで壊れる。
#
#   多行表   商船三井「陸上1,046 / 海上387 / 合計1,433」   → 最初の整数は陸上
#            スカイマーク「地上/運航/客室/合計又は平均」    → 最初の整数は地上社員
#   転置表   本田技研（〜2023年3月期）「前事業年度|当事業年度|増減」→ 最初の整数は前期
#
# ただしセル位置方式にも弱点があり、全件に当てると別の7件（本田2024以降・野村HD）で
# 誤る。だから **テキスト方式がXBRLのどちらのタグとも一致しなかったときだけ** 呼ぶ。
# そして一致が得られない限り採用しない。裁定者はPDFのままで、裁定結果が
# 候補のどれでもないなら黙って通さない。
# ---------------------------------------------------------------------------

PURE_INT = re.compile(r"^\d{1,3}(?:,\d{3})*$|^\d{2,7}$")
ROW_TOL = 8.0
MAX_EMPLOYEES = 1_000_000


def _cx(r) -> float:
    return (r[0] + r[2]) / 2


def _cy(r) -> float:
    return (r[1] + r[3]) / 2


def _dist(a, b) -> float:
    return abs(_cx(a) - _cx(b)) + abs(_cy(a) - _cy(b))


def _ints_in_row(words, y: float, x_min: float, exclude: int | None) -> list[tuple[float, int]]:
    """行 y の「括弧のない純粋な整数」を左から。臨時従業員数（括弧内）や△は落ちる。"""
    out = []
    for w in words:
        if abs(_cy(w) - y) >= ROW_TOL or _cx(w) <= x_min:
            continue
        t = w[4].strip()
        if not PURE_INT.match(t):
            continue
        v = int(t.replace(",", ""))
        if v != exclude and 1 <= v <= MAX_EMPLOYEES:
            out.append((_cx(w), v))
    return sorted(out)


def cellwise_employees(pdf: Path, salary: int) -> tuple[int | None, str]:
    doc = fitz.open(pdf)
    try:
        for needle in (f"{salary:,}", f"{salary // 1000:,}"):
            for page in doc:
                sal_vals = page.search_for(needle)
                if not sal_vals:
                    continue
                sal_labels = page.search_for("平均年間給与")
                emp_labels = page.search_for("従業員数")
                if not sal_labels or not emp_labels:
                    continue
                words = page.get_text("words")
                for W in sal_vals:
                    S = min(sal_labels, key=lambda r: _dist(r, W))
                    if _dist(S, W) > 400:
                        continue

                    if abs(_cy(S) - _cy(W)) < ROW_TOL:
                        # 転置表：給与の値がその行で何番目かを数え、従業員数の行の同じ番目を採る
                        L = min((r for r in emp_labels if _cy(r) < _cy(S)),
                                key=lambda r: abs(_cx(r) - _cx(S)), default=None)
                        if L is None:
                            continue
                        row = _ints_in_row(words, _cy(W), _cx(S), None)
                        k = next((i for i, (x, _) in enumerate(row) if abs(x - _cx(W)) < 1.0), None)
                        emp_row = _ints_in_row(words, _cy(L), _cx(L), None)
                        if k is None or k >= len(emp_row):
                            continue
                        return emp_row[k][1], "転置表"

                    # 通常表：給与と同じ行の整数のうち、従業員数の見出しに横位置がいちばん近いもの。
                    # 閾値を使ってはいけない（見出しは左寄せ・値は中央寄せで35pt以上ずれる）。
                    L = min((r for r in emp_labels if abs(_cy(r) - _cy(S)) < ROW_TOL and _cx(r) < _cx(S)),
                            key=lambda r: _cx(r), default=None)
                    if L is None:
                        continue
                    row = _ints_in_row(words, _cy(W), 0.0, salary)
                    if row:
                        return min(row, key=lambda p: abs(p[0] - _cx(L)))[1], "通常表"
    finally:
        doc.close()
    return None, ""


def _existing() -> dict[str, dict[str, str]]:
    """すでに判定済みの書類。docIDごとに1行。有報は一度出れば内容が変わらないので読み直さない。"""
    if not OUT.exists():
        return {}
    with OUT.open(encoding="utf-8-sig", newline="") as f:
        return {r["doc_id"]: r for r in csv.DictReader(f)}


def main() -> None:
    full = "--full" in sys.argv  # 抽出ロジックを直したときは全件やり直す
    with (ROOT / "companies.csv").open(encoding="utf-8", newline="") as f:
        names = {r["edinet_code"]: r["name"] for r in csv.DictReader(f)}
    with (ROOT / "data" / "doc_index.csv").open(encoding="utf-8", newline="") as f:
        filings = [r for r in csv.DictReader(f) if r["edinet_code"] in names]

    done = {} if full else _existing()
    if done:
        print(f"判定済み {len(done)}件はPDFを読み直しません（--full で全件やり直す）")

    out_rows, flagged = [], []
    for filing in sorted(filings, key=lambda r: (r["edinet_code"], r["period_end"])):
        c = {"edinet_code": filing["edinet_code"], "name": names[filing["edinet_code"]]}
        doc_id = filing["doc_id"]
        if doc_id in done:
            row = done[doc_id]
            out_rows.append(row)
            if not (row["resolved_employees"] or "").strip():
                flagged.append(f"{c['name']}({filing['period_end']})")
            continue
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
        #
        # ただし「PDFの値がXBRLのどちらのタグとも一致しない」ときは、PDFの読み方を
        # 間違えている可能性が高い（多行表の小計・転置表の前期列を拾っている）。
        # そのときだけセル位置方式で読み直し、**一致が得られたときだけ**採用する。
        layout = ""
        if pdf_val is not None and pdf_val not in (std, ext) and salary is not None:
            alt, layout = cellwise_employees(pdf_path, salary)
            if alt is not None and alt in (std, ext):
                pdf_val = alt
            else:
                layout = f"再読込も不一致({alt})" if alt is not None else "再読込も読めず"

        bsum = breakdown_sum(rows) if salary is None else None
        if pdf_val is None and salary is None and std is not None and bsum is not None and bsum == std:
            # 平均年間給与が単一値でない会社（かんぽ生命保険は「内部管理職員」「営業職員」に
            # 分かれ、合算した「平均年間給与」という開示自体が無い）。給与という検索アンカーが
            # 無いのでPDFの表を探せない。ただし内訳タグの合計が標準タグと一致するなら、
            # それ自体がPDFアンカーの代わりの検算になる。一致しなければ human review のまま。
            verdict, source, resolved = "XBRL標準タグ（給与非公表のため内訳タグの合計で検算・一致）", "XBRL標準タグ", std
        elif pdf_val is None:
            verdict, source, resolved = "PDFから読めず（要 human review）", "", None
        elif pdf_val == std:
            verdict = "XBRL標準タグと一致" + (f"（セル位置方式・{layout}）" if layout else "")
            source, resolved = "有報PDF（標準タグと一致）", pdf_val
        elif pdf_val == ext:
            verdict = f"XBRL拡張タグと一致({ext_name})" + (f"（セル位置方式・{layout}）" if layout else "")
            source, resolved = "有報PDF（拡張タグと一致）", pdf_val
        else:
            # どの読み方でもXBRLと合わない。推測で埋めず、公開しない。
            verdict, source, resolved = f"要 human review：XBRLと不一致（PDF={pdf_val} {layout}）", "", None

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
