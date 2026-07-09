"""有報の「事業の内容」をClaudeに要約させ、data/prose/{EDINETコード}.json に置く。

    python write_prose.py --submit     # Batch API に投げる（50%オフ）。バッチIDを保存
    python write_prose.py --collect    # 結果を回収して検証し、data/prose/ に書く
    python write_prose.py --status     # 進捗を見る

このスクリプトを一度も動かさなくても、サイトは完全に成立する。
売っているのは数字の並びであって文章ではない。文章は装飾である。

**Claudeに数字を書かせない**
------------------------------
1. プロンプトで数字の出力を禁じる
2. 出力に半角・全角の数字が1文字でも混じっていたら**機械的に捨てる**（--collect の検証）
3. 生成元は有報の「事業の内容」の本文だけ。Claudeの記憶から書かせない（幻覚を防ぐ）

なぜ要約させるのか。数値は表が語れるが、「この会社は何で食っているのか」は
表では語れない。そこだけをClaudeにやらせる。原文はEDINETのPDL1.0なので
再配布・加工とも認められている（出典と加工の主体はページに明記済み）。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

import fitz
from anthropic import Anthropic
from anthropic.types.messages.batch_create_params import Request
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "cache" / "edinet" / "docs"
PROSE = ROOT / "data" / "prose"
STATE = ROOT / "data" / "prose_batch.json"

MODEL = "claude-opus-4-8"
MAX_TOKENS = 700

# 有報の「３【事業の内容】」から「４【関係会社の状況】」まで。全角・半角の両方に当たる。
HEAD_RE = re.compile(r"[3３]\s*[【\[]\s*事\s*業\s*の\s*内\s*容\s*[】\]]")
END_RE = re.compile(r"[4４]\s*[【\[]\s*関\s*係\s*会\s*社\s*の\s*状\s*況\s*[】\]]")

# 検証：数字（半角・全角）と、数値を含みがちな単位。1文字でも当たれば捨てる。
DIGIT_RE = re.compile(r"[0-9０-９]")

SYSTEM = """あなたは有価証券報告書の「事業の内容」を、就活生に向けて要約する編集者です。

厳守すること:
1. **数字を一切書かない。** 半角も全角も、社数・年度・金額・比率・順位、すべて書かない。
   「連結子会社39社」→「多数の連結子会社」。「2026年3月期」→ 書かない。
2. 与えられた原文に書かれていない事実を足さない。あなたの記憶から補わない。
   原文に無ければ書かない。推測しない。
3. 原文の文をそのまま写さない。自分の言葉で言い直す。
4. 250〜350字の日本語。「〜です・ます」調。見出しや箇条書きを使わず、地の文で書く。
5. 就活生が知りたいのは「この会社は何で稼いでいるのか」「どんな仕事があるのか」です。
   セグメントの名前と、それぞれが何をしているかを、その順で書いてください。

出力は要約の本文だけ。前置きも後書きも書かない。"""

USER = """次は{name}の有価証券報告書「事業の内容」の本文です。これを要約してください。

--- 原文ここから ---
{body}
--- 原文ここまで ---

