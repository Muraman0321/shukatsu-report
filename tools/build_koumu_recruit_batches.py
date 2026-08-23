"""官公庁・政府系126機関の採用ページ判定を Gemini に依頼するための、バッチ入力テキストを作る。

data/koumu/_master.json（機関名・kind）と data/links/koumu_icons.json（候補リンク、
fetch_koumu_links.py の candidate_links() が集めたもの）を突き合わせ、1バッチ
BATCH_SIZE 機関ぶんを gemini_batches/koumu_recruit_batch_NN.txt として書き出すだけの
下ごしらえスクリプト（実際の判定はしない。tools/dump_saiyo_batch.py と同じ役割）。

候補が0件の機関も「候補なし」として含める（省略しない＝Geminiの出力件数とバッチの
入力機関数が必ず一致するようにし、抜け漏れを機械的に検知できるようにするため）。

使い方:
    python tools/build_koumu_recruit_batches.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MASTER_JSON = ROOT / "data" / "koumu" / "_master.json"
ICONS_JSON = ROOT / "data" / "links" / "koumu_icons.json"
OUT_DIR = ROOT / "gemini_batches"
BATCH_SIZE = 35


def main() -> None:
    master = json.loads(MASTER_JSON.read_text(encoding="utf-8"))
    icons = json.loads(ICONS_JSON.read_text(encoding="utf-8")) if ICONS_JSON.exists() else {}
    entities = master["entities"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    batches = [entities[i:i + BATCH_SIZE] for i in range(0, len(entities), BATCH_SIZE)]

    total_with_candidates = 0
    for bi, batch in enumerate(batches, 1):
        lines = []
        for ent in batch:
            slug = ent["slug"]
            rec = icons.get(slug, {})
            candidates = rec.get("candidates") or []
            if candidates:
                total_with_candidates += 1
            lines.append("=" * 70)
            lines.append(f"[{slug}] {ent['name']}（{ent['kind']}）")
            if not candidates:
                lines.append("(候補なし)")
                continue
            for c in candidates:
                lines.append(f"- text=\"{c['text']}\"  url={c['url']}")
        out_path = OUT_DIR / f"koumu_recruit_batch_{bi:02d}.txt"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"{out_path}  ({len(batch)}機関)")

    print(f"\n{len(batches)}バッチに分割（1バッチ最大{BATCH_SIZE}機関、"
          f"候補ありの機関 {total_with_candidates}/{len(entities)}）")


if __name__ == "__main__":
    main()
