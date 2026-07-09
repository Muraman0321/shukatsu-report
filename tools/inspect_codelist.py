"""EDINETコードリストの中身を確認するだけの使い捨てスクリプト。"""

import sys
from pathlib import Path

import pandas as pd

CSV = Path(__file__).resolve().parents[1] / "cache" / "edinetcode" / "EdinetcodeDlInfo.csv"

df = pd.read_csv(CSV, encoding="cp932", skiprows=1, dtype=str)
df.columns = [c.strip() for c in df.columns]

listed = df[(df["上場区分"] == "上場") & (df["提出者種別"] == "内国法人・組合")]

print(f"全件: {len(df)}  /  上場・内国法人: {len(listed)}")
print()
print("業種トップ15:")
print(listed["提出者業種"].value_counts().head(15).to_string())
print()

targets = sys.argv[1:] or ["三菱商事", "三井物産", "伊藤忠商事", "住友商事", "丸紅", "双日"]
print("名前検索:")
for t in targets:
    hit = listed[listed["提出者名"].str.contains(t, na=False)]
    for _, r in hit.iterrows():
        print(f"  {r['ＥＤＩＮＥＴコード']}  {r['証券コード']}  {r['提出者名']}  [{r['提出者業種']}]")
    if hit.empty:
        print(f"  (該当なし) {t}")
