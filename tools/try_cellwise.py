"""「提出会社の従業員数」を表のセル位置で取る方式を、既存方式と全件で突き合わせる。

既存方式（テキスト順で最初の整数）は2種類の表で壊れる。

  (A) 多行表  … 商船三井「陸上/海上/合計」、スカイマーク「地上/運航/客室/合計又は平均」
                最初の整数は陸上・地上社員の数であって、提出会社の従業員数ではない
  (B) 転置表  … 本田技研「前事業年度 / 当事業年度 / 増減」
                最初の整数は前期の従業員数

どちらも「平均年間給与の値」と「従業員数の見出し」の**位置関係**で解ける。

  平均年間給与の見出しが給与の値と同じ行にある  → 転置表(B)
      給与の値がその行で何番目かを数え、従業員数の行の同じ番目を採る
  平均年間給与の見出しが給与の値の上にある      → 通常表(A)
      給与と同じ行の数値のうち、従業員数の見出しに横位置がいちばん近いものを採る

列合わせに閾値を使ってはいけない。見出しは左寄せ、値は中央寄せで描かれるため、
三菱商事では見出し中心 x=74.6 に対し値の中心が x=109.7 と35pt離れる。
閾値ではなく「最も近いもの」を採る。

    python tools/try_cellwise.py
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "cache" / "edinet" / "docs"

PURE_INT = re.compile(r"^\d{1,3}(?:,\d{3})*$|^\d{2,7}$")
ROW_TOL = 8.0        # 同じ行とみなす縦のずれ（pt）
MAX_EMPLOYEES = 1_000_000


def cx(r) -> float:
    return (r[0] + r[2]) / 2


def cy(r) -> float:
    return (r[1] + r[3]) / 2


def dist(a, b) -> float:
    return abs(cx(a) - cx(b)) + abs(cy(a) - cy(b))


def ints_in_row(words, y: float, x_min: float, exclude: int | None) -> list:
    """行 y にある「括弧なしの純粋な整数」を左から。臨時従業員数（括弧内）と△は落ちる。"""
    out = []
    for w in words:
        if abs(cy(w) - y) >= ROW_TOL or cx(w) <= x_min:
            continue
        t = w[4].strip()
        if not PURE_INT.match(t):
            continue
        v = int(t.replace(",", ""))
        if v == exclude or not (1 <= v <= MAX_EMPLOYEES):
            continue
        out.append((cx(w), v))
    return sorted(out)


def cellwise(pdf: Path, salary: int) -> tuple[int | None, str]:
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
                    S = min(sal_labels, key=lambda r: dist(r, W))
                    if dist(S, W) > 400:
                        continue

                    if abs(cy(S) - cy(W)) < ROW_TOL:      # 転置表
                        L = min((r for r in emp_labels if cy(r) < cy(S)),
                                key=lambda r: abs(cx(r) - cx(S)), default=None)
                        if L is None:
                            continue
                        row = ints_in_row(words, cy(W), cx(S), exclude=None)
                        k = next((i for i, (x, _) in enumerate(row) if abs(x - cx(W)) < 1.0), None)
                        emp_row = ints_in_row(words, cy(L), cx(L), exclude=None)
                        if k is None or k >= len(emp_row):
                            continue
                        doc.close()
                        return emp_row[k][1], "転置表"

                    # 通常表：従業員数の見出しは平均年間給与の見出しと同じ行にある
                    L = min((r for r in emp_labels if abs(cy(r) - cy(S)) < ROW_TOL and cx(r) < cx(S)),
                            key=lambda r: cx(r), default=None)
                    if L is None:
                        continue
                    row = ints_in_row(words, cy(W), 0.0, exclude=salary)
                    if not row:
                        continue
                    doc.close()
                    return min(row, key=lambda p: abs(p[0] - cx(L)))[1], "通常表"
    finally:
        if not doc.is_closed:
            doc.close()
    return None, ""


def main() -> None:
    rows = list(csv.DictReader((ROOT / "data" / "employees_verified.csv").open(encoding="utf-8-sig")))
    fixed = broke = same = failed = 0
    layouts: dict[str, int] = {}
    for r in rows:
        if not r["salary_yen"]:
            continue
        new, layout = cellwise(DOCS / f"{r['doc_id']}_type2.pdf", int(r["salary_yen"]))
        layouts[layout] = layouts.get(layout, 0) + 1
        old = int(r["pdf_employees"]) if r["pdf_employees"] else None
        std = int(r["xbrl_standard"]) if r["xbrl_standard"] else None
        ext = int(r["xbrl_extension"]) if r["xbrl_extension"] else None

        if new is None:
            failed += 1
            print(f"  読めず  {r['name'][:18]:20s} {r['period_end']}  旧={old} 標準={std}")
            continue
        if new == old:
            same += 1
            continue
        now, before = new in (std, ext), old in (std, ext)
        tag = "改善" if now and not before else ("改悪" if before and not now else "変化")
        fixed += tag == "改善"
        broke += tag == "改悪"
        print(f"  {tag}  {r['name'][:18]:20s} {r['period_end']}  旧={old} 新={new} 標準={std} 拡張={ext} [{layout}]")

    print(f"\n同じ {same} / 改善 {fixed} / 改悪 {broke} / 読めず {failed}")
    print("レイアウト:", layouts)


if __name__ == "__main__":
    main()
