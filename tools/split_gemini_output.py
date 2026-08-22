"""Step2(Gemini)の出力を data/saiyo/{edinet_code}.json に1社1ファイルへ分割する。

使い方:
1. gemini_batches/batch_NN.txt の中身をAI Studio(Gemini 1.5 Pro)に貼り付けて実行
2. 返ってきたJSON配列を gemini_output/batch_NN.json として保存
   （コードブロックの```json ... ```で囲まれたままでも良い。前後の説明文が
   多少混ざっていても、このスクリプトが最初の[から最後の]までを抜き出して読む）
3. 24バッチぶん貯まったら python tools/split_gemini_output.py を実行
   （揃っていないバッチがあっても、貯まっている分だけ処理する）

既存の data/saiyo/{code}.json を上書きするかどうかは Step3 の判断事項なので、
このスクリプトは既存ファイルがあれば無条件に上書きしない（--overwrite を付けた
ときだけ上書き）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IN_DIR = ROOT / "gemini_output"
OUT_DIR = ROOT / "data" / "saiyo"

REQUIRED_KEYS = {"edinet_code", "fetched_at", "target_faculty", "tracks", "notes", "source_urls"}


def extract_json_array(text: str) -> list[dict]:
    # 一部バッチは {"response": "[...]"}という、配列がJSON文字列として
    # 二重エンコードされた形で保存されていた（AI Studio側の出力形式差）。
    # その場合はresponseの中身を取り出してから配列として読む。
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
    ap.add_argument("--overwrite", action="store_true",
                    help="data/saiyo/ に既にあるファイルも上書きする")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    batch_files = sorted(p for p in IN_DIR.glob("*batch_*") if p.is_file())
    if not batch_files:
        print(f"{IN_DIR} に batch_NN を含むファイルが見つかりません。")
        return

    written = 0
    skipped_existing = 0
    skipped_bad_schema = 0

    for bf in batch_files:
        text = bf.read_text(encoding="utf-8")
        try:
            companies = extract_json_array(text)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"[{bf.name}] JSON解析失敗、スキップ: {e}")
            continue

        for c in companies:
            if not REQUIRED_KEYS.issubset(c.keys()):
                missing = REQUIRED_KEYS - c.keys()
                print(f"[{bf.name}] スキーマ不足({missing})のためスキップ: {c.get('edinet_code')}")
                skipped_bad_schema += 1
                continue

            code = c["edinet_code"]
            out_path = OUT_DIR / f"{code}.json"
            if out_path.exists() and not args.overwrite:
                skipped_existing += 1
                continue

            out_path.write_text(
                json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
            written += 1

    print(f"書き出し: {written} 社")
    print(f"既存ファイルありでスキップ: {skipped_existing} 社（--overwrite で上書き可）")
    if skipped_bad_schema:
        print(f"スキーマ不足でスキップ: {skipped_bad_schema} 社")


if __name__ == "__main__":
    main()
