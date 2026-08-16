"""有報の「地域ごとの情報」から海外売上高比率を取る。**取れた会社だけ載せる。**

なぜこれを作るのか:
    「海外で働けるか」は就活生の関心が大きいのに、公的な一次情報で横比較した表が
    どこにもない。有報の連結財務諸表注記「地域ごとの情報」には売上の国内・海外内訳が
    開示義務で載っており、これが唯一の一次情報である。

歩留まりは企業規模で大きく違う（実測）:
    連結従業員数 上位100社 61% / 中位100社 10% / 下位100社 8%
    単一セグメント・国内のみの会社は開示義務が無いため。就活生が見る大手ほど載っている。

抽出の難しさと、その対処:

1. **数値はXBRLのタグではなくテキストブロックの中にある。**
   表がHTMLではなくプレーンテキストに潰れており、しかも前期と当期が
   区切り無しで連結する（日立「日本3,779,2033,912,854」＝前期3,779,203／当期3,912,854）。
   カンマ区切りの数値として貪欲に読むと正しく2つに割れる。

2. **同じブロックに非流動資産の地域別表が続く。** そちらの「日本」を売上と取り違えると
   比率が壊れる。**売上の表が終わる位置で本文を切ってから**読む。

3. **前期と当期を取り違えても、見た目には気づけない。**
   → そこで **XBRLから別途取った当期の売上高と突き合わせる**。
   テキストから読んだ合計が売上高と2%以内で一致したときだけ採用する。
   一致しなければ公開しない。verify_employees.py が有報PDFとXBRLを突き合わせるのと同じ思想。

使い方:
    python extract_overseas.py              # 全社 → data/overseas/*.json
    python extract_overseas.py --sample 200 # 歩留まりだけ見る（書き出さない）
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOC_CACHE = ROOT / "cache" / "edinet" / "docs"
COMPANY_DIR = ROOT / "data" / "companies"
FIN_DIR = ROOT / "data" / "financials"
OUT_DIR = ROOT / "data" / "overseas"

GEO_ELEM = re.compile(r"(InformationAboutGeographicalAreas|GeographicalInformation)")
# 売上の表の終わりを示す語。ここから後ろは資産の表なので読まない（地雷2）
ASSET_MARK = re.compile(r"(非流動資産|有形固定資産|所在地別の有形固定資産|②\s*非流動資産)")
UNIT_RX = re.compile(r"単位[：:]?\s*(百万円|千円|円)")
NUM_RUN = re.compile(r"\d{1,3}(?:,\d{3})*")

# 地域名の表記は会社ごとにばらばら。長いものから順に当てる。
# 「日本」と書く会社（トヨタ・日立）と「国内」と書く会社（NTT・富士通）がある。
JAPAN_LABELS = ["国内（日本）", "国内(日本)", "日本国内", "日本", "国内"]
OVERSEAS_LABELS = ["海外売上収益", "海外売上高", "海外収益", "海外"]
OTHER_REGION_LABELS = [
    "アジア・太平洋地域", "アジアパシフィック", "アジア／オセアニア", "その他アジア", "東アジア",
    "その他の地域", "その他地域", "中南米", "南米", "北米", "米州", "米国", "アメリカ",
    "欧州", "ヨーロッパ", "中国", "アジア", "オセアニア", "インド", "シンガポール",
    "オーストラリア", "オランダ", "カナダ", "メキシコ", "ブラジル", "韓国", "台湾", "タイ",
    "中近東", "中東", "アフリカ", "その他",
]
TOTAL_LABELS = ["連結売上高", "合計", "連結", "計"]

ALL_LABELS = JAPAN_LABELS + OVERSEAS_LABELS + OTHER_REGION_LABELS + TOTAL_LABELS
LABEL_RX = re.compile("|".join(re.escape(x) for x in sorted(ALL_LABELS, key=len, reverse=True)))

UNIT_MULT = {"百万円": 1_000_000, "千円": 1_000, "円": 1}
TOLERANCE = 0.02  # XBRLの売上高との許容差


def load_rows(doc_id: str) -> list[list[str]]:
    with zipfile.ZipFile(DOC_CACHE / f"{doc_id}_type5.zip") as z:
        name = next(n for n in z.namelist() if "asr" in n and n.endswith(".csv"))
        text = z.read(name).decode("utf-16")
    return list(csv.reader(io.StringIO(text), delimiter="\t"))[1:]


def plain(html: str) -> str:
    s = re.sub(r"<[^>]+>", " ", html)
    s = s.replace("&#160;", " ").replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"[ \t　]+", " ", s)


def _values_in(run: str) -> list[int]:
    """1つのラベルの直後に続く数値の並び。前期・当期が区切り無しで連なる（地雷1）。

    三菱電機のように金額と構成比（%）が交互に並ぶ表があるので、
    **桁区切りのカンマを持つ数値があるときは、それだけを金額とみなす。**
    そうしないと「日本 2,723,553 49.3％ 2,932,381 49.7％」の末尾から 7 を拾う。
    """
    found = NUM_RUN.findall(run)
    with_comma = [x for x in found if "," in x]
    use = with_comma or found
    return [int(x.replace(",", "")) for x in use]


FOLLOWED_BY_NUM = re.compile(r"^[ 　]*\d")
PROSE_RX = re.compile(r"[ぁ-んァ-ヶ]{2,}|[一-龥]{3,}")


def label_values(text: str) -> list[tuple[str, list[int]]]:
    """本文を左から走査し、(地域ラベル, その直後の数値の並び) を順に返す。

    表がプレーンテキストに潰れているので、ラベルの位置を手がかりに切り分けるほかない。

    **ラベルとみなすのは、直後（空白のみ挟んで）に数字が続くものだけ。**
    この制約が無いと「前連結会計年度」の中の『連結』や『計』を合計行と誤認し、
    日付を金額として読む。表では必ずラベルの右隣に数値が来る、という構造を使う。

    誤読があっても、最後にXBRLの売上高と突き合わせて落とす（地雷3）。
    """
    marks = [
        m for m in LABEL_RX.finditer(text)
        if FOLLOWED_BY_NUM.match(text[m.end(): m.end() + 4])
    ]
    out: list[tuple[str, list[int]]] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else min(len(text), m.end() + 120)
        run = text[m.end(): end]
        # 数値の並びが終わって地の文に入ったら、そこで打ち切る
        p = PROSE_RX.search(run)
        if p:
            run = run[: p.start()]
        vals = _values_in(run)
        if vals:
            out.append((m.group(0), vals))
    return out


def parse_block(text: str) -> dict | None:
    """1つの地域別情報テキストから、日本・海外・合計（いずれも当期）を読む。"""
    unit_m = UNIT_RX.search(text)
    mult = UNIT_MULT.get(unit_m.group(1), 1_000_000) if unit_m else 1_000_000

    # どこを売上の表とみなすかは slices() が候補を作って渡してくる（地雷2）
    pairs = label_values(text)
    if not pairs:
        return None

    def first_of(labels: list[str]) -> int | None:
        """当期＝並びの最後の値。表は前期→当期の順に並ぶ。"""
        for lab, vals in pairs:
            if lab in labels:
                return vals[-1]
        return None

    japan = first_of(JAPAN_LABELS)
    overseas = first_of(OVERSEAS_LABELS)
    total = first_of(TOTAL_LABELS)

    if total is None:
        if japan is not None and overseas is not None:
            total = japan + overseas
        else:
            # 合計行が無い表は、地域を足し上げる。誤れば XBRL との照合で落ちる
            region_sum = sum(
                vals[-1] for lab, vals in pairs
                if lab in JAPAN_LABELS + OTHER_REGION_LABELS
            )
            total = region_sum or None
    if not total or total <= 0:
        return None
    if overseas is None:
        if japan is None:
            return None
        overseas = total - japan
    if japan is None:
        japan = total - overseas
    if not (0 <= overseas <= total) or not (0 <= japan <= total):
        return None

    return {
        "unit_multiplier": mult,
        "japan": japan * mult,
        "overseas": overseas * mult,
        "total": total * mult,
        "overseas_ratio": round(overseas / total, 4),
        "unit_label": unit_m.group(1) if unit_m else "百万円（推定）",
    }


def slices(text: str) -> list[str]:
    """1つのテキストから読み方の候補をいくつか作る。

    表の並び順は会社によって逆になる。スズキは非流動資産の表が先で売上の表が後ろにあり、
    先頭から読むと資産の数字を売上として読んでしまう。どれが正しいかは
    XBRLの売上高と突き合わせて決めればよいので、候補を出して照合に委ねる。
    """
    out = [text]
    a = ASSET_MARK.search(text)
    if a:
        if a.start() > 200:
            out.append(text[: a.start()])   # 売上の表が先
        out.append(text[a.end():])          # 資産の表が先
    return out


def extract(doc_id: str, xbrl_revenue: float | None) -> dict | None:
    rows = load_rows(doc_id)
    blocks = [r[8] for r in rows if len(r) > 8 and GEO_ELEM.search(r[0]) and len(r[8]) > 120]
    candidates = []
    for raw in blocks:
        t = plain(raw)
        for s in slices(t):
            got = parse_block(s)
            if got:
                candidates.append(got)
    for got in candidates:
        # XBRLの売上高との突き合わせ。合わなければ採らない（地雷3）
        if xbrl_revenue:
            diff = abs(got["total"] - xbrl_revenue) / xbrl_revenue
            got["xbrl_revenue"] = xbrl_revenue
            got["diff_vs_xbrl"] = round(diff, 4)
            if diff > TOLERANCE:
                got["verdict"] = "不一致のため不採用"
                continue
            got["verdict"] = f"XBRLの売上高と一致（差 {diff * 100:.2f}%）"
        else:
            got["verdict"] = "XBRLの売上高が無く突き合わせ不能のため不採用"
            continue
        return got
    return None


def main(argv: list[str]) -> None:
    sample = 0
    if "--sample" in argv:
        i = argv.index("--sample")
        sample = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    only = {a for a in argv if a.startswith("E")}

    files = sorted(COMPANY_DIR.glob("*.json"))
    if only:
        files = [f for f in files if f.stem in only]
    if sample:
        import random
        random.seed(20260816)
        files = random.sample(files, min(sample, len(files)))
    if not sample:
        OUT_DIR.mkdir(parents=True, exist_ok=True)

    stat = Counter()
    examples = []
    for p in files:
        d = json.loads(p.read_text(encoding="utf-8"))
        stat["対象"] += 1
        did = (d["latest"].get("source") or {}).get("doc_id")
        if not did or not (DOC_CACHE / f"{did}_type5.zip").exists():
            continue
        fp = FIN_DIR / f"{d['edinet_code']}.json"
        rev = None
        if fp.exists():
            rev = json.loads(fp.read_text(encoding="utf-8"))["latest"]["values"].get("revenue")
        try:
            got = extract(did, rev)
        except (zipfile.BadZipFile, StopIteration, UnicodeDecodeError, ValueError):
            continue
        if not got:
            continue
        stat["採用"] += 1
        rec = {
            "edinet_code": d["edinet_code"], "name": d["name"], "peer_group": d.get("peer_group", ""),
            "period_end": d["latest"]["source"]["period_end"], "doc_id": did, **got,
        }
        if len(examples) < 12:
            examples.append(rec)
        if not sample:
            (OUT_DIR / f"{d['edinet_code']}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    n = stat["対象"]
    print(f"\n対象 {n} 社 → 海外売上高比率を採用できたのは {stat['採用']} 社（{stat['採用']/n*100:.1f}%）")
    print("（XBRLの売上高と2%以内で一致したものだけを採用。一致しないものは捨てている）")
    print("\n例:")
    for r in examples:
        print(f"  {r['name'][:22]:24s} 海外 {r['overseas_ratio']*100:5.1f}%  "
              f"合計 {r['total']/1e12:7.2f}兆円  {r['verdict']}")
    if not sample:
        print(f"\n書き出し: {OUT_DIR}")


main(sys.argv[1:])
