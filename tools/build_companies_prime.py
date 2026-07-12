"""JPX「東証上場銘柄一覧」とEDINETコードリストを突合し、東証プライム全銘柄を
companies.csv に追記する。

既存154社（手作業で調べた peer_group・note を含む）は一切上書きしない。
新規に追加する企業だけを、機械的に判定できる範囲で埋める。

突合の考え方:
    JPX data_j.xls の「コード」（4桁）+ "0" = EDINETコードリストの「証券コード」（5桁）
    一致した行の EDINETコード・提出者名をそのまま使う（表記ゆれを避けるため
    企業名は JPX の銘柄名ではなく EDINET の提出者名を正とする）

industry 列は JPX の33業種区分をそのまま使うが、既存データとの互換のために
2つだけ手を加える（companies.csv の既存154社で確認済みの命名規則）:
    「証券、商品先物取引業」 → 「証券業」
    「電気・ガス業」        → 会社名に「瓦斯」を含むものだけ「ガス業」に分離

is_holding は名前パターンで機械判定する。ソニーグループ・ＮＴＴ・イオンのように
社名にホールディングス的な語を含まない持株会社は拾えない。この場合、
generate.py 側の持株会社バナーが出ないだけで、数値そのものは正しい
（掲載する平均年収・従業員数は常に「提出会社」の値であり、is_holding の
判定精度は注記の有無にしか影響しない）。

使い方:
    python tools/build_companies_prime.py            # 差分をレポートするだけ
    python tools/build_companies_prime.py --apply     # companies.csv に追記する
    python tools/build_companies_prime.py --apply --only-industry 銀行業   # パイロット用
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
COMPANIES_CSV = ROOT / "companies.csv"
EDINETCODE_CSV = ROOT / "cache" / "edinetcode" / "EdinetcodeDlInfo.csv"
JPX_CACHE_DIR = ROOT / "cache" / "jpx"
LOG_PATH = ROOT / "logs" / "jpx_access.log"

JPX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
USER_AGENT = "shukatsu-report-bot/0.1 (+research use; contact: CONTACT_EMAIL)"

# JPXの33業種区分 → companies.csv の industry 表記への変換（既存154社の命名規則に合わせる）
INDUSTRY_RENAME = {
    "証券、商品先物取引業": "証券業",
}

# industry確定後のデフォルト peer_group（既存154社から機械的に導出した対応表）
INDUSTRY_TO_PEER_GROUP = {
    "その他製品": "その他製品", "その他金融業": "その他金融", "ガス業": "ガス業",
    "ガラス・土石製品": "ガラス・土石", "ゴム製品": "ゴム製品", "サービス業": "サービス業",
    "パルプ・紙": "パルプ・紙", "不動産業": "不動産業", "保険業": "保険業",
    "倉庫・運輸関連業": "倉庫運輸", "化学": "化学", "医薬品": "医薬品",
    "卸売業": "卸売業", "小売業": "小売", "建設業": "建設業",
    "情報・通信業": "情報・通信業", "機械": "機械", "水産・農林業": "水産農林",
    "海運業": "海運", "石油・石炭製品": "石油・石炭", "空運業": "空運",
    "精密機器": "精密機器", "繊維製品": "繊維製品", "証券業": "証券",
    "輸送用機器": "輸送用機器", "金属製品": "金属製品", "鉄鋼": "鉄鋼",
    "鉱業": "鉱業", "銀行業": "銀行業", "陸運業": "陸運", "電気・ガス業": "電力",
    "電気機器": "電気機器", "非鉄金属": "非鉄金属", "食料品": "食料品",
}

HOLDING_PATTERNS = [
    "ホールディングス", "ホールディング", "ＨＤ", "持株会社", "持株",
    "フィナンシャルグループ", "フィナンシャル・グループ",
]


def _log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"{stamp}\t{line}\n")


def _load_contact_email() -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("CONTACT_EMAIL="):
                return line.split("=", 1)[1].strip()
    return "unknown"


def fetch_jpx_list() -> Path:
    """data_j.xls を取得する。当日ぶんのキャッシュがあれば再取得しない。"""
    JPX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    cached = JPX_CACHE_DIR / f"data_j_{today}.xls"
    if cached.exists():
        _log(f"CACHE\t{cached.name}")
        return cached

    ua = USER_AGENT.replace("CONTACT_EMAIL", _load_contact_email())
    # robots.txt 確認済み（jpx.co.jp は Disallow なし、2026-07-11確認）
    resp = requests.get(JPX_URL, headers={"User-Agent": ua}, timeout=30)
    resp.raise_for_status()
    cached.write_bytes(resp.content)
    _log(f"GET\t{JPX_URL}\thttp={resp.status_code}\tbytes={len(resp.content)}")
    print(f"  取得: {cached.name} ({len(resp.content):,} bytes)")
    return cached


def load_prime_list() -> pd.DataFrame:
    path = fetch_jpx_list()
    df = pd.read_excel(path, dtype={"コード": str})
    prime = df[df["市場・商品区分"] == "プライム（内国株式）"].copy()
    prime["sec_code"] = prime["コード"].str.strip() + "0"
    return prime[["sec_code", "銘柄名", "33業種区分"]].rename(columns={"銘柄名": "jpx_name", "33業種区分": "jpx_industry"})


def load_edinet_codes() -> pd.DataFrame:
    df = pd.read_csv(EDINETCODE_CSV, encoding="cp932", skiprows=1, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    listed = df[(df["上場区分"] == "上場") & (df["提出者種別"] == "内国法人・組合")].copy()
    listed = listed.rename(columns={
        "ＥＤＩＮＥＴコード": "edinet_code", "証券コード": "sec_code", "提出者名": "name",
    })
    return listed[["edinet_code", "sec_code", "name"]]


def normalize_industry(jpx_industry: str, name: str) -> str:
    industry = INDUSTRY_RENAME.get(jpx_industry, jpx_industry)
    if industry == "電気・ガス業" and ("瓦斯" in name or "ガス" in name):
        return "ガス業"
    return industry


def detect_is_holding(name: str) -> bool:
    return any(p in name for p in HOLDING_PATTERNS)


def build_rows(prime: pd.DataFrame, edinet: pd.DataFrame, existing_codes: set[str]) -> tuple[list[dict], pd.DataFrame]:
    merged = prime.merge(edinet, on="sec_code", how="left")
    unmatched = merged[merged["edinet_code"].isna()]
    matched = merged[merged["edinet_code"].notna()]

    new_rows = []
    for _, r in matched.iterrows():
        if r["edinet_code"] in existing_codes:
            continue  # 既存154社は上書きしない
        industry = normalize_industry(r["jpx_industry"], r["name"])
        new_rows.append({
            "edinet_code": r["edinet_code"],
            "sec_code": r["sec_code"],
            "name": r["name"],
            "industry": industry,
            "peer_group": INDUSTRY_TO_PEER_GROUP.get(industry, industry),
            "is_holding": "yes" if detect_is_holding(r["name"]) else "no",
            "note": "自動追加（JPXプライム市場・33業種区分から機械分類）",
        })
    return new_rows, unmatched


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="companies.csv に追記する")
    ap.add_argument("--only-industry", help="このindustryの企業だけ対象にする（パイロット用）")
    args = ap.parse_args()

    with COMPANIES_CSV.open(encoding="utf-8", newline="") as f:
        existing = list(csv.DictReader(f))
        fieldnames = list(existing[0].keys())
    existing_codes = {r["edinet_code"] for r in existing}

    print("JPXプライム市場一覧を取得します。")
    prime = load_prime_list()
    print(f"  プライム内国株式: {len(prime)}件")

    edinet = load_edinet_codes()
    print(f"  EDINET上場・内国法人: {len(edinet)}件")

    new_rows, unmatched = build_rows(prime, edinet, existing_codes)

    if not unmatched.empty:
        print(f"\nEDINETコードに突合できなかった{len(unmatched)}件（優先株式・種類株式などは正常）:")
        for _, r in unmatched.iterrows():
            print(f"  {r['sec_code']}  {r['jpx_name']}  [{r['jpx_industry']}]")

    if args.only_industry:
        before = len(new_rows)
        new_rows = [r for r in new_rows if r["industry"] == args.only_industry]
        print(f"\n--only-industry {args.only_industry}: {before}件 → {len(new_rows)}件に絞り込み")

    print(f"\n新規追加候補: {len(new_rows)}件（既存154社は変更しない）")
    from collections import Counter
    by_industry = Counter(r["industry"] for r in new_rows)
    for ind, n in sorted(by_industry.items(), key=lambda x: -x[1]):
        print(f"  {ind:12s} +{n}")
    holding_n = sum(1 for r in new_rows if r["is_holding"] == "yes")
    print(f"\n  is_holding=yes と自動判定: {holding_n}件")

    if not args.apply:
        print("\n反映するには --apply を付けて実行してください。")
        return

    with COMPANIES_CSV.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        for r in new_rows:
            w.writerow(r)
    print(f"\n{COMPANIES_CSV} に{len(new_rows)}行追記しました。")


if __name__ == "__main__":
    sys.exit(main())
