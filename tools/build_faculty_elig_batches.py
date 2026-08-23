"""就活の軸診断の学部フィルタ用に、data/saiyo/*.json の target_faculty（採用ページ原文の
自由記述）を Gemini に構造化してもらうための、バッチ入力テキストを作る。

対象は target_faculty を持つ会社だけ。1バッチ BATCH_SIZE 社ぶんを
gemini_batches/faculty_elig_batch_NN.txt として書き出すだけの下ごしらえスクリプト
（実際の判定はしない。tools/build_koumu_recruit_batches.py と同じ役割）。

使い方:
    python tools/build_faculty_elig_batches.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAIYO_DIR = ROOT / "data" / "saiyo"
COMPANIES_DIR = ROOT / "data" / "companies"
OUT_DIR = ROOT / "gemini_batches"
BATCH_SIZE = 40


def main() -> None:
    entries = []
    for sp in sorted(SAIYO_DIR.glob("*.json")):
        saiyo = json.loads(sp.read_text(encoding="utf-8"))
        target_faculty = saiyo.get("target_faculty")
        if not target_faculty:
            continue
        code = sp.stem
        cp = COMPANIES_DIR / f"{code}.json"
        name = json.loads(cp.read_text(encoding="utf-8"))["name"] if cp.exists() else code
        entries.append((code, name, target_faculty))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    batches = [entries[i:i + BATCH_SIZE] for i in range(0, len(entries), BATCH_SIZE)]

    for bi, batch in enumerate(batches, 1):
        lines = []
        for code, name, target_faculty in batch:
            lines.append("=" * 70)
            lines.append(f"[{code}] {name}")
            lines.append(f"target_faculty: {target_faculty}")
        out_path = OUT_DIR / f"faculty_elig_batch_{bi:02d}.txt"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"{out_path}  ({len(batch)}社)")

    print(f"\n{len(batches)}バッチに分割（1バッチ最大{BATCH_SIZE}社、対象 {len(entries)}社）")


if __name__ == "__main__":
    main()
