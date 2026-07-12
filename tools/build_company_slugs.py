"""EDINETコードリストの英字名・ヨミから、企業ページの読めるURLスラグを生成する。

既存の SLUG 辞書（generate.py 内、154社ぶん手動選定）は絶対に上書きしない。
そこに無い企業だけを対象に、`提出者名（英字）` を機械的にスラグ化し、
無ければ `提出者名（ヨミ）` をローマ字化する。結果は data/company_slugs.json に
**一度書いたら二度と変えない**形で追記していく。URLは公開後に変えるとSEOが死ぬため。

    python tools/build_company_slugs.py           # 未割当の企業だけレポート
    python tools/build_company_slugs.py --apply    # data/company_slugs.json に追記
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EDINETCODE_CSV = ROOT / "cache" / "edinetcode" / "EdinetcodeDlInfo.csv"
COMPANIES_CSV = ROOT / "companies.csv"
OUT = ROOT / "data" / "company_slugs.json"

# generate.py の SLUG 辞書と同じキー集合を読むために、この中身をここで再定義はしない。
# 代わりに generate.py を import して SLUG を直接参照する。
sys.path.insert(0, str(ROOT))
from generate import SLUG  # noqa: E402

# EDINETコードリスト側の英字名に明白な誤記があるものだけ、個別に直す
# （データそのものは書き換えない。URLスラグにだけ適用する）。
ENGLISH_NAME_TYPO_FIX = {
    "E03622": "Yamaguchi Financial Group, Inc.",  # 原本は "Finacial"（Financial の誤記）
}

SUFFIX_RE = re.compile(
    r",?\s*(Co\.,?\s*Ltd\.?|Ltd\.?|Inc\.?|Corporation|Corp\.?|Holdings?|Group|"
    r"K\.K\.?|G\.K\.?|Limited)\s*$",
    re.IGNORECASE,
)

# カタカナ→ローマ字（ヘボン式簡易版）。英字名が無い企業のフォールバック用。
KANA_MAP = {
    "キャ": "kya", "キュ": "kyu", "キョ": "kyo", "シャ": "sha", "シュ": "shu", "ショ": "sho",
    "チャ": "cha", "チュ": "chu", "チョ": "cho", "ニャ": "nya", "ニュ": "nyu", "ニョ": "nyo",
    "ヒャ": "hya", "ヒュ": "hyu", "ヒョ": "hyo", "ミャ": "mya", "ミュ": "myu", "ミョ": "myo",
    "リャ": "rya", "リュ": "ryu", "リョ": "ryo", "ギャ": "gya", "ギュ": "gyu", "ギョ": "gyo",
    "ジャ": "ja", "ジュ": "ju", "ジョ": "jo", "ビャ": "bya", "ビュ": "byu", "ビョ": "byo",
    "ピャ": "pya", "ピュ": "pyu", "ピョ": "pyo", "ファ": "fa", "フィ": "fi", "フェ": "fe",
    "フォ": "fo", "ウィ": "wi", "ウェ": "we", "ウォ": "wo", "ティ": "ti", "ディ": "di",
    "ア": "a", "イ": "i", "ウ": "u", "エ": "e", "オ": "o",
    "カ": "ka", "キ": "ki", "ク": "ku", "ケ": "ke", "コ": "ko",
    "サ": "sa", "シ": "shi", "ス": "su", "セ": "se", "ソ": "so",
    "タ": "ta", "チ": "chi", "ツ": "tsu", "テ": "te", "ト": "to",
    "ナ": "na", "ニ": "ni", "ヌ": "nu", "ネ": "ne", "ノ": "no",
    "ハ": "ha", "ヒ": "hi", "フ": "fu", "ヘ": "he", "ホ": "ho",
    "マ": "ma", "ミ": "mi", "ム": "mu", "メ": "me", "モ": "mo",
    "ヤ": "ya", "ユ": "yu", "ヨ": "yo",
    "ラ": "ra", "リ": "ri", "ル": "ru", "レ": "re", "ロ": "ro",
    "ワ": "wa", "ヲ": "o", "ン": "n",
    "ガ": "ga", "ギ": "gi", "グ": "gu", "ゲ": "ge", "ゴ": "go",
    "ザ": "za", "ジ": "ji", "ズ": "zu", "ゼ": "ze", "ゾ": "zo",
    "ダ": "da", "ヂ": "ji", "ヅ": "zu", "デ": "de", "ド": "do",
    "バ": "ba", "ビ": "bi", "ブ": "bu", "ベ": "be", "ボ": "bo",
    "パ": "pa", "ピ": "pi", "プ": "pu", "ペ": "pe", "ポ": "po",
    "ッ": "", "ー": "", "・": "-", "　": "-", " ": "-",
}


# ヨミは「カブシキガイシャ」+社名+業態語（フィナンシャルグループ等）の連結で、
# 文字単位のローマ字化だけでは単語の切れ目が失われ読めない塊になる
# （例：メブキフィナンシャルグループ→ mebukifinansharugurupu）。
# よくある接頭辞・接尾辞だけは意味のある英単語に置き換えてハイフンで区切る。
PREFIX_STRIP = ("カブシキガイシャ", "カブシキカイシャ")
SUFFIX_WORDS = [
    ("フィナンシャルホールディングス", "financial-holdings"),
    ("フィナンシャルグループ", "financial-group"),
    ("ホールディングス", "holdings"),
    ("ギンコウ", "bank"),
]


def _romanize_chars(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        two = s[i : i + 2]
        if two in KANA_MAP:
            out.append(KANA_MAP[two])
            i += 2
            continue
        out.append(KANA_MAP.get(s[i], ""))
        i += 1
    return "".join(out)


def romanize_kana(yomi: str) -> str:
    s = yomi.strip()
    for p in PREFIX_STRIP:
        if s.startswith(p):
            s = s[len(p) :]
            break
    suffix = ""
    for kana, eng in SUFFIX_WORDS:
        if s.endswith(kana):
            suffix = "-" + eng
            s = s[: -len(kana)]
            break
    core = _romanize_chars(s)
    return (core + suffix).strip("-")


def slugify_english(name: str) -> str:
    s = name.strip()
    s = re.sub(r"^The\s+", "", s, flags=re.IGNORECASE)
    # "Co., Ltd." のような複合サフィックスに対応するため複数回剥がす
    for _ in range(3):
        s2 = SUFFIX_RE.sub("", s).strip().rstrip(",").strip()
        if s2 == s:
            break
        s = s2
    s = s.replace("&", " and ")
    s = re.sub(r"[.,]", "", s)
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s


def unique_slug(base: str, taken: set[str]) -> str:
    if not base:
        return ""
    if base not in taken:
        return base
    for n in range(2, 50):
        cand = f"{base}-{n}"
        if cand not in taken:
            return cand
    return base


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    edi = pd.read_csv(EDINETCODE_CSV, encoding="cp932", skiprows=1, dtype=str)
    edi.columns = [c.strip() for c in edi.columns]
    eng = dict(zip(edi["ＥＤＩＮＥＴコード"], edi["提出者名（英字）"]))
    yomi = dict(zip(edi["ＥＤＩＮＥＴコード"], edi["提出者名（ヨミ）"]))

    with COMPANIES_CSV.open(encoding="utf-8", newline="") as f:
        companies = list(csv.DictReader(f))

    existing: dict[str, str] = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    taken = set(SLUG.values()) | set(existing.values())

    added = {}
    unresolved = []
    for c in companies:
        code = c["edinet_code"]
        if code in SLUG or code in existing:
            continue
        name_en = ENGLISH_NAME_TYPO_FIX.get(code, str(eng.get(code) or "").strip())
        base = slugify_english(name_en) if name_en and name_en != "nan" else ""
        if not base:
            name_yomi = str(yomi.get(code) or "").strip()
            base = romanize_kana(name_yomi) if name_yomi and name_yomi != "nan" else ""
        if not base:
            unresolved.append(c["name"])
            continue
        slug = unique_slug(base, taken)
        taken.add(slug)
        added[code] = slug

    print(f"新規に割り当て: {len(added)}件")
    for code, slug in list(added.items())[:20]:
        print(f"  {code}  ->  {slug}")
    if len(added) > 20:
        print(f"  ...ほか{len(added) - 20}件")
    if unresolved:
        print(f"\n英字名・ヨミとも取得できず未解決（edinet_codeのままになる）: {len(unresolved)}件")
        for n in unresolved:
            print("  -", n)

    if not a.apply:
        print("\n反映するには --apply を付けて実行してください。")
        return

    existing.update(added)
    OUT.write_text(json.dumps(existing, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"\n{OUT} に{len(added)}件追記しました（既存{len(existing) - len(added)}件は変更なし）。")


if __name__ == "__main__":
    main()
