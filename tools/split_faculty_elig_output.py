"""Gemini（AI Studio）が構造化した学部適性を data/saiyo/{code}.json に反映する。

gemini_output/faculty_elig_batch_NN.json（PROMPT_faculty_elig_gemini.md の出力を
ユーザーが保存したもの）を読み、各社の bunkei/rikei_kougaku/rikei_seimei が
true/false/null のどれかであることだけ機械的に検証してから
data/saiyo/{code}.json に target_faculty_eligibility として書き戻す。
値が不正（true/false/null 以外）・対応する data/saiyo/{code}.json が無い場合はスキップして警告する
＝ tools/split_koumu_recruit_output.py と同じ「機械検証してから反映する」原則。

使い方:
    python tools/split_faculty_elig_output.py
    python tools/split_faculty_elig_output.py --dry-run   # 反映せず検証結果だけ表示
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IN_DIR = ROOT / "gemini_output"
SAIYO_DIR = ROOT / "data" / "saiyo"
FIELDS = ("bunkei", "rikei_kougaku", "rikei_seimei")


def extract_json_array(text: str) -> list[dict]:
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            outer = json.loads(stripped)
        except json.JSONDecodeError:
            outer = None
        if isinstance(outer, dict) and isinstance(outer.get("response"), str):
            text = outer["response"]
    start = text.index("[")
    end = text.rindex("]") + 1
    return json.loads(text[start:end])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="反映せず検証結果だけ表示する")
    args = ap.parse_args()

    batch_files = sorted(IN_DIR.glob("faculty_elig_batch_*.json"))
    if not batch_files:
        print(f"{IN_DIR} に faculty_elig_batch_*.json が見つかりません。")
        return

    applied = 0
    skipped_bad_value = 0
    skipped_no_file = 0

    for bf in batch_files:
        text = bf.read_text(encoding="utf-8")
        try:
            items = extract_json_array(text)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"[{bf.name}] JSON解析失敗、スキップ: {e}")
            continue

        for item in items:
            code = item.get("code")
            if not code:
                print(f"[{bf.name}] code が無い要素をスキップ: {item}")
                continue

            elig = {}
            bad = False
            for f in FIELDS:
                v = item.get(f)
                if v is not True and v is not False and v is not None:
                    print(f"[{bf.name}] {code}: {f} の値が不正のためスキップ: {v!r}")
                    bad = True
                    break
                elig[f] = v
            if bad:
                skipped_bad_value += 1
                continue

            saiyo_path = SAIYO_DIR / f"{code}.json"
            if not saiyo_path.exists():
                print(f"[{bf.name}] {code}: data/saiyo/{code}.json が無いためスキップ")
                skipped_no_file += 1
                continue

            saiyo = json.loads(saiyo_path.read_text(encoding="utf-8"))
            saiyo["target_faculty_eligibility"] = elig
            if not args.dry_run:
                saiyo_path.write_text(
                    json.dumps(saiyo, ensure_ascii=False, indent=1), encoding="utf-8"
                )
            applied += 1

    print(f"\n反映: {applied} 社" + ("（--dry-run のため未書き込み）" if args.dry_run else ""))
    if skipped_bad_value:
        print(f"値が不正でスキップ: {skipped_bad_value} 社")
    if skipped_no_file:
        print(f"対応するdata/saiyo/ファイルが無くスキップ: {skipped_no_file} 社")


if __name__ == "__main__":
    main()
