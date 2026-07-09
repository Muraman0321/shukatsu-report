"""EDINET API v2 クライアント。

金融庁 EDINET から有価証券報告書を取得する。仕様は EDINET API仕様書 Version 2
(cache/EDINET_API_spec_v2.pdf) に準拠。

  書類一覧API  GET /api/v2/documents.json?date=YYYY-MM-DD&type=2&Subscription-Key=...
  書類取得API  GET /api/v2/documents/{docID}?type=N&Subscription-Key=...
                 type=1 提出本文書及び監査報告書(ZIP/XBRL)  type=2 PDF  type=5 CSV

守るべき約束が3つある。

1. EDINETの利用規約は短時間の大量アクセスを禁じており、仕様書にも 429 Too Many
   Requests が定義されている。全リクエストを1件/秒に絞る。
2. 認証エラーでも **HTTPステータスは200** で返り、本文の StatusCode に 401 が入る。
   raise_for_status() だけでは失敗を握り潰すので、本文まで検査する。
3. 取得済みのファイルは cache/ に置き、二度と取りに行かない。

使い方:
    python fetch_edinet.py find --from 2026-06-01 --to 2026-06-30
    python fetch_edinet.py get  --types 5
    python fetch_edinet.py get  --types 2 --only E02529,E02513   # 目視照合用のPDF
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache" / "edinet"
LIST_CACHE = CACHE / "list"
DOC_CACHE = CACHE / "docs"
LOG_PATH = ROOT / "logs" / "edinet_access.log"
DOC_INDEX = ROOT / "data" / "doc_index.csv"

API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
DOCTYPE_ANNUAL_REPORT = "120"  # 有価証券報告書（仕様書 書類種別コード）

MIN_INTERVAL_SEC = 1.0  # 利用規約遵守。緩めないこと
MAX_RETRIES = 4

# 書類取得APIの type と、保存時の拡張子
DOC_TYPE_EXT = {"1": "zip", "2": "pdf", "3": "zip", "4": "zip", "5": "zip"}


class EdinetError(RuntimeError):
    pass


def _log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"{stamp}\t{line}\n")


class RateLimitedSession:
    """全リクエストの間隔を MIN_INTERVAL_SEC 以上に強制するセッション。"""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "shukatsu-report/0.1 (research; contact via repository)"
        self._last_request_at = 0.0

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_INTERVAL_SEC:
            time.sleep(MIN_INTERVAL_SEC - elapsed)

    def get(self, url: str, params: dict) -> requests.Response:
        params = {**params, "Subscription-Key": self.api_key}
        safe_url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items() if k != 'Subscription-Key')}"

        for attempt in range(1, MAX_RETRIES + 1):
            self._wait()
            resp = self.session.get(url, params=params, timeout=60)
            self._last_request_at = time.monotonic()
            _log(f"GET\t{safe_url}\thttp={resp.status_code}\tattempt={attempt}")

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2**attempt * 5))
                _log(f"429 Too Many Requests -> sleep {wait}s")
                print(f"  429を受信。{wait}秒待って再試行します。", file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = 2**attempt
                _log(f"{resp.status_code} server error -> sleep {wait}s")
                time.sleep(wait)
                continue
            return resp

        raise EdinetError(f"再試行上限に達しました: {safe_url}")


def _check_json_body(resp: requests.Response) -> dict:
    """EDINETは認証失敗でもHTTP 200を返す。本文のStatusCodeまで見る。"""
    data = resp.json()
    if "StatusCode" in data and str(data["StatusCode"]) != "200":
        raise EdinetError(f"EDINET拒否: StatusCode={data['StatusCode']} {data.get('message')}")
    status = data.get("metadata", {}).get("status")
    if status and str(status) != "200":
        raise EdinetError(f"EDINET拒否: metadata.status={status} {data.get('metadata', {}).get('message')}")
    return data


# --------------------------------------------------------------------------
# find: 書類一覧APIを日付でなめて、対象企業の有報のdocIDを拾う
# --------------------------------------------------------------------------


@dataclass
class Filing:
    edinet_code: str
    filer_name: str
    doc_id: str
    period_end: str
    submit_datetime: str
    doc_description: str
    csv_flag: str
    pdf_flag: str


def _date_range(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def fetch_document_list(sess: RateLimitedSession, day: dt.date) -> dict:
    """1日分の提出書類一覧。取得済みならキャッシュを返す（再取得しない）。"""
    LIST_CACHE.mkdir(parents=True, exist_ok=True)
    cached = LIST_CACHE / f"{day.isoformat()}.json"
    if cached.exists():
        _log(f"CACHE\tlist\t{day.isoformat()}")
        return json.loads(cached.read_text(encoding="utf-8"))

    resp = sess.get(f"{API_BASE}/documents.json", {"date": day.isoformat(), "type": "2"})
    data = _check_json_body(resp)
    cached.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def cmd_find(args) -> None:
    sess = RateLimitedSession(_require_key())
    targets = _load_companies()
    wanted = set(targets)

    start = dt.date.fromisoformat(args.date_from)
    end = dt.date.fromisoformat(args.date_to)
    days = (end - start).days + 1
    print(f"{start} 〜 {end}（{days}日）を走査します。1件/秒なので最大 {days} 秒。")

    found: list[Filing] = []
    for i, day in enumerate(_date_range(start, end), 1):
        data = fetch_document_list(sess, day)
        results = data.get("results") or []
        hits = [
            r
            for r in results
            if r.get("edinetCode") in wanted and r.get("docTypeCode") == DOCTYPE_ANNUAL_REPORT
        ]
        for r in hits:
            found.append(
                Filing(
                    edinet_code=r["edinetCode"],
                    filer_name=r.get("filerName", ""),
                    doc_id=r["docID"],
                    period_end=r.get("periodEnd") or "",
                    submit_datetime=r.get("submitDateTime") or "",
                    doc_description=r.get("docDescription") or "",
                    csv_flag=r.get("csvFlag") or "",
                    pdf_flag=r.get("pdfFlag") or "",
                )
            )
        print(f"  [{i}/{days}] {day} 全{len(results)}件 → 対象の有報 {len(hits)}件", flush=True)

    _write_index(found, append=args.append)
    print(f"\n{len(found)}件の有報を見つけ、{DOC_INDEX} に書きました。")

    missing = wanted - {f.edinet_code for f in found}
    if missing:
        names = {c: n for c, n in targets.items()}
        print("\nこの期間に有報が見つからなかった企業（決算期が3月でない可能性）:")
        for code in sorted(missing):
            print(f"  {code}  {names[code]}")


def _write_index(rows: list[Filing], append: bool) -> None:
    DOC_INDEX.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[tuple[str, str], Filing] = {}
    if append and DOC_INDEX.exists():
        with DOC_INDEX.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                existing[(r["edinet_code"], r["doc_id"])] = Filing(**r)
    for f_ in rows:
        existing[(f_.edinet_code, f_.doc_id)] = f_

    with DOC_INDEX.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(Filing.__dataclass_fields__))
        w.writeheader()
        for f_ in sorted(existing.values(), key=lambda x: (x.edinet_code, x.period_end)):
            w.writerow(f_.__dict__)


# --------------------------------------------------------------------------
# get: 書類取得APIで実ファイルを落とす
# --------------------------------------------------------------------------


def cmd_get(args) -> None:
    sess = RateLimitedSession(_require_key())
    if not DOC_INDEX.exists():
        raise SystemExit(f"{DOC_INDEX} がありません。先に `find` を実行してください。")

    with DOC_INDEX.open(encoding="utf-8", newline="") as f:
        filings = [Filing(**r) for r in csv.DictReader(f)]

    if args.only:
        keep = {c.strip() for c in args.only.split(",")}
        filings = [f for f in filings if f.edinet_code in keep]

    doc_types = [t.strip() for t in args.types.split(",")]
    DOC_CACHE.mkdir(parents=True, exist_ok=True)

    for f_ in filings:
        for t in doc_types:
            if t == "5" and f_.csv_flag == "0":
                print(f"  skip {f_.doc_id} type=5 (csvFlag=0 提供なし)")
                continue
            if t == "2" and f_.pdf_flag == "0":
                print(f"  skip {f_.doc_id} type=2 (pdfFlag=0 提供なし)")
                continue

            out = DOC_CACHE / f"{f_.doc_id}_type{t}.{DOC_TYPE_EXT[t]}"
            if out.exists():
                _log(f"CACHE\tdoc\t{f_.doc_id}\ttype={t}")
                print(f"  cached {out.name}")
                continue

            resp = sess.get(f"{API_BASE}/documents/{f_.doc_id}", {"type": t})
            ctype = resp.headers.get("content-type", "")
            # 書類取得APIは成功でバイナリ、失敗でJSONを返す（仕様書 3-3）
            if "application/json" in ctype:
                raise EdinetError(f"{f_.doc_id} type={t} 取得失敗: {resp.text[:200]}")
            out.write_bytes(resp.content)
            print(f"  saved  {out.name}  ({len(resp.content):,} bytes)  {f_.filer_name}")


# --------------------------------------------------------------------------


def _require_key() -> str:
    load_dotenv(ROOT / ".env")
    key = os.getenv("EDINET_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "EDINET_API_KEY が未設定です。\n"
            "  0. https://api.edinet-fsa.go.jp をポップアップ許可サイトに追加（発行画面はポップアップで開く）\n"
            "  1. EDINET閲覧サイト https://disclosure2.edinet-fsa.go.jp/weee0010.aspx →「ログイン」→「今すぐサインアップ」\n"
            "  2. .env.example を .env にコピーし、EDINET_API_KEY= に貼り付け\n"
            "  （?mode=2 のURLは『APIキー削除』画面。開かないこと）"
        )
    return key


def _load_companies() -> dict[str, str]:
    with (ROOT / "companies.csv").open(encoding="utf-8", newline="") as f:
        return {r["edinet_code"]: r["name"] for r in csv.DictReader(f)}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find", help="書類一覧APIを走査し、対象企業の有報docIDを doc_index.csv に書く")
    f.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    f.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    f.add_argument("--append", action="store_true", help="既存のdoc_index.csvに追記する（過年度を足すとき）")
    f.set_defaults(func=cmd_find)

    g = sub.add_parser("get", help="書類取得APIで実ファイルを cache/edinet/docs に落とす")
    g.add_argument("--types", default="5", help="カンマ区切り。5=CSV, 2=PDF, 1=XBRL(ZIP)")
    g.add_argument("--only", help="EDINETコードをカンマ区切りで絞る")
    g.set_defaults(func=cmd_get)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
