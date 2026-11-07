#!/usr/bin/env python3
"""IRDB OpenSearch から機関リポジトリのアイテムを列挙し、本文ファイル(PDF)の
Wayback Machine 上の最新アーカイブ URL を CSV に出力する（標準ライブラリのみ）。

流れ:
  1. IRDB OpenSearch (fulltext=1, Atom) で全件取得し、URI のホストで自機関分に絞る
  2. URI からレコード番号を取り出す（本文 URL のファイル名は IRDB に無いため）
  3. Wayback CDX にレコード番号配下 (/record/{id}/files/) を
     プレフィックス照会し、PDF ファイルごとに最新の capture を選ぶ
  4. 結果を CSV に出力する（ダウンロードは担当者が手元で行う）

注意:
  - CDX の mimetype は信頼しない。実測で PDF が application/octet-stream になる例が
    あったため、判定は URL の拡張子 (.pdf) で行い、mimetype は参考列として残す。
  - Wayback はクローラの到達結果しか保存しない。保存が無いものは status=no_capture。
  - レート制限は公式に未公表。既定は 1 リクエスト/秒、429/503 は待って再試行する。

使い方:
  WB_CONTACT="連絡先(任意)" python3 wb_fulltext_csv.py --host jircas.repo.nii.ac.jp --out result.csv
  python3 wb_fulltext_csv.py --host HOST --limit 20 --out smoke.csv   # スモーク
  python3 wb_fulltext_csv.py --host HOST --bulk --out result.csv      # 一括取得（レコードごとの照会より少ない回数で済む）
--host は対象リポジトリのホスト（必須）。--query（IRDB の q）の既定は --host と同じ。
中断しても、同じ --cache-dir で再実行すれば取得済みの分は再利用される。
キャッシュは検索条件（IRDB 一覧）とホスト（CDX）ごとに識別され、条件が違えば再利用しない。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ATOM = "{http://www.w3.org/2005/Atom}"
OSNS = "{http://a9.com/-/spec/opensearch/1.1/}"
PRISM = "{http://prismstandard.org/namespaces/basic/2.0/}"

IRDB_OPENSEARCH = "https://irdb.nii.ac.jp/opensearch/search"
CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
CDX_FIELDS = ["timestamp", "original", "mimetype", "statuscode", "length"]
# 本文ファイル URL は /record/{id}/files/ 固定とする（暫定）。詳細ページは /records/{id} だが、
# ファイルは常に単一の保存先を指すため複数表記を照会する必要性は低いと判断した。
PATH_KINDS = ("record",)

# IRDB の prism:* 要素。CSV の列名は prism: を除いたもの
PRISM_FIELDS = ("issn", "publicationName", "volume", "number",
                "startingPage", "endingPage", "publicationDate")

CSV_COLUMNS = [
    "status", "title", *PRISM_FIELDS, "updated", "irdb_url", "record_url", "record_id",
    "file_url", "capture_timestamp", "capture_date", "mimetype", "length",
    "n_captures", "item_files_captured", "archive_url",
]


# ---------------------------------------------------------------------------
# 純粋関数（ネットワーク非依存。テスト対象）
# ---------------------------------------------------------------------------

def parse_atom(text: str) -> Tuple[int, List[dict]]:
    """IRDB OpenSearch の Atom 応答から (totalResults, entries) を返す。"""
    root = ET.fromstring(text)
    total = int((root.findtext(f"{OSNS}totalResults") or "0").strip() or 0)
    entries: List[dict] = []
    for e in root.findall(f"{ATOM}entry"):
        link = e.find(f"{ATOM}link")
        entries.append({
            "title": (e.findtext(f"{ATOM}title") or "").strip(),
            "irdb_url": (link.get("href") if link is not None else "") or "",
            "uri": (e.findtext(f"{ATOM}URI") or "").strip(),
            "irname": (e.findtext(f"{ATOM}irname") or "").strip(),
            **{k: join_texts(e.findall(f"{PRISM}{k}")) for k in PRISM_FIELDS},
            "updated": (e.findtext(f"{ATOM}updated") or "").strip(),
        })
    return total, entries


def count_duplicates(entries: Iterable[dict]) -> int:
    """IRDB 詳細ページ URL が重複している件数。

    ページ送りの間に並びが変わると、一覧に重複と欠落が同時に出る（実測で 36 件）。
    重複があれば同じ数だけ欠落している可能性が高く、取り直しが必要。
    """
    urls = [e.get("irdb_url", "") for e in entries]
    return len(urls) - len(set(urls))


def join_texts(elems: Iterable[ET.Element]) -> str:
    """同名の要素が複数ある場合（言語違い等）は、lang は出さず内容だけを " | " でつなぐ。"""
    return " | ".join(t for t in ((x.text or "").strip() for x in elems) if t)


_RECORD_RE = re.compile(r"/records?/(\d+)")


def record_id(uri: str) -> Optional[str]:
    """`https://host/records/2001056` や `/record/2000885/files/x.pdf` から番号を返す。"""
    m = _RECORD_RE.search(urllib.parse.urlsplit(uri).path)
    return m.group(1) if m else None


def uri_host(uri: str) -> str:
    return urllib.parse.urlsplit(uri).netloc.lower()


def parse_cdx(text: str) -> List[dict]:
    """CDX の output=json 応答を辞書のリストにする。空応答は []。"""
    text = (text or "").strip()
    if not text:
        return []
    data = json.loads(text)
    if not data:
        return []
    header = data[0]
    if "timestamp" in header and "original" in header:
        keys, body = header, data[1:]
    else:  # ヘッダ無し（想定外）の場合は既定の並びとみなす
        keys, body = CDX_FIELDS, data
    return [dict(zip(keys, row)) for row in body]


def parse_cdx_page(text: str) -> Tuple[List[dict], Optional[str]]:
    """showResumeKey=true の応答を (行, 次ページの resumeKey) にする。

    応答の末尾は `[], ["<resumeKey>"]` の形。最終ページには resumeKey が付かない。
    """
    text = (text or "").strip()
    if not text:
        return [], None
    data = json.loads(text)
    resume = None
    if [] in data:
        idx = data.index([])
        tail = data[idx + 1:]
        data = data[:idx]
        if tail and tail[0]:
            resume = tail[0][0]
    return parse_cdx(json.dumps(data)), resume


def is_pdf_url(original: str) -> bool:
    return urllib.parse.urlsplit(original).path.lower().endswith(".pdf")


def file_key(original: str) -> Tuple[str, str]:
    """同一ファイルとみなすキー。スキーム（http/https）とクエリは無視する。"""
    p = urllib.parse.urlsplit(original)
    return (p.netloc.lower(), p.path)


def has_valid_length(r: dict) -> bool:
    """CDX の length が正の整数か。

    実測で、status 200 なのに length が負の値の capture があり（7 件）、実際に取得すると
    Wayback の 404 ページだった。length 欄が欠けている行（fl に含めない場合）は有効とみなす。
    """
    v = r.get("length")
    if v is None or v == "":
        return True
    try:
        return int(v) > 0
    except (TypeError, ValueError):
        return False


def pick_latest_pdfs(rows: Iterable[dict]) -> List[dict]:
    """status 200 の PDF capture を、ファイルごとに最新の 1 件へ絞る。

    返す各行に `n_captures`（そのファイルの 200 の capture 総数）を付ける。
    length が不正な capture は無効として除く。
    """
    groups: Dict[Tuple[str, str], List[dict]] = {}
    for r in rows:
        if str(r.get("statuscode")) != "200" or not has_valid_length(r):
            continue
        if not is_pdf_url(r.get("original", "")):
            continue
        groups.setdefault(file_key(r["original"]), []).append(r)
    out = []
    for _, caps in sorted(groups.items()):
        latest = max(caps, key=lambda r: r["timestamp"])
        out.append({**latest, "n_captures": len(caps)})
    return out


def archive_url(timestamp: str, original: str) -> str:
    """ツールバー無し・リンク書き換え無しの原本を返す URL（id_ 指定）。"""
    return f"https://web.archive.org/web/{timestamp}id_/{original}"


def capture_date(timestamp: str) -> str:
    try:
        return datetime.strptime(timestamp[:14], "%Y%m%d%H%M%S").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def build_rows(entry: dict, rid: str, cdx_rows: List[dict]) -> List[dict]:
    """1 アイテム分の CSV 行（PDF ごとに 1 行。無ければ状態だけの 1 行）を作る。

    `item_files_captured` は、このアイテムで保存を確認できた PDF の数。
    保存されなかったファイルは CDX に現れないため、部分保存は検出できない
    （アイテムのファイル総数は IRDB からも CDX からも分からない）。
    """
    base = {
        "title": entry.get("title", ""),
        **{k: entry.get(k, "") for k in PRISM_FIELDS},
        "updated": entry.get("updated", ""),
        "irdb_url": entry.get("irdb_url", ""),
        "record_url": entry.get("uri", ""),
        "record_id": rid,
    }
    pdfs = pick_latest_pdfs(cdx_rows)
    if pdfs:
        return [{
            **base,
            "status": "ok",
            "file_url": p["original"],
            "capture_timestamp": p["timestamp"],
            "capture_date": capture_date(p["timestamp"]),
            "mimetype": p.get("mimetype", ""),
            "length": p.get("length", ""),
            "n_captures": p["n_captures"],
            "item_files_captured": len(pdfs),
            "archive_url": archive_url(p["timestamp"], p["original"]),
        } for p in pdfs]
    status = "no_pdf" if cdx_rows else "no_capture"
    return [{**base, "status": status, "item_files_captured": 0}]


def summarize(rows: Iterable[dict]) -> Tuple[Dict[str, int], int]:
    """(アイテム単位の status 内訳, ok 行 = 保存を確認できた PDF の総数) を返す。

    アイテム単位では、PDF が複数あっても 1 件と数え、1 行でも ok なら ok とする。
    """
    item_status: Dict[str, str] = {}
    pdf_files = 0
    for r in rows:
        if r["status"] == "ok":
            pdf_files += 1
        if item_status.get(r["record_id"]) != "ok":
            item_status[r["record_id"]] = r["status"]
    by_status: Dict[str, int] = {}
    for st in item_status.values():
        by_status[st] = by_status.get(st, 0) + 1
    return by_status, pdf_files


def write_csv(path: Path, rows: Iterable[dict]) -> None:
    # Excel で文字化けしないよう BOM 付き UTF-8
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ---------------------------------------------------------------------------
# HTTP（ペーシングとリトライ）
# ---------------------------------------------------------------------------

class Http:
    def __init__(self, interval: float, contact: str = ""):
        self.interval = interval
        self._last = 0.0
        ua = "wb-fulltext-csv/0.1"
        if contact:
            ua += f" ({contact})"
        self.headers = {"User-Agent": ua}

    def get(self, url: str, retries: int = 5, timeout: int = 60) -> str:
        delay = max(self.interval, 1.0)
        for attempt in range(retries + 1):
            wait = self.interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                    ra = e.headers.get("Retry-After") if e.headers else None
                    sleep = float(ra) if ra and ra.isdigit() else delay * (2 ** attempt)
                    warn(f"HTTP {e.code}: {sleep:.0f} 秒待って再試行 ({attempt + 1}/{retries})")
                    time.sleep(sleep)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < retries:
                    sleep = delay * (2 ** attempt)
                    warn(f"通信エラー {e}: {sleep:.0f} 秒待って再試行 ({attempt + 1}/{retries})")
                    time.sleep(sleep)
                    continue
                raise
            finally:
                self._last = time.monotonic()
        raise RuntimeError("unreachable")


def warn(msg: str) -> None:
    print(f"[warn] {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"[info] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 取得処理
# ---------------------------------------------------------------------------

class IncompleteListing(Exception):
    """IRDB の一覧が totalResults に達する前に終わった（不完全な一覧）。"""


def fetch_entries(http: Http, query: str, count: int = 100) -> List[dict]:
    """IRDB OpenSearch（本文あり）を全ページ取得する。

    次の start は、要求件数ではなく実際に取得できた件数から決める（応答が count 未満でも
    残りを取りこぼさない）。totalResults に達する前に空ページが返ったら IncompleteListing。
    検索結果が正当に 0 件（totalResults=0）の場合は [] を返す。
    """
    entries: List[dict] = []
    while True:
        params = {"q": query, "fulltext": "1", "count": str(count),
                  "start": str(len(entries) + 1), "format": "atom"}
        text = http.get(f"{IRDB_OPENSEARCH}?{urllib.parse.urlencode(params)}")
        total, page = parse_atom(text)
        if not page:
            if len(entries) < total:
                raise IncompleteListing(
                    f"start={len(entries) + 1} で 0 件（取得 {len(entries)}/{total} 件）")
            break
        entries.extend(page)
        info(f"IRDB: {len(entries)}/{total} 件")
        if len(entries) >= total:
            break
    return entries


ENTRIES_CACHE_VERSION = 2


def load_entries_cache(path: Path, query: str) -> Optional[List[dict]]:
    """IRDB 一覧のキャッシュを読む。検索条件が違う・旧形式・壊れている場合は None。"""
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if (not isinstance(obj, dict) or obj.get("version") != ENTRIES_CACHE_VERSION
            or obj.get("query") != query or not isinstance(obj.get("entries"), list)):
        return None  # 旧形式（単なるリスト）も含め、条件を確認できないキャッシュは使わない
    return obj["entries"]


def save_entries_cache(path: Path, query: str, entries: List[dict]) -> None:
    obj = {"version": ENTRIES_CACHE_VERSION, "query": query, "entries": entries}
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def repair_jsonl_tail(path: Path) -> None:
    """中断で壊れた JSONL の末尾（改行なしの不完全な行）を、追記前に取り除く。

    末尾が改行で終わっていなければ、最後の行を JSON として読めるか確かめる。読めれば改行だけ補い、
    読めなければその行を切り捨てる。それより前の正常な行はそのまま残す。
    """
    if not path.exists():
        return
    data = path.read_bytes()
    if not data or data.endswith(b"\n"):
        return
    cut = data.rfind(b"\n") + 1
    try:
        json.loads(data[cut:].decode("utf-8"))
        path.write_bytes(data + b"\n")
    except (UnicodeDecodeError, json.JSONDecodeError):
        path.write_bytes(data[:cut])
        warn(f"{path.name}: 末尾の不完全な行を除去しました")


def cdx_for_record(http: Http, host: str, rid: str) -> List[dict]:
    rows: List[dict] = []
    for kind in PATH_KINDS:
        params = [
            ("url", f"{host}/{kind}/{rid}/files/"),
            ("matchType", "prefix"),
            ("filter", "statuscode:200"),
            ("fl", ",".join(CDX_FIELDS)),
            ("output", "json"),
        ]
        text = http.get(f"{CDX_ENDPOINT}?{urllib.parse.urlencode(params)}")
        rows.extend(parse_cdx(text))
    return rows


BULK_PAGE_SIZE = 5000
BULK_TIMEOUT = 180  # 1 回の照会に 20〜60 秒かかる実測があるため長めにする


def load_bulk_cache(path: Path, host: str) -> Dict[str, dict]:
    """バルク取得の進捗を読む。kind ごとに {rows, resume, done}。

    各行は 1 ページ分 {"host", "kind", "rows", "resume"}。resume が null のページが最終ページ。
    host が違う行（host 欄の無い旧形式を含む）は使わない。
    """
    state: Dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # 中断で壊れた末尾行
            if not isinstance(obj, dict) or obj.get("host") != host:
                continue
            st = state.setdefault(obj["kind"], {"rows": [], "resume": None, "done": False})
            st["rows"].extend(obj["rows"])
            st["resume"] = obj["resume"]
            st["done"] = obj["resume"] is None
    return state


def fetch_bulk(http: Http, host: str, cache_path: Path,
               page_size: int = BULK_PAGE_SIZE) -> List[dict]:
    """`{host}/record/` 配下の 200 の capture を resumeKey で分割取得する。

    ページごとにキャッシュへ追記するので、中断しても同じ cache で再実行すれば続きから進む。
    """
    repair_jsonl_tail(cache_path)  # 読み込みより先に。UTF-8 の途中で切れていても読めるようにする
    state = load_bulk_cache(cache_path, host)
    with cache_path.open("a", encoding="utf-8") as cf:
        for kind in PATH_KINDS:
            st = state.setdefault(kind, {"rows": [], "resume": None, "done": False})
            while not st["done"]:
                params = [
                    ("url", f"{host}/{kind}/"),
                    ("matchType", "prefix"),
                    ("filter", "statuscode:200"),
                    ("fl", ",".join(CDX_FIELDS)),
                    ("output", "json"),
                    ("limit", str(page_size)),
                    ("showResumeKey", "true"),
                ]
                if st["resume"]:
                    params.append(("resumeKey", st["resume"]))
                text = http.get(f"{CDX_ENDPOINT}?{urllib.parse.urlencode(params)}",
                                timeout=BULK_TIMEOUT)
                rows, resume = parse_cdx_page(text)
                cf.write(json.dumps({"host": host, "kind": kind, "rows": rows,
                                     "resume": resume}, ensure_ascii=False) + "\n")
                cf.flush()
                st["rows"].extend(rows)
                st["resume"] = resume
                st["done"] = resume is None
                info(f"CDX バルク ({kind}): 累計 {len(st['rows'])} 行"
                     + ("（完了）" if st["done"] else ""))
    return [r for st in state.values() for r in st["rows"]]


_FILES_PATH_RE = re.compile(r"^/(?:%s)/(\d+)/files/" % "|".join(PATH_KINDS))


def group_by_record(rows: Iterable[dict]) -> Dict[str, List[dict]]:
    """レコード番号ごとにまとめる。対象は `/record/{id}/files/…` の本文ファイルだけ。

    CDX の prefix 照会は `/record/` で終わる指定でも `/records/…` を返す（実測）。
    詳細ページ・export・`/records/{id}/file_details/x.pdf`（HTML。拡張子だけ .pdf）が
    混ざると PDF と誤判定するため、ここでパスを絞る。
    """
    grouped: Dict[str, List[dict]] = {}
    for r in rows:
        m = _FILES_PATH_RE.match(urllib.parse.urlsplit(r.get("original", "")).path)
        if m:
            grouped.setdefault(m.group(1), []).append(r)
    return grouped


def load_cdx_cache(path: Path, host: str) -> Dict[str, List[dict]]:
    """レコードごとの CDX 結果のキャッシュ。host が違う行（旧形式を含む）は使わない。"""
    cache: Dict[str, List[dict]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # 中断で壊れた末尾行
            if not isinstance(obj, dict) or obj.get("host") != host:
                continue
            cache[obj["rid"]] = obj["rows"]
    return cache


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True,
                    help="対象リポジトリのホスト（例: jircas.repo.nii.ac.jp）。"
                         "URI がこのホストのものだけを対象にする")
    ap.add_argument("--query", default=None,
                    help="IRDB OpenSearch の q（既定: --host と同じ）")
    ap.add_argument("--out", default="wayback_fulltext.csv")
    ap.add_argument("--cache-dir", default=".wb_cache")
    ap.add_argument("--interval", type=float, default=1.0, help="リクエスト間隔（秒）")
    ap.add_argument("--limit", type=int, default=0, help="スモーク用: 先頭 N 件のみ")
    ap.add_argument("--refresh", action="store_true", help="IRDB の一覧を取り直す")
    ap.add_argument("--bulk", action="store_true",
                    help="レコードごとの照会ではなく、/record/ 配下を一括で分割取得して突き合わせる")
    ap.add_argument("--bulk-page-size", type=int, default=BULK_PAGE_SIZE,
                    help=f"--bulk の 1 回あたり取得行数（既定 {BULK_PAGE_SIZE}）")
    args = ap.parse_args()
    host = args.host.strip().lower()
    query = args.query if args.query else host

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    http = Http(args.interval, os.environ.get("WB_CONTACT", "").strip())

    entries_path = cache_dir / "irdb_entries.json"
    entries = None if args.refresh else load_entries_cache(entries_path, query)
    if entries is not None:
        if entries and any(k not in entries[0] for k in (*PRISM_FIELDS, "updated")):
            info("IRDB 一覧のキャッシュに prism:* / updated の項目が無いため、取り直します")
            entries = None
        else:
            info(f"IRDB 一覧をキャッシュから読み込み: {len(entries)} 件")
    if entries is None:
        try:
            entries = fetch_entries(http, query)
        except IncompleteListing as ex:
            # 不完全な一覧はキャッシュしない
            warn(f"IRDB 一覧が不完全です ({ex})。キャッシュせず終了します。再実行してください")
            return 1
        save_entries_cache(entries_path, query, entries)

    dups = count_duplicates(entries)
    if dups:
        warn(f"IRDB 一覧に重複が {dups} 件あります。ページ送り中に並びが変わり、同数の欠落が"
             "ある可能性が高いので、--refresh で取り直してください")

    targets, skipped = [], 0
    for e in entries:
        rid = record_id(e["uri"])
        if uri_host(e["uri"]) != host or not rid:
            skipped += 1
            continue
        targets.append((e, rid))
    info(f"対象 {len(targets)} 件（ホスト不一致またはレコード番号なしで除外 {skipped} 件）")
    if args.limit:
        targets = targets[:args.limit]

    bulk: Optional[Dict[str, List[dict]]] = None
    if args.bulk:
        try:
            bulk = group_by_record(fetch_bulk(http, host, cache_dir / "cdx_bulk.jsonl",
                                              args.bulk_page_size))
        except Exception as ex:  # noqa: BLE001
            # 取得が不完全なまま突き合わせると、保存済みを no_capture と誤判定する
            warn(f"バルク取得に失敗 ({ex})。CSV は出力しません。同じコマンドで再実行すると続きから進みます")
            return 1
        info(f"バルク取得: {len(bulk)} レコード分")

    cdx_cache_path = cache_dir / "cdx.jsonl"
    repair_jsonl_tail(cdx_cache_path)  # 読み込みより先に（UTF-8 の途中で切れていても読めるようにする）
    cdx_cache = load_cdx_cache(cdx_cache_path, host)
    out_rows: List[dict] = []
    errors = 0
    with cdx_cache_path.open("a", encoding="utf-8") as cf:
        for i, (entry, rid) in enumerate(targets, 1):
            if bulk is not None:
                rows = bulk.get(rid, [])
            elif rid in cdx_cache:
                rows = cdx_cache[rid]
            else:
                try:
                    rows = cdx_for_record(http, host, rid)
                except Exception as ex:  # noqa: BLE001 - 1 件の失敗で全体を止めない
                    warn(f"record {rid}: CDX 照会に失敗 ({ex})")
                    errors += 1
                    out_rows.append({**build_rows(entry, rid, [])[0], "status": "cdx_error",
                                     "item_files_captured": ""})  # 不明（0 ではない）
                    continue
                cf.write(json.dumps({"host": host, "rid": rid, "rows": rows},
                                    ensure_ascii=False) + "\n")
                cf.flush()
            out_rows.extend(build_rows(entry, rid, rows))
            if i % 50 == 0:
                info(f"CDX: {i}/{len(targets)} 件")

    write_csv(Path(args.out), out_rows)

    by_status, pdf_files = summarize(out_rows)
    n_items = sum(by_status.values())
    judged = n_items - by_status.get("cdx_error", 0)  # 照会に失敗した分は分母から除く
    info(f"出力: {args.out}（{len(out_rows)} 行）")
    info(f"アイテム単位（{n_items} 件）の内訳: "
         + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    if judged:
        ok_items = by_status.get("ok", 0)
        info(f"アイテム単位の保存確認率: {ok_items}/{judged} = {ok_items / judged:.1%}"
             "（1 件でも PDF の保存を確認できたアイテム。部分保存は検出できない）")
    info(f"ファイル単位: 保存を確認できた PDF {pdf_files} 件（ok の行数）")
    if errors:
        warn(f"CDX 照会の失敗 {errors} 件。同じコマンドで再実行すると、失敗分だけ取り直します")
    return 0


if __name__ == "__main__":
    sys.exit(main())
