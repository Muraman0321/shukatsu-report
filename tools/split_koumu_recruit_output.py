"""Gemini（AI Studio）が判定した官公庁・政府系機関の採用ページを data/koumu/{slug}.json に反映する。

gemini_output/koumu_recruit_batch_NN.json（PROMPT_koumu_recruit_gemini.md の出力をユーザーが
保存したもの）を読み、各機関の recruit URL が実際にその機関へ渡した候補リンク
（data/links/koumu_icons.json の candidates）のいずれかと一致するか機械的に検証してから、
一致したものだけ data/koumu/{slug}.json に recruit・recruit_text として書き戻す。
一致しない（＝Geminiが候補にないURLを書いた）ものはスキップして警告する
＝ tools/split_gemini_output.py と同じ「機械検証してから反映する」原則。

使い方:
    python tools/split_koumu_recruit_output.py
    python tools/split_koumu_recruit_output.py --dry-run   # 反映せず検証結果だけ表示
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IN_DIR = ROOT / "gemini_output"
ICONS_JSON = ROOT / "data" / "links" / "koumu_icons.json"
KOUMU_DIR = ROOT / "data" / "koumu"


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

    icons = json.loads(ICONS_JSON.read_text(encoding="utf-8")) if ICONS_JSON.exists() else {}

    batch_files = sorted(IN_DIR.glob("koumu_recruit_batch_*.json"))
    if not batch_files:
        print(f"{IN_DIR} に koumu_recruit_batch_*.json が見つかりません。")
        return

    applied = 0
    skipped_no_match = 0
    skipped_no_file = 0
    null_count = 0

    for bf in batch_files:
        text = bf.read_text(encoding="utf-8")
        try:
            items = extract_json_array(text)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"[{bf.name}] JSON解析失敗、スキップ: {e}")
            continue

        for item in items:
            slug = item.get("slug")
            recruit = item.get("recruit")
            recruit_text = item.get("recruit_text")
            if not slug:
                print(f"[{bf.name}] slug が無い要素をスキップ: {item}")
                continue
            if recruit is None:
                null_count += 1
                continue

            candidates = {c["url"] for c in icons.get(slug, {}).get("candidates", [])}
            if recruit not in candidates:
                print(f"[{bf.name}] {slug}: recruit URLが候補に無いためスキップ: {recruit}")
                skipped_no_match += 1
                continue

            koumu_path = KOUMU_DIR / f"{slug}.json"
            if not koumu_path.exists():
                print(f"[{bf.name}] {slug}: data/koumu/{slug}.json が無いためスキップ")
                skipped_no_file += 1
                continue

            entity = json.loads(koumu_path.read_text(encoding="utf-8"))
            entity["recruit"] = recruit
            entity["recruit_text"] = recruit_text
            if not args.dry_run:
                koumu_path.write_text(
                    json.dumps(entity, ensure_ascii=False, indent=1), encoding="utf-8"
                )
            applied += 1

    print(f"\n反映: {applied} 機関" + ("（--dry-run のため未書き込み）" if args.dry_run else ""))
    print(f"該当なし(null): {null_count} 機関")
    if skipped_no_match:
        print(f"候補に無いURLでスキップ: {skipped_no_match} 機関")
    if skipped_no_file:
        print(f"対応するdata/koumu/ファイルが無くスキップ: {skipped_no_file} 機関")


if __name__ == "__main__":
    main()
