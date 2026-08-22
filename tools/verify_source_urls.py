"""data/saiyo/{code}.json の source_urls が、raw_jobs_data.json の該当会社の
raw_text 内に実在するURLかを機械的に検証する（Geminiの捏造混入チェック）。

問題のあった会社は REVIEW_saiyo_source_url_mismatch.md に一覧として書き出す
（人間が目視確認するまで data/saiyo/ のファイル自体は削除しない）。

使い方: python tools/verify_source_urls.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_JOBS = ROOT / "raw_jobs_data.json"
SAIYO_DIR = ROOT / "data" / "saiyo"
REPORT_PATH = ROOT / "REVIEW_saiyo_source_url_mismatch.md"


def main() -> None:
    raw_jobs = json.loads(RAW_JOBS.read_text(encoding="utf-8"))
    raw_text_by_code = {c["edinet_code"]: c["raw_text"] for c in raw_jobs}

    checked = 0
    ok = 0
    problems: list[dict] = []

    for f in sorted(SAIYO_DIR.glob("*.json")):
        code = f.stem
        if code not in raw_text_by_code:
            continue
        checked += 1
        data = json.loads(f.read_text(encoding="utf-8"))
        raw_text = raw_text_by_code[code]
        bad_urls = [u for u in data.get("source_urls", []) if u not in raw_text]
        if bad_urls:
            real_urls = re.findall(r"--- 出典: (.*?) ---", raw_text)
            problems.append({
                "edinet_code": code,
                "company": next((c["company"] for c in raw_jobs if c["edinet_code"] == code), None),
                "claimed_source_urls": data.get("source_urls", []),
                "bad_urls": bad_urls,
                "actual_urls_in_raw_text": real_urls,
            })
        else:
            ok += 1

    print(f"raw_jobs_data.json対象の会社のうちチェック: {checked} 社")
    print(f"source_urls全て本文内に実在: {ok} 社")
    print(f"問題あり: {len(problems)} 社")

    if problems:
        lines = [
            "# data/saiyo 内、source_urlsが本文に実在しない会社の一覧",
            "",
            f"（{checked}社中{len(problems)}社。tools/verify_source_urls.py で機械検出。"
            "data/saiyo/のファイル自体は削除せず保留中。人間が目視確認して要否を判断すること）",
            "",
        ]
        for p in problems:
            lines.append(f"## {p['edinet_code']} {p['company']}")
            lines.append(f"- 出力に含まれていた本文に無いURL: {p['bad_urls']}")
            lines.append(f"- 出力のsource_urls全体: {p['claimed_source_urls']}")
            lines.append(f"- 本文中に実在するURL一覧: {p['actual_urls_in_raw_text']}")
            lines.append("")
        REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
        print(f"一覧を書き出し: {REPORT_PATH}")


if __name__ == "__main__":
    main()
