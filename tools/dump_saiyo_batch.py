"""data/saiyo_pilot.json の順番で、生テキスト（間引き済みがあればそちら）をN社ぶんまとめて
1つのテキストファイルに出す。Task 4（このセッションが読んで構造化する工程）用の下ごしらえ。

各社1500字までに切り、thin/error の会社は本文を出さず「取得できず」とだけ書く
（採用枠は書かない対象なので読む必要が無い）。

使い方:
    python tools/dump_saiyo_batch.py --start 0 --count 10 --out scratchpad/batch1.txt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PER_COMPANY_CHARS = 1500


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pilot", default="data/saiyo_pilot.json")
    args = ap.parse_args()

    pilot = json.loads((ROOT / args.pilot).read_text(encoding="utf-8"))
    companies = pilot["companies"][args.start: args.start + args.count]

    lines = []
    for c in companies:
        code = c["edinet_code"]
        raw_path = ROOT / "data" / "saiyo_raw" / f"{code}.json"
        clean_path = ROOT / "data" / "saiyo_clean" / f"{code}.json"
        lines.append("=" * 70)
        lines.append(f"[{code}] {c['name'].strip()}  ({c['peer_group']})")
        if not raw_path.exists():
            lines.append("(未取得)")
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        if raw.get("status") != "ok":
            lines.append(f"(status={raw.get('status')}: 取得できず。採用枠は書かずリンクのみ表示)")
            continue
        src = json.loads(clean_path.read_text(encoding="utf-8")) if clean_path.exists() else raw
        for p in src["pages"]:
            lines.append(f"--- {p['url']} ---")
            lines.append(p["text"][:PER_COMPANY_CHARS])
        lines.append("")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(companies)}社ぶん → {out_path}")


if __name__ == "__main__":
    main()
