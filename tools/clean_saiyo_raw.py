"""fetch_saiyo.py が集めた生テキストから、**同じ会社の複数ページに共通するナビ・フッター文言**を間引く。

双日の例で実測した通り、企業サイトはどのページを取っても同じグローバルナビ・フッターが
数千字ぶん先頭に付いてくる。1社で複数ページ取れた場合だけ、ページ間で完全一致する
20字以上のかたまりを「定型文」とみなして除く。1ページしか取れなかった会社は
比較対象が無いので手を付けない（削りすぎて本文まで消す方が害が大きい）。

内容を書き換えるわけではない——**Task 4（構造化）でこのセッションが読む量を減らすための下ごしらえ**。
判断に迷う場合の正本はあくまで data/saiyo_raw の生テキスト。

使い方: python tools/clean_saiyo_raw.py
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "saiyo_raw"
CLEAN_DIR = ROOT / "data" / "saiyo_clean"
MIN_BLOCK = 20  # これ未満の一致は「たまたま同じ短い言い回し」として残す


def common_blocks(texts: list[str]) -> set[str]:
    """先頭ページを基準に、他の全ページと共通する20字以上のかたまりを集める。"""
    if len(texts) < 2:
        return set()
    common: set[str] = set()
    base = texts[0]
    for other in texts[1:]:
        sm = difflib.SequenceMatcher(None, base, other, autojunk=False)
        for block in sm.get_matching_blocks():
            if block.size >= MIN_BLOCK:
                common.add(base[block.a: block.a + block.size])
    return common


def strip_common(text: str, common: set[str]) -> str:
    for phrase in sorted(common, key=len, reverse=True):  # 長い一致から先に消す（部分一致の巻き添えを防ぐ）
        text = text.replace(phrase, " ")
    return " ".join(text.split())


def main() -> None:
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    n_cleaned = n_skipped = 0
    for f in sorted(RAW_DIR.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        pages = d.get("pages") or []
        if d.get("status") != "ok" or not pages:
            continue
        texts = [p["text"] for p in pages]
        common = common_blocks(texts)
        cleaned_pages = [{"url": p["url"], "text": strip_common(p["text"], common)} for p in pages]
        removed = sum(len(t) for t in texts) - sum(len(p["text"]) for p in cleaned_pages)
        out = {
            "edinet_code": d["edinet_code"], "name": d["name"], "fetched_at": d["fetched_at"],
            "pages": cleaned_pages, "removed_chars": removed,
        }
        (CLEAN_DIR / f.name).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        if removed > 0:
            n_cleaned += 1
        else:
            n_skipped += 1
    print(f"共通文言を間引いた: {n_cleaned}社 / 変化なし（1ページのみ等）: {n_skipped}社")


if __name__ == "__main__":
    main()
