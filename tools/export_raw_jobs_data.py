"""採用枠パイプライン Step1 成果物：既存クロール済みデータを Gemini 1.5 Pro 投入用にまとめる。

data/saiyo_pilot.json（対象企業リスト）を正本とし、各社 data/saiyo_clean/{code}.json
（無ければ data/saiyo_raw/{code}.json にフォールバック）の本文を連結して raw_jobs_data.json
を作る。ステータスが error の会社（本文が実質空）は除外する。

文字数では切り詰めない（Gemini 1.5 Pro は文脈窓が大きいので全文をそのまま渡す）。
ページ境界には「--- 出典: <URL> ---」を挟み、Step2（Gemini）が抽出結果に source_urls を
持たせられるようにする（本プロジェクトの「捏造しない・出典は実在するURLのみ」の原則を維持）。

使い方: python tools/export_raw_jobs_data.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PILOT_JSON = ROOT / "data" / "saiyo_pilot.json"
CLEAN_DIR = ROOT / "data" / "saiyo_clean"
RAW_DIR = ROOT / "data" / "saiyo_raw"
OUT_PATH = ROOT / "raw_jobs_data.json"


def build_raw_text(pages: list[dict]) -> str:
    parts = []
    for p in pages:
        url = p.get("url", "")
        text = p.get("text", "")
        parts.append(f"--- 出典: {url} ---\n{text}")
    return "\n\n".join(parts)


def main() -> None:
    pilot = json.loads(PILOT_JSON.read_text(encoding="utf-8"))
    companies = pilot["companies"]

    out = []
    skipped_error = 0
    skipped_no_data = 0

    for c in companies:
        code = c["edinet_code"]
        raw_path = RAW_DIR / f"{code}.json"
        if not raw_path.exists():
            skipped_no_data += 1
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        if raw.get("status") not in ("ok", "thin"):
            skipped_error += 1
            continue

        clean_path = CLEAN_DIR / f"{code}.json"
        if clean_path.exists():
            src = json.loads(clean_path.read_text(encoding="utf-8"))
        else:
            src = raw

        pages = src.get("pages", [])
        raw_text = build_raw_text(pages)
        if not raw_text.strip():
            skipped_no_data += 1
            continue

        out.append({
            "company": c["name"].strip(),
            "edinet_code": code,
            "peer_group": c.get("peer_group"),
            "employees": c.get("employees"),
            "is_holding": c.get("is_holding", False),
            "url": c.get("recruit_url") or c.get("official_url"),
            "fetched_at": src.get("fetched_at") or raw.get("fetched_at"),
            "raw_text": raw_text,
        })

    OUT_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    total_chars = sum(len(c["raw_text"]) for c in out)
    print(f"対象 {len(companies)} 社中、書き出し {len(out)} 社"
          f"（error除外 {skipped_error}、本文空/データ無しで除外 {skipped_no_data}）")
    print(f"合計文字数: {total_chars:,} 字")
    print(f"出力: {OUT_PATH}")


if __name__ == "__main__":
    main()
