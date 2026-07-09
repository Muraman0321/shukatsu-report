"""週次で走らせる更新ジョブ。新しい有報が出ていれば取り込み、検証が通ったときだけ公開する。

    python update.py            # 確認だけ（新しい有報の有無を報告して終わる）
    python update.py --apply    # 取得・検証・生成まで行う。安全なら push する
    python update.py --apply --no-push   # push しない

なぜ「検証が通ったときだけ」なのか
----------------------------------
有報は年1回（6月）しか出ない。つまりこのジョブは1年のうち51週は何もしない。
何もしない週に壊れる余地はないが、**動く1週にだけ壊れる**。しかも本人は海外にいる。

だから、少しでも怪しければ**公開せずに止まる**。止まるのは安全側の失敗である。
間違った平均年収を世界に配るより、1週間サイトが古いほうがはるかにましだ。

止まる条件:
  1. 提出会社の従業員数をPDFから読めなかった書類が1件でもある
  2. 平均年間給与が前年から ±40% を超えて動いた（分母の取り違えの兆候）
  3. 企業が1社でも消えた
いずれかに当たれば、差分を報告して終わる。人間が見るまで公開しない。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUMMARY = ROOT / "site" / "data" / "companies.json"
LOOKBACK_DAYS = 40          # 週次で回すので30日+予備。EDINETの一覧APIは1日1リクエスト
SALARY_JUMP_LIMIT = 0.40    # 前年比±40%。実際の最大は三菱商事の+24%（2023年3月期）


def run(*cmd: str) -> str:
    print(f"$ {' '.join(cmd)}", flush=True)
    p = subprocess.run([sys.executable, *cmd], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        print(p.stdout[-2000:], p.stderr[-2000:], sep="\n")
        raise SystemExit(f"失敗: {' '.join(cmd)}")
    return p.stdout


def known_doc_ids() -> set[str]:
    with (ROOT / "data" / "doc_index.csv").open(encoding="utf-8", newline="") as f:
        return {r["doc_id"] for r in csv.DictReader(f)}


def snapshot() -> dict[str, dict]:
    if not SUMMARY.exists():
        return {}
    return {c["code"]: c for c in json.loads(SUMMARY.read_text(encoding="utf-8"))["companies"]}


def find_new() -> list[dict]:
    """EDINETの書類一覧を引き、対象27社の未取得の有報を返す。"""
    before = known_doc_ids()
    today = dt.date.today()
    run("fetch_edinet.py", "find",
        "--from", (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat(),
        "--to", today.isoformat(), "--append")
    with (ROOT / "data" / "doc_index.csv").open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r["doc_id"] not in before]


def verification_gaps() -> list[str]:
    with (ROOT / "data" / "employees_verified.csv").open(encoding="utf-8-sig", newline="") as f:
        return [
            f"{r['name']} {r['period_end']}"
            for r in csv.DictReader(f)
            if not (r["resolved_employees"] or "").strip()
        ]


def salary_jumps() -> list[str]:
    out = []
    for p in (ROOT / "data" / "companies").glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        if not d["salary_trend_comparable"]:
            continue  # 基準変更のある企業は比較しない（それ自体が既知の不連続）
        s = [v for _, v in sorted(d["trend"]["average_annual_salary_yen"].items())]
        for a, b in zip(s, s[1:]):
            if a and abs(b / a - 1) > SALARY_JUMP_LIMIT:
                out.append(f"{d['name']}: {a:,} → {b:,} 円（{(b / a - 1) * 100:+.0f}%）")
    return out


def diff(before: dict, after: dict) -> tuple[list[str], list[str]]:
    lost = [before[k]["name"] for k in before.keys() - after.keys()]
    changed = []
    for k in before.keys() & after.keys():
        b, a = before[k], after[k]
        if b["period"] != a["period"]:
            changed.append(f"{a['name']}: {b['period']}期 → {a['period']}期  "
                           f"平均年収 {b['salary']:,} → {a['salary']:,} 円")
    return lost, changed


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise SystemExit(f"git 失敗: {' '.join(args)}\n{p.stderr}")
    return p.stdout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="取得・検証・生成まで行う")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()

    new = find_new()
    if not new:
        print("新しい有価証券報告書はありません。何もしません。")
        return

    print(f"\n新しい有報 {len(new)}件:")
    for r in new:
        print(f"  {r['filer_name']}  {r['period_end']}期  {r['doc_id']}")
    if not a.apply:
        print("\n取り込むには --apply を付けて実行してください。")
        return

    before = snapshot()
    run("fetch_edinet.py", "get", "--types", "5", "2")
    run("verify_employees.py")

    gaps = verification_gaps()
    if gaps:
        print("\n【公開を中止】提出会社の従業員数をPDFから読めなかった書類があります:")
        print("\n".join(f"  - {g}" for g in gaps))
        print("平均年間給与の分母が確定しないため、サイトを更新しません。人が確認してください。")
        raise SystemExit(2)

    run("extract.py")

    jumps = salary_jumps()
    if jumps:
        print(f"\n【公開を中止】平均年間給与が前年から±{SALARY_JUMP_LIMIT:.0%}を超えて動いています:")
        print("\n".join(f"  - {j}" for j in jumps))
        print("分母（提出会社の従業員数）の取り違えの疑いがあります。人が確認してください。")
        raise SystemExit(2)

    run("generate.py")
    after = snapshot()
    lost, changed = diff(before, after)
    if lost:
        print(f"\n【公開を中止】企業が消えました: {', '.join(lost)}")
        raise SystemExit(2)

    print("\n更新された企業:")
    print("\n".join(f"  {c}" for c in changed) or "  （数値に変化なし）")

    if not git("status", "--porcelain").strip():
        print("\nコミットする変更がありません。")
        return

    git("add", "-A")
    periods = sorted({r["period_end"][:7] for r in new})
    git("commit", "-m",
        f"有報を{len(new)}件取り込む（{'、'.join(periods)}期）\n\n"
        + "\n".join(f"- {c}" for c in changed)
        + "\n\n検証：全書類で提出会社の従業員数をPDFから確認。前年比の異常なし。\n\n"
        "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>")
    print(f"\nコミット: {git('log', '--oneline', '-1').strip()}")

    if a.no_push:
        print("--no-push のため push しません。")
        return
    git("push", "origin", "main")
    print("push 完了。Render が自動デプロイします → https://shukatsu-report.onrender.com")


if __name__ == "__main__":
    main()