数字は一切書かないでください。"""


def business_section(pdf: Path) -> str:
    """目次側のヒットを避けるため、見出しごとに切り出して最長のものを採る。"""
    doc = fitz.open(pdf)
    text = "\n".join(p.get_text() for p in doc)
    doc.close()
    best = ""
    for m in HEAD_RE.finditer(text):
        tail = text[m.end():]
        e = END_RE.search(tail)
        cand = tail[: e.start()] if e else tail[:8000]
        if len(cand) > len(best):
            best = cand
    return re.sub(r"[ \t　]+", "", best).strip()[:6000]


def targets() -> list[dict]:
    with (ROOT / "companies.csv").open(encoding="utf-8", newline="") as f:
        names = {r["edinet_code"]: r["name"] for r in csv.DictReader(f)}
    with (ROOT / "data" / "doc_index.csv").open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["edinet_code"] in names]
    latest: dict[str, dict] = {}
    for r in rows:
        cur = latest.get(r["edinet_code"])
        if cur is None or r["period_end"] > cur["period_end"]:
            latest[r["edinet_code"]] = r
    out = []
    for code, r in latest.items():
        pdf = DOCS / f"{r['doc_id']}_type2.pdf"
        if not pdf.exists():
            print(f"  skip {names[code]}: PDF未取得")
            continue
        body = business_section(pdf)
        if len(body) < 200:
            print(f"  skip {names[code]}: 「事業の内容」が短すぎる（{len(body)}字）")
            continue
        out.append({"code": code, "name": names[code], "body": body})
    return out


def client() -> Anthropic:
    load_dotenv(ROOT / ".env")
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        sys.exit(
            "ANTHROPIC_API_KEY が .env にありません。\n"
            "  https://console.anthropic.com/settings/keys で発行し、.env に1行足してください:\n"
            "    ANTHROPIC_API_KEY=sk-ant-...\n"
            "  先に Console の Spend limit を月$33（≒5,000円）に設定しておくこと。"
        )
    return Anthropic(api_key=key)


def submit() -> None:
    items = targets()
    if not items:
        sys.exit("対象が無い")
    reqs = [
        Request(
            custom_id=it["code"],
            params=MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM,
                messages=[{"role": "user", "content": USER.format(name=it["name"], body=it["body"])}],
            ),
        )
        for it in items
    ]
    batch = client().messages.batches.create(requests=reqs)
    STATE.write_text(json.dumps({"batch_id": batch.id, "count": len(reqs)}, indent=1), encoding="utf-8")
    print(f"{len(reqs)}件を投入 → batch {batch.id}")
    print("結果は最大24時間以内。`python write_prose.py --collect` で回収する。")


def status() -> None:
    b = client().messages.batches.retrieve(json.loads(STATE.read_text())["batch_id"])
    print(b.processing_status, b.request_counts)


def collect() -> None:
    batch_id = json.loads(STATE.read_text(encoding="utf-8"))["batch_id"]
    c = client()
    b = c.messages.batches.retrieve(batch_id)
    if b.processing_status != "ended":
        sys.exit(f"まだ終わっていない: {b.processing_status} {b.request_counts}")

    PROSE.mkdir(parents=True, exist_ok=True)
    ok = rejected = failed = 0
    for r in c.messages.batches.results(batch_id):
        code = r.custom_id
        if r.result.type != "succeeded":
            failed += 1
            print(f"  失敗 {code}: {r.result.type}")
            continue
        text = "".join(b.text for b in r.result.message.content if b.type == "text").strip()

        # ここが要。数字が1文字でもあれば採用しない。プロンプトは破られうるが、この検証は破られない。
        if DIGIT_RE.search(text):
            rejected += 1
            found = "".join(sorted(set(DIGIT_RE.findall(text))))
            print(f"  却下 {code}: 数字が混入（{found}）")
            continue
        if not (150 <= len(text) <= 600):
            rejected += 1
            print(f"  却下 {code}: 長さ {len(text)}字")
            continue

        (PROSE / f"{code}.json").write_text(
            json.dumps({"business": text, "model": MODEL, "source": "有価証券報告書「事業の内容」"},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        ok += 1

    print(f"\n採用 {ok} / 却下 {rejected} / 失敗 {failed}")
    print("`python generate.py` で本文に差し込まれる。却下された企業は文章なしで出る（数字の表は出る）。")


def main() -> None:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--submit", action="store_true")
    g.add_argument("--collect", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--dry-run", action="store_true", help="APIを呼ばず、抽出した原文の長さだけ見る")
    a = p.parse_args()
    if a.dry_run:
        for it in targets():
            print(f"{it['name'][:20]:22s} {len(it['body']):5d}字")
    elif a.submit:
        submit()
    elif a.collect:
        collect()
    else:
        status()


if __name__ == "__main__":
    main()
