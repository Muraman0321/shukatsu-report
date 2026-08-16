"""週次で走らせる更新ジョブ。新しい有報が出ていれば取り込み、検証が通ったときだけ公開する。

    python update.py            # 確認だけ（新しい有報の有無を報告して終わる）
    python update.py --apply    # 取得・検証・生成まで行う。安全なら push する
    python update.py --apply --no-push   # push しない

なぜ「検証が通ったときだけ」なのか
----------------------------------
有報は各社が年1回しか出さない。決算期は3月がほとんどだが、12月期（キリン・アサヒ・
中外製薬・キヤノン）は3月提出、2月期（セブン&アイ・イオン）は5月提出、8月期
（ファーストリテイリング）は11月提出になる。つまりこのジョブは1年のうち大半の週は
何もしない。何もしない週に壊れる余地はないが、**動く数週にだけ壊れる**。
しかも本人は2027年5月まで海外にいる（その間に上の6社の有報が出る）。

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
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUMMARY = ROOT / "site" / "data" / "companies.json"
LOOKBACK_DAYS = 40          # 週次で回すので30日+予備。EDINETの一覧APIは1日1リクエスト
SALARY_JUMP_LIMIT = 0.40    # 前年比±40%。実際の最大は三菱商事の+24%（2023年3月期）


# 子プロセスの出力をUTF-8で受け取るための環境。**指定しないとWindowsで文字化けする。**
# パイプに書くときPythonは端末ではなくロケール（日本語WindowsではCP932）を使うので、
# UTF-8として読もうとすると日本語の1バイト目で必ず落ちる。落ちるのは出力を読む
# スレッドの中なので、**本体はエラーにならないまま出力だけが消える**。
# 失敗の理由が見えなくなるのがいちばん困る（検証で止まった理由が読めない）。
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def run(*cmd: str) -> str:
    print(f"$ {' '.join(cmd)}", flush=True)
    p = subprocess.run([sys.executable, *cmd], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=CHILD_ENV)
    if p.returncode != 0:
        print((p.stdout or "")[-2000:], (p.stderr or "")[-2000:], sep="\n")
        raise SystemExit(f"失敗: {' '.join(cmd)}")
    return p.stdout or ""


def known_doc_ids() -> set[str]:
    with (ROOT / "data" / "doc_index.csv").open(encoding="utf-8", newline="") as f:
        return {r["doc_id"] for r in csv.DictReader(f)}


def snapshot() -> dict[str, dict]:
    if not SUMMARY.exists():
        return {}
    return {c["code"]: c for c in json.loads(SUMMARY.read_text(encoding="utf-8"))["companies"]}


def latest_on_site() -> dict[str, str]:
    """会社ごとに、いまサイトが持っている最新の決算期を返す。"""
    out: dict[str, str] = {}
    for p in (ROOT / "data" / "companies").glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        periods = sorted(d["trend"]["average_annual_salary_yen"])
        if periods:
            out[d["edinet_code"]] = periods[-1]
    return out


def find_new() -> list[dict]:
    """まだサイトに反映されていない有報を返す。

    **「今回の一覧取得で初めて出てきた書類」で判定してはいけない。**
    以前この判定を使っていて、17社の新しい有報を見落とした。書類一覧を引く
    `find` だけが何かの拍子に先に走ると、doc_index には載るのに取得も抽出も
    されないまま「既知の書類」になり、二度と検出されなくなる。アサヒGHDの
    2025年12月期がその状態で、サイトは1年古い数字を最新として出し続けていた。

    見るべきは索引の差分ではなく、**索引と実データの差**である。
    """
    today = dt.date.today()
    run("fetch_edinet.py", "find",
        "--from", (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat(),
        "--to", today.isoformat(), "--append")
    have = latest_on_site()
    with (ROOT / "data" / "doc_index.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    # 同じ会社の同じ期が複数あれば最後の提出（訂正報告など）を採る
    pending: dict[str, dict] = {}
    for r in rows:
        code, period = r["edinet_code"], r["period_end"]
        if period <= have.get(code, ""):
            continue
        cur = pending.get(code)
        if cur is None or (period, r["submit_datetime"]) > (cur["period_end"], cur["submit_datetime"]):
            pending[code] = r
    return sorted(pending.values(), key=lambda r: r["submit_datetime"])


def verification_gaps(doc_ids: set[str]) -> tuple[list[str], list[str]]:
    """今回取り込んだ書類について、提出会社の従業員数を確定できたかを見る。

    返り値は (公開を止める理由, 注意として報告するだけのもの)。

    **「1件でも読めなければ止める」ではもう回らない。** その判定は対象が27社
    だった頃のもので、全件がPDFから読めていた。1,549社になった今は9,029件中
    348件（3.9%）が読めておらず、その状態のまま公開している。読めなかった場合
    `extract.py` は従業員数を null にし、企業ページに「提出会社の従業員数を
    有報PDFから確定できていない（要確認）」と明記する。**黙って間違えるのでは
    なく、分からないと書いてある。** これは鉄則3のとおりで、止める理由にならない。

    止めるのは、PDFの読み取り自体が壊れた場合である。新しく取り込んだ書類の
    従業員数が1件も確定できなかったときは、個々の会社の事情ではなく
    ライブラリや様式の変更を疑う。
    """
    with (ROOT / "data" / "employees_verified.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["doc_id"] in doc_ids]
    with_salary = [r for r in rows if (r["salary_yen"] or "").strip()]
    unresolved = [r for r in with_salary if not (r["resolved_employees"] or "").strip()]
    warn = [f"{r['name']} {r['period_end']}（平均年収は載せるが従業員数は非公表になる）"
            for r in unresolved]
    # 「1件も確定できない」だけでは不十分。残りが2件のときに、その2件がたまたま
    # 読めない会社（加藤製作所・ヨロズ）だと全損に見えてしまい誤爆した。
    # 壊れたと言うには母数がいる。5件以上あって全滅なら、様式ではなく仕組みを疑う。
    fatal = []
    if len(with_salary) >= 5 and len(unresolved) == len(with_salary):
        fatal = [f"{r['name']} {r['period_end']}" for r in unresolved]
    return fatal, warn


def salary_jumps(had: dict[str, str]) -> list[str]:
    """**今回新しく入った期だけ**を、その1つ前の期と比べる。

    以前は全社の全期を毎回見ていた。対象が27社の頃はそれで足りたが、
    1,549社になると過去の実在する急変（日本製鉄の2023年3月期 +54%、商船三井の
    +41% など、海運・鉄鋼の好況で実際に賞与が跳ねた年）が毎回引っかかり、
    **二度と公開できなくなる**。この関門が守りたいのは「今回の取り込みで
    分母を取り違えていないか」であって、過去の実績値の是非ではない。

    過去分の点検は `verify_employees.py` と `data/financials_review.csv` の役目。
    """
    out = []
    for p in (ROOT / "data" / "companies").glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        if not d["salary_trend_comparable"]:
            continue  # 基準変更のある企業は比較しない（それ自体が既知の不連続）
        prev_latest = had.get(d["edinet_code"], "")
        s = sorted(d["trend"]["average_annual_salary_yen"].items())
        for (pa, a), (pb, b) in zip(s, s[1:]):
            if pb <= prev_latest:
                continue  # 今回より前からサイトに載っていた期
            if a and b and abs(b / a - 1) > SALARY_JUMP_LIMIT:
                out.append(f"{d['name']}: {pa} {a:,} → {pb} {b:,} 円（{(b / a - 1) * 100:+.0f}%）")
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
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"git 失敗: {' '.join(args)}\n{p.stderr}")
    return p.stdout or ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="取得・検証・生成まで行う")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--ci", metavar="OUT", help="新しい有報があればOUTに一覧を書き、終了コード3で抜ける")
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
        # CIでは終了コードで知らせる。原本のキャッシュ（約300MB）はCIに無いので、
        # 取り込み自体は手元で行う。対象27社の有報は135件すべて6月提出なので、
        # 見張りが鳴るのは年に一度だけである。
        if a.ci:
            summary = "\n".join(f"- {r['filer_name']}　{r['period_end']}期　`{r['doc_id']}`" for r in new)
            Path(a.ci).write_text(summary, encoding="utf-8")
            raise SystemExit(3)
        return

    had = latest_on_site()   # 取り込む前にサイトが持っていた最新期。急変の判定に使う
    before = snapshot()
    run("fetch_edinet.py", "get", "--types", "5,2")   # XBRLとPDF。カンマ区切りの1引数
    run("verify_employees.py")

    fatal, warn = verification_gaps({r["doc_id"] for r in new})
    if fatal:
        print("\n【公開を中止】今回取り込んだ書類の従業員数を1件も確定できませんでした:")
        print("\n".join(f"  - {g}" for g in fatal))
        print("個々の会社の事情ではなくPDFの読み取り自体が壊れた疑いがあります。人が確認してください。")
        raise SystemExit(2)
    if warn:
        print(f"\n【注意】従業員数をPDFから確定できなかった書類 {len(warn)}件"
              "（企業ページには「確定できていない」と明記されます）:")
        print("\n".join(f"  - {g}" for g in warn))

    run("extract.py")

    jumps = salary_jumps(had)
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
    print("push 完了。Render が自動デプロイします → https://shukatsu-data.com")


if __name__ == "__main__":
    main()
