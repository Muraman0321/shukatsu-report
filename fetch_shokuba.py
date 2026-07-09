"""しょくばらぼ（厚労省 職場情報総合サイト）の全件データファイルを取得する。

有報には載らない、就活生が最も知りたい数字がここにある。

  新卒者の採用・定着状況（前年度/2年度前/3年度前）の採用者数と離職者数 → **3年以内離職率**
  月平均所定外労働時間 / 正社員の有給休暇取得日数
  男女の賃金の差異（全労働者・正規・非正規）/ 管理職に占める女性の割合
  研修制度・メンター制度・自己啓発支援の有無

アクセスの根拠（ここを曖昧にしない）
------------------------------------
- `https://shokuba.mhlw.go.jp/robots.txt` は全クローラーに `Disallow: /shokuba/` を課す。
  Struts製の検索アプリ全体がこの配下にあるため、**検索ページは1つも叩かない。スクレイパーも書かない。**
- e-Gov APIカタログに登録された「提供用WEB-API」は
  「提供用WEB-API機能による情報提供サービスについては、現在一時停止しております」（公式）。使えない。
- 一方、同サイトのCSV一括ダウンロードページ（/shokuba/utilize/utilize010.do）は
  **「上記と同じ職場情報ダウンロードURLは以下になります。各サイト様にて…ご活用ください」**
  と明記して、下記の直リンクURLを第三者に公開している。
- ライセンスは政府標準利用規約（第2.0版）準拠、CC BY 4.0 互換、**商用利用可**。
  出典表示「出典：職場情報総合サイト（しょくばらぼ）(https://shokuba.mhlw.go.jp)」＋加工した旨の記載が条件。

よって、**サイトが公開している静的な全件ファイルを1リクエストだけ取得する**。
巡回ではないので robots.txt の趣旨に反しない。取得済みならスキップし、再取得しない。
"""

from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache" / "shokuba"

# CSV一括ダウンロードページが第三者向けに公開している直リンク
BULK_URL = "https://shokuba.mhlw.go.jp/shokuba/utilize/download010?lang=JA"
DEFINITION_URL = "https://shokuba.mhlw.go.jp/manual/aboutcsv.pdf"  # /manual/ は robots.txt の対象外

CHUNK = 1 << 20


def user_agent() -> str:
    load_dotenv(ROOT / ".env")
    contact = os.getenv("CONTACT_EMAIL", "").strip() or "no-contact-configured"
    return f"shukatsu-report/0.1 (+research use; contact: {contact})"


def download(url: str, dest: Path, ua: str) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  cached {dest.name} ({dest.stat().st_size:,} bytes)")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, headers={"User-Agent": ua}, stream=True, timeout=(30, 600)) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got = 0
        with tmp.open("wb") as f:
            for chunk in r.iter_content(CHUNK):
                f.write(chunk)
                got += len(chunk)
                if total:
                    print(f"\r  {dest.name}: {got/1e6:.1f}/{total/1e6:.1f} MB", end="", flush=True)
    print()
    tmp.replace(dest)
    print(f"  saved {dest.name} ({dest.stat().st_size:,} bytes)")


def main() -> None:
    ua = user_agent()
    download(DEFINITION_URL, CACHE / "aboutcsv.pdf", ua)
    download(BULK_URL, CACHE / "shokuba_all.zip", ua)


if __name__ == "__main__":
    main()
