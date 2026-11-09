#!/usr/bin/env python3
"""IRDB OpenSearch から機関リポジトリのアイテムを列挙し、本文ファイル(PDF)の
Wayback Machine 上の最新アーカイブ URL を CSV に出力する（標準ライブラリのみ）。

流れ:
  1. IRDB OpenSearch (fulltext=1, Atom) で全件取得し、URI のホストで自機関分に絞る
  2. URI からレコード番号を取り出す（本文 URL のファイル名は IRDB に無いため）
  3. Wayback CDX にレコード番号配下 (/record/{id}/files/) を
     プレフィックス照会し、PDF ファイルごとに最新の capture を選ぶ（--capture-before で日時の上限）
  4. 結果を CSV に出力する（ダウンロードは担当者が手元で行う）

注意:
  - CDX の mimetype は信頼しない。実測で PDF が application/octet-stream になる例が
    あったため、判定は URL の拡張子 (.pdf) で行い、mimetype は参考列として残す。
  - Wayback はクローラの到達結果しか保存しない。保存が無いものは status=no_capture。
  - レート制限は公式に未公表。既定は 1 リクエスト/秒、429/503 は待って再試行する。

使い方:
  WB_CONTACT="連絡先(任意)" python3 wb_fulltext_csv.py --host jircas.repo.nii.ac.jp --out result.csv
  python3 wb_fulltext_csv.py --host HOST --no-license --out result.csv  # ライセンス情報を取得しない（速い）
  python3 wb_fulltext_csv.py --host HOST --no-bulk --out result.csv     # レコードごとに CDX を照会する
  python3 wb_fulltext_csv.py --host HOST --limit 20 --out smoke.csv     # スモーク
既定は --bulk（/record/ 配下の一括取得）と --license（ライセンス情報の取得）。
--license は、Wayback に保存された WEKO3 の /records/{id}/export/json を 1 件ずつ取得し、
権利情報とファイルの licensetype から CC ライセンスかどうかを判定する（初回は数百件で数十分かかる。
結果はキャッシュし、2 回目以降は新しい保存が現れたものだけ取り直す）。--no-license では
ライセンスの列を空にする。--no-bulk のときもライセンス情報は取得しない。
--host は対象リポジトリのホスト（必須）。--query（IRDB の q）の既定は --host と同じ。
--limit と --refresh 以外のオプションと WB_CONTACT は、カレントディレクトリの .env に
WB_HOST=jircas.repo.nii.ac.jp のように書いておける（キーは WB_ + オプション名を大文字にし、
- を _ にしたもの。真偽は true/false。UTF-8、BOM 付きも可）。--env-file で別のファイルを指定できる。
優先順はコマンドライン > 環境変数 > .env > 既定値。書き方は .env.example を参照。
中断しても、同じ --cache-dir で再実行すれば取得済みの分は再利用される。
キャッシュは検索条件（IRDB 一覧）とホスト・照会条件（CDX）ごとに識別され、条件が違えば再利用しない。

保存履歴の digest（Issue #5）:
  CDX の digest（Wayback が保存したバイト列の SHA-1）を、同じ本文 URL の capture どうしで比べ、
  integrity_status（consistent / changed / single / unknown）を CSV に出す。比べるのは取得済みの
  比較可能な履歴だけで、digest の一致は真正性を保証しない。changed はバイト列の差異であり、
  内容の変更（正規の差し替えと改ざんは区別できない）のほか、途中で切れた保存や gzip のまま
  保存されたものでも起きる。changed でも archive_url は出す（判断は利用者が行う）。
  --capture-before 2026-10-28 のように指定すると、UTC でその日時より前（未満）の保存版から
  最新のものを採用する（日付だけなら 00:00:00 UTC = 日本時間 09:00）。上限より後にしか保存が無い
  ファイルは status=after_cutoff になる。ライセンス判定に使う export JSON にも同じ上限を適用する。
  上限の指定はリスクを下げる手段であり、安全を保証しない。

途中で切れている可能性（Issue #7）:
  採用した保存版より length が大きく、digest の違う保存版があれば possibly_truncated=1 にする
  （クローラのサイズ上限で途中までしか保存されず、開けない PDF がある）。採用は変えない。
  larger_capture_date は全期間でいちばん大きい版の保存日、alt_capture_date と alt_archive_url は
  上限より前でいちばん大きい版（同じ長さなら新しいもの）。CDX の length は圧縮後の長さなので推定にすぎず、
  正規の差し替えや gzip のまま保存された版でも 1 になり、保存が 1 件だけのものは検出できない。
  空は「この条件に当たらない」という意味で、正常を確かめたものではない。
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ATOM = "{http://www.w3.org/2005/Atom}"
OSNS = "{http://a9.com/-/spec/opensearch/1.1/}"
PRISM = "{http://prismstandard.org/namespaces/basic/2.0/}"

IRDB_OPENSEARCH = "https://irdb.nii.ac.jp/opensearch/search"
CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
CDX_FIELDS = ["timestamp", "original", "mimetype", "statuscode", "length", "digest"]
CDX_FILTER = "statuscode:200"
# キャッシュ（cdx_bulk.jsonl、cdx.jsonl）の各行に書く照会条件。現在の条件と違う行（条件の無い旧形式を
# 含む）は使わない。取得項目やフィルタを変えたら、キャッシュは自動で取り直しになる。
CDX_QUERY = {"fl": CDX_FIELDS, "filter": CDX_FILTER}
# 本文ファイル URL は /record/{id}/files/ 固定とする（暫定）。詳細ページは /records/{id} だが、
# ファイルは常に単一の保存先を指すため複数表記を照会する必要性は低いと判断した。
PATH_KINDS = ("record",)

# IRDB の prism:* 要素。CSV の列名は prism: を除いたもの
PRISM_FIELDS = ("issn", "publicationName", "volume", "number",
                "startingPage", "endingPage", "publicationDate")

TRUNCATION_COLUMNS = ("possibly_truncated", "larger_capture_date", "alt_capture_date",
                      "alt_archive_url")

CSV_COLUMNS = [
    "status", "title", *PRISM_FIELDS, "updated", "irdb_url", "record_url", "record_id",
    "file_url", "capture_timestamp", "capture_date", "mimetype", "length",
    "n_captures", "item_files_captured", "archive_url",
    *("license_status", "license", "rights", "rights_uri", "licensetype",
      "license_capture_date", "license_source_url"),
    # 保存履歴の digest の比較（Issue #5）。既存の列の位置を変えないよう末尾に足す
    *("capture_digest", "digest_variants", "integrity_status", "digest_first_seen",
      "later_digest_date"),
    # 途中で切れている可能性（Issue #7）。採用版が digest の違う保存版より小さいもの
    *TRUNCATION_COLUMNS,
]

# WEKO3 のファイル情報の licensetype のうち CC のもの（NC/ND 付きも CC として扱う）。
# license_no（なし）・license_free（自由入力）・license_note（つくばで実測）などは CC ではない。
CC_LICENSETYPES = {
    "license_0": "CC BY 4.0", "license_1": "CC BY-SA 4.0", "license_2": "CC BY-ND 4.0",
    "license_3": "CC BY-NC 4.0", "license_4": "CC BY-NC-SA 4.0", "license_5": "CC BY-NC-ND 4.0",
    "license_6": "CC BY 3.0", "license_7": "CC BY-SA 3.0", "license_8": "CC BY-ND 3.0",
    "license_9": "CC BY-NC 3.0", "license_10": "CC BY-NC-SA 3.0", "license_11": "CC BY-NC-ND 3.0",
    "license_12": "CC0 1.0",
}


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


_DIGEST_RE = re.compile(r"[A-Z2-7]{32}")


def normalize_digest(v: object) -> str:
    """CDX の digest を比較できる形にする。比較できない値（欠損）は空文字を返す。

    前後の空白を除き、先頭の `sha1:` を大文字小文字を区別せずに除いてから英大文字にする。
    32 文字の Base32（A-Z、2-7）だけを比較可能とし、None、空文字、`-`、それ以外の形式は欠損とする。
    """
    s = str(v or "").strip()
    if s[:5].lower() == "sha1:":
        s = s[5:].strip()
    s = s.upper()
    return s if _DIGEST_RE.fullmatch(s) else ""


def integrity_status(digests: List[str]) -> str:
    """1 ファイルの全期間の capture の digest（正規化済み。欠損は空）から判定する。

    優先順: 比較可能な digest が 2 種類以上なら changed（一部が欠損でも、確認できた違いを示す）。
    変更を確認できず、欠損があれば unknown。全部揃い、2 回以上で 1 種類なら consistent。
    全部揃い、1 回だけなら single（比較対象として取得できた有効な capture が 1 件という意味）。
    """
    variants = {d for d in digests if d}
    if len(variants) >= 2:
        return "changed"
    if any(not d for d in digests):
        return "unknown"
    return "consistent" if len(digests) >= 2 else "single"


def length_int(r: dict) -> Optional[int]:
    """CDX の length を整数で返す。空や数値でない値は None（例外にしない）。"""
    try:
        return int(str(r.get("length", "")).strip())
    except ValueError:
        return None


def _largest(caps: List[dict]) -> Optional[dict]:
    """length が最大の capture（同じなら新しいもの）。空なら None。"""
    return max(caps, key=lambda c: (length_int(c), c["timestamp"])) if caps else None


def truncation_check(caps: List[dict], digests: List[str], adopted: Optional[dict],
                     before: str = "") -> dict:
    """採用版が、digest の違うほかの保存版より小さいか（途中で切れている可能性）を調べる（Issue #7）。

    比較対象は、digest が欠損しておらず採用版と違い、length が整数として読めて採用版より大きい capture。
    CDX の length は圧縮後の WARC レコード長なので、これは本文サイズの推定にすぎない。
    警告（possibly_truncated）と larger_capture_date は全期間の比較対象で決め、代替リンク
    （alt_capture_date、alt_archive_url）は上限より前（未満）の比較対象から選ぶ。どちらも length が
    最大のもの（同じなら新しいもの）。該当しなければ 4 つとも空（正常を確かめたという意味ではない）。
    """
    empty = {"possibly_truncated": "", "larger_capture_date": "",
             "alt_capture_date": "", "alt_archive_url": ""}
    if adopted is None:
        return empty
    ad = normalize_digest(adopted.get("digest"))
    ad_len = length_int(adopted)
    if not ad or ad_len is None:
        return empty
    larger = [c for c, d in zip(caps, digests)
              if d and d != ad and (length_int(c) or 0) > ad_len]
    if not larger:
        return empty
    top = _largest(larger)
    alt = _largest([c for c in larger if not before or c["timestamp"] < before])
    return {
        "possibly_truncated": "1",
        "larger_capture_date": capture_date(top["timestamp"]),
        "alt_capture_date": capture_date(alt["timestamp"]) if alt else "",
        "alt_archive_url": archive_url(alt["timestamp"], alt["original"]) if alt else "",
    }


def summarize_pdf_files(rows: Iterable[dict], before: str = "") -> List[dict]:
    """status 200 で length が正の PDF capture を、ファイルごとにまとめる。

    `before`（14 桁の UTC timestamp。空なら制限なし）より前の capture のうち最新を採用する
    （`adopted`。上限より前に無ければ None）。digest の比較と `n_captures` は、上限より後も含む
    取得済みの全期間の capture で行う（上限より後の変化こそ知りたい情報のため）。

    返す各要素: original（採用版の URL。無ければ最新の capture の URL）, adopted, n_captures,
    digest_variants, integrity_status, capture_digest, digest_first_seen, later_digest_date と、
    `truncation_check` の 4 項目（possibly_truncated、larger_capture_date、alt_capture_date、
    alt_archive_url）。日付の 2 列は、採用版の digest が欠損なら空。後続の欠損 digest は
    「別の digest」と数えない。
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
        digests = [normalize_digest(c.get("digest")) for c in caps]
        eligible = [c for c in caps if not before or c["timestamp"] < before]
        adopted = max(eligible, key=lambda r: r["timestamp"]) if eligible else None
        ad = normalize_digest(adopted.get("digest")) if adopted else ""
        first_seen = later = ""
        if ad:
            first_seen = capture_date(min(c["timestamp"] for c, d in zip(caps, digests) if d == ad))
            diff = [c["timestamp"] for c, d in zip(caps, digests)
                    if d and d != ad and c["timestamp"] > adopted["timestamp"]]
            later = capture_date(min(diff)) if diff else ""
        out.append({
            "original": (adopted or max(caps, key=lambda r: r["timestamp"]))["original"],
            "adopted": adopted,
            "n_captures": len(caps),
            "digest_variants": len({d for d in digests if d}),
            "integrity_status": integrity_status(digests),
            "capture_digest": ad,
            "digest_first_seen": first_seen,
            "later_digest_date": later,
            **truncation_check(caps, digests, adopted, before),
        })
    return out


def pick_latest_pdfs(rows: Iterable[dict], before: str = "") -> List[dict]:
    """status 200 の PDF capture を、ファイルごとに採用する 1 件へ絞る（上限より前に無いファイルは除く）。

    返す各行は、採用した capture に `n_captures`（そのファイルの有効な capture の総数）を付けたもの。
    length が不正な capture は無効として除く。
    """
    return [{**f["adopted"], "n_captures": f["n_captures"]}
            for f in summarize_pdf_files(rows, before) if f["adopted"]]


def archive_url(timestamp: str, original: str) -> str:
    """ツールバー無し・リンク書き換え無しの原本を返す URL（id_ 指定）。"""
    return f"https://web.archive.org/web/{timestamp}id_/{original}"


def capture_date(timestamp: str) -> str:
    try:
        return datetime.strptime(timestamp[:14], "%Y%m%d%H%M%S").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def build_rows(entry: dict, rid: str, cdx_rows: List[dict], before: str = "") -> List[dict]:
    """1 アイテム分の CSV 行（PDF ごとに 1 行。無ければ状態だけの 1 行）を作る。

    `item_files_captured` は、このアイテムで保存を確認でき、上限より前の保存版を採用できた PDF の数。
    保存されなかったファイルは CDX に現れないため、部分保存は検出できない
    （アイテムのファイル総数は IRDB からも CDX からも分からない）。

    `before` を指定したとき、上限より後にしか有効な capture が無いファイルは `after_cutoff` の行にする。
    この行は file_url と全期間の集計（n_captures、digest_variants、integrity_status）だけを埋め、
    採用版に依存する列（capture_*、mimetype、length、archive_url、digest の日付）は空にする。
    """
    base = {
        "title": entry.get("title", ""),
        **{k: entry.get(k, "") for k in PRISM_FIELDS},
        "updated": entry.get("updated", ""),
        "irdb_url": entry.get("irdb_url", ""),
        "record_url": entry.get("uri", ""),
        "record_id": rid,
    }
    files = summarize_pdf_files(cdx_rows, before)
    if files:
        adopted_n = sum(1 for f in files if f["adopted"])
        out = []
        for f in files:
            row = {
                **base,
                "file_url": f["original"],
                "n_captures": f["n_captures"],
                "item_files_captured": adopted_n,
                "digest_variants": f["digest_variants"],
                "integrity_status": f["integrity_status"],
            }
            p = f["adopted"]
            if p is None:
                out.append({**row, "status": "after_cutoff"})
                continue
            out.append({
                **row,
                "status": "ok",
                "capture_timestamp": p["timestamp"],
                "capture_date": capture_date(p["timestamp"]),
                "mimetype": p.get("mimetype", ""),
                "length": p.get("length", ""),
                "archive_url": archive_url(p["timestamp"], p["original"]),
                "capture_digest": f["capture_digest"],
                "digest_first_seen": f["digest_first_seen"],
                "later_digest_date": f["later_digest_date"],
                **{k: f[k] for k in TRUNCATION_COLUMNS},
            })
        return out
    status = "no_pdf" if cdx_rows else "no_capture"
    return [{**base, "status": status, "item_files_captured": 0}]


# アイテム単位の status の優先順（1 行でも ok なら ok。次に after_cutoff）
_ITEM_STATUS_RANK = {"ok": 2, "after_cutoff": 1}


def summarize(rows: Iterable[dict]) -> Tuple[Dict[str, int], int]:
    """(アイテム単位の status 内訳, ok 行 = 保存を確認できた PDF の総数) を返す。

    アイテム単位では、PDF が複数あっても 1 件と数え、ok > after_cutoff > その他の順で代表させる。
    """
    item_status: Dict[str, str] = {}
    pdf_files = 0
    for r in rows:
        if r["status"] == "ok":
            pdf_files += 1
        cur = item_status.get(r["record_id"])
        if cur is None or _ITEM_STATUS_RANK.get(r["status"], 0) > _ITEM_STATUS_RANK.get(cur, 0):
            item_status[r["record_id"]] = r["status"]
    by_status: Dict[str, int] = {}
    for st in item_status.values():
        by_status[st] = by_status.get(st, 0) + 1
    return by_status, pdf_files


INTEGRITY_ORDER = ("consistent", "changed", "single", "unknown")


def summarize_integrity(rows: Iterable[dict]) -> Tuple[Dict[str, int], int]:
    """ファイル単位（ok と after_cutoff の行）の integrity_status の内訳と、
    採用した保存版より後に別の digest を観測したファイルの数を返す。"""
    by: Dict[str, int] = {}
    later = 0
    for r in rows:
        if r.get("status") not in ("ok", "after_cutoff"):
            continue
        st = r.get("integrity_status") or "unknown"
        by[st] = by.get(st, 0) + 1
        if r.get("later_digest_date"):
            later += 1
    return by, later


def summarize_truncation(rows: Iterable[dict]) -> Tuple[int, int]:
    """ok の行のうち、途中で切れている可能性があるファイルの数と、そのうち代替リンクがあるものの数。"""
    flagged = [r for r in rows if r.get("status") == "ok" and r.get("possibly_truncated")]
    return len(flagged), sum(1 for r in flagged if r.get("alt_archive_url"))


def parse_capture_before(s: Optional[str]) -> str:
    """--capture-before の値を 14 桁の UTC timestamp にする。空文字は ""（制限なし）を返す。

    受け付ける形は YYYY-MM-DD、YYYYMMDD、YYYYMMDDhhmmss。日付だけのときは、その日の 00:00:00 UTC。
    採用するのは、timestamp がこの値より前（未満）の capture。None を返さないのは、None が
    resolve_options で「未指定」を意味するため（空文字で下位の設定を打ち消せるようにする）。
    """
    v = (s or "").strip()
    if not v:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        v = v.replace("-", "")
    if re.fullmatch(r"\d{8}", v):
        v += "000000"
    if not re.fullmatch(r"\d{14}", v):
        raise ValueError(f"日時として解釈できません: {s!r}（YYYY-MM-DD、YYYYMMDD、YYYYMMDDhhmmss）")
    try:
        datetime.strptime(v, "%Y%m%d%H%M%S")
    except ValueError:
        raise ValueError(f"存在しない日時です: {s!r}") from None
    return v


def describe_before(before: str) -> str:
    """上限の説明（例: 2026-10-28 00:00:00 UTC（日本時間 2026-10-28 09:00:00）より前）。"""
    t = datetime.strptime(before, "%Y%m%d%H%M%S")
    jst = t + timedelta(hours=9)
    return f"{t:%Y-%m-%d %H:%M:%S} UTC（日本時間 {jst:%Y-%m-%d %H:%M:%S}）より前"


# ---------------------------------------------------------------------------
# ライセンス情報（Wayback に保存された WEKO3 の /records/{id}/export/json から読む）
# ---------------------------------------------------------------------------

_EXPORT_JSON_RE = re.compile(r"^/records/(\d+)/export/json$")


def group_export_json(rows: Iterable[dict], before: str = "") -> Dict[str, List[dict]]:
    """`/records/{id}/export/json` の有効な capture（200、length が正）をレコードごとに新しい順で返す。

    `--bulk` の `/record/` 配下の一括取得には、`/records/…` の行も含まれる（実測）。
    `before` を指定したときは、それより前の capture だけを残す。候補が無いレコードはキーごと除く
    （呼び出し側が `caps[0]` を参照するため）。
    """
    grouped: Dict[str, List[dict]] = {}
    for r in rows:
        m = _EXPORT_JSON_RE.match(urllib.parse.urlsplit(r.get("original", "")).path)
        if m and str(r.get("statuscode")) == "200" and has_valid_length(r):
            if before and not r["timestamp"] < before:
                continue
            grouped.setdefault(m.group(1), []).append(r)
    for caps in grouped.values():
        caps.sort(key=lambda r: r["timestamp"], reverse=True)
    return grouped


def extract_license_info(record: dict) -> dict:
    """export JSON から、権利情報とファイルごとの licensetype を取り出す。

    プロパティキー（`item_30002_rights6`、`item_5_rights_12`、`item_1742563285404` など）は
    リポジトリやアイテムタイプごとに違うので使わない。次のサブフィールドで見分ける。
      - 権利情報: `attribute_value_mlt` の要素に `subitem_rights` か `subitem_rights_resource` がある
      - ファイル情報: `attribute_type` が `file`
    表示名（`attribute_name`）は編集できるので使わない。
    """
    meta = record.get("metadata") if isinstance(record, dict) else None
    rights: List[dict] = []
    files: List[dict] = []
    if not isinstance(meta, dict):
        return {"rights": rights, "files": files}
    for v in meta.values():
        if not isinstance(v, dict):
            continue
        vals = [x for x in (v.get("attribute_value_mlt") or []) if isinstance(x, dict)]
        if v.get("attribute_type") == "file":
            files += [{"filename": str(x.get("filename") or ""),
                       "licensetype": str(x.get("licensetype") or "")} for x in vals]
            continue
        for x in vals:
            if "subitem_rights" in x or "subitem_rights_resource" in x:
                text = str(x.get("subitem_rights") or "").strip()
                uri = str(x.get("subitem_rights_resource") or "").strip()
                if text or uri:
                    rights.append({"text": text, "uri": uri})
    return {"rights": rights, "files": files}


_CC_URL_RE = re.compile(
    r"creativecommons\.org/(?:licenses/([a-z]+(?:-[a-z]+)*)|publicdomain/(zero))/(\d+(?:\.\d+)?)",
    re.I)


def cc_license_name(s: str) -> Optional[str]:
    """文字列中の CC の URL から `CC BY-NC-ND 4.0` / `CC0 1.0` のような名前を返す。無ければ None。

    http/https、`/deed.en`・`/deed/en`・末尾スラッシュの有無は問わない。権利情報の本文に URL だけを
    書いている例（実測）もあるので、URI 欄に限らず本文も対象にする。
    """
    m = _CC_URL_RE.search(s or "")
    if not m:
        return None
    if m.group(2):
        return f"CC0 {m.group(3)}"
    return f"CC {m.group(1).upper()} {m.group(3)}"


def license_for_file(info: Optional[dict], filename: Optional[str]) -> dict:
    """1 行分のライセンス列を作る。`filename` が None なら、そのアイテムの PDF 全体で判定する。

    license_status:
      cc        権利情報の CC URL か、ファイルの licensetype が CC で、互いに矛盾しない
      check     要確認（CC の種類が食い違う、または licensetype が license_no なのに権利情報が CC）
      not_cc    権利情報はあるが CC ではない
      no_rights 権利情報もなく、licensetype も CC ではない
      no_json   export JSON を取得できなかった
    """
    if info is None:
        return {"license_status": "no_json"}
    rights = info.get("rights", [])
    files = info.get("files", [])
    if filename is None:
        targets = [f for f in files if f["filename"].lower().endswith(".pdf")]
    else:
        targets = [f for f in files if f["filename"] == filename]
    types = sorted({f["licensetype"] for f in targets if f["licensetype"]})
    names = {n for r in rights for n in (cc_license_name(r["uri"]), cc_license_name(r["text"])) if n}
    rights_cc = bool(names)
    names |= {CC_LICENSETYPES[t] for t in types if t in CC_LICENSETYPES}
    if len(names) > 1 or (rights_cc and "license_no" in types):
        status = "check"
    elif names:
        status = "cc"
    elif rights:
        status = "not_cc"
    else:
        status = "no_rights"
    return {
        "license_status": status,
        "license": " / ".join(sorted(names)),
        "rights": " | ".join(r["text"] for r in rights if r["text"]),
        "rights_uri": " | ".join(r["uri"] for r in rights if r["uri"]),
        "licensetype": " | ".join(types),
    }


def add_license_columns(rows: List[dict], lic: Optional[dict]) -> List[dict]:
    """build_rows の行に、ライセンス列を足す。`lic` はキャッシュの 1 レコード分（無ければ None）。"""
    info = lic.get("info") if lic else None
    extra = {}
    if info is not None:
        extra = {"license_capture_date": capture_date(lic["timestamp"]),
                 "license_source_url": archive_url(lic["timestamp"], lic["original"])}
    out = []
    for r in rows:
        fname = None
        if r.get("file_url"):
            fname = urllib.parse.unquote(urllib.parse.urlsplit(r["file_url"]).path.rsplit("/", 1)[-1])
        out.append({**r, **license_for_file(info, fname), **extra})
    return out


def decode_body(data: bytes, content_encoding: str = "") -> str:
    """応答本文を文字列にする。Wayback は原本の `Content-Encoding: gzip` のまま返すことがある（実測）。"""
    if "gzip" in (content_encoding or "").lower() or data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data.decode("utf-8")


def summarize_licenses(rows: Iterable[dict]) -> Tuple[Dict[str, int], int]:
    """(アイテム単位の license_status 内訳, PDF の保存があり CC の行を持つアイテム数) を返す。

    アイテムの代表値は、CC の行があれば cc、なければ最初の行の値とする。
    """
    item: Dict[str, str] = {}
    ok_cc = set()
    for r in rows:
        st = r.get("license_status", "")
        if r["record_id"] not in item or st == "cc":
            item[r["record_id"]] = st
        if st == "cc" and r["status"] == "ok":
            ok_cc.add(r["record_id"])
    by: Dict[str, int] = {}
    for st in item.values():
        by[st] = by.get(st, 0) + 1
    return by, len(ok_cc)


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
                    return decode_body(resp.read(), resp.headers.get("Content-Encoding", ""))
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


def fetch_entries_complete(http: Http, query: str, passes: int = 5) -> List[dict]:
    """一覧を最大 `passes` 回取り、IRDB 詳細ページ URL で和集合をとる。

    IRDB の並びは同順位（`updated` が同じ日のものが 98%）の間で要求ごとに入れ替わるらしく、
    ページ送りで重複と欠落が同数出る（2026-11-08 に 2 回続けて 36 件）。取り直すたびに欠落する
    アイテムが変わるので、重ね合わせれば揃う。重複のない件数が 1 回目の件数に達したら止める。
    `passes` 回取っても揃わなければ IncompleteListing（重複を除いた後なので、main の重複チェック
    では欠落に気付けない。不完全な一覧をキャッシュしないよう、ここで失敗にする）。
    """
    merged: Dict[str, dict] = {}
    expected = 0
    for n in range(1, passes + 1):
        page = fetch_entries(http, query)
        expected = expected or len(page)
        for e in page:
            merged.setdefault(e.get("irdb_url", ""), e)
        info(f"IRDB 一覧 {n} 回目: 重複を除いて {len(merged)}/{expected} 件")
        if len(merged) >= expected:
            return list(merged.values())
    raise IncompleteListing(f"{passes} 回取得しても {len(merged)}/{expected} 件しか揃わない")


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


def is_current_query(obj: object) -> bool:
    """キャッシュの 1 行が、現在の照会条件（取得項目とフィルタ）で取得したものか。"""
    return isinstance(obj, dict) and obj.get("query") == CDX_QUERY


def drop_stale_cache_lines(path: Path) -> int:
    """CDX のキャッシュから、現在の照会条件と違う行（条件の無い旧形式を含む）を取り除く。

    旧形式の行を残すと、読み込みのたびに読み飛ばすことになり、取り直しの警告も毎回出てしまう。
    使えない行なので、ファイルから消して 1 回だけ警告する。読めない行も除く（末尾の不完全な行は
    先に repair_jsonl_tail で処理しておく）。戻り値は除いた行数。
    """
    if not path.exists():
        return 0
    keep: List[str] = []
    dropped = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            obj = None
        if is_current_query(obj):
            keep.append(line + "\n")
        elif line.strip():
            dropped += 1
    if dropped:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("".join(keep), encoding="utf-8")
        os.replace(tmp, path)
        warn(f"{path.name}: 取得項目が古いキャッシュ {dropped} 行を削除しました。"
             "digest を含めて取り直します")
    return dropped


def cdx_for_record(http: Http, host: str, rid: str) -> List[dict]:
    rows: List[dict] = []
    for kind in PATH_KINDS:
        params = [
            ("url", f"{host}/{kind}/{rid}/files/"),
            ("matchType", "prefix"),
            ("filter", CDX_FILTER),
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

    各行は 1 ページ分 {"host", "kind", "query", "rows", "resume"}。resume が null のページが最終ページ。
    host が違う行（host 欄の無い旧形式を含む）と、照会条件（query）が現在と違う行は使わない。
    """
    state: Dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # 中断で壊れた末尾行
            if not is_current_query(obj) or obj.get("host") != host:
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
    drop_stale_cache_lines(cache_path)
    state = load_bulk_cache(cache_path, host)
    with cache_path.open("a", encoding="utf-8") as cf:
        for kind in PATH_KINDS:
            st = state.setdefault(kind, {"rows": [], "resume": None, "done": False})
            while not st["done"]:
                params = [
                    ("url", f"{host}/{kind}/"),
                    ("matchType", "prefix"),
                    ("filter", CDX_FILTER),
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
                cf.write(json.dumps({"host": host, "kind": kind, "query": CDX_QUERY,
                                     "rows": rows, "resume": resume}, ensure_ascii=False) + "\n")
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
    """レコードごとの CDX 結果のキャッシュ。1 行 {"host", "rid", "query", "rows"}。

    host が違う行と、照会条件（query）が現在と違う行（どちらも旧形式を含む）は使わない。
    """
    cache: Dict[str, List[dict]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # 中断で壊れた末尾行
            if not is_current_query(obj) or obj.get("host") != host:
                continue
            cache[obj["rid"]] = obj["rows"]
    return cache


EXPORT_TRIES = 3        # 1 レコードで試す capture の数（新しい順）
EXPORT_TIMEOUT = 90


def load_license_cache(path: Path, host: str) -> Dict[str, dict]:
    """export JSON の取得結果のキャッシュ。1 行 1 レコード {host, rid, latest, timestamp, original, info}。

    `latest` は取得時点で最新だった capture の timestamp。CDX に新しい capture が現れたら取り直す。
    `info` が null の行は、試したすべての capture が 404 か JSON でなかったもの。
    """
    cache: Dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # 中断で壊れた末尾行
            if isinstance(obj, dict) and obj.get("host") == host and "rid" in obj:
                cache[obj["rid"]] = obj
    return cache


def fetch_license(http: Http, host: str, rid: str, caps: List[dict]) -> Optional[dict]:
    """新しい capture から順に export JSON を取り、キャッシュに書く 1 行を返す。

    404 や JSON でない応答は次の capture へ進む。通信エラーが残った場合は None
    （キャッシュせず、次回の実行で取り直す）。

    `latest` は「ここまでは確定的に処理した」capture の timestamp。新しい capture が通信エラーで
    取れず、古い capture で判定した場合は、使った capture の timestamp を入れる。CDX の最新と一致
    しないので、次回の実行で新しい capture を取り直す（古い権利情報のまま固定しないため）。
    """
    transient = False
    for c in caps[:EXPORT_TRIES]:
        try:
            text = http.get(archive_url(c["timestamp"], c["original"]), retries=3,
                            timeout=EXPORT_TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                continue
            transient = True
            continue
        except Exception:  # noqa: BLE001 - 通信エラーは次回に回す
            transient = True
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or not isinstance(record.get("metadata"), dict):
            continue
        return {"host": host, "rid": rid,
                "latest": c["timestamp"] if transient else caps[0]["timestamp"],
                "timestamp": c["timestamp"], "original": c["original"],
                "info": extract_license_info(record)}
    if transient:
        return None
    return {"host": host, "rid": rid, "latest": caps[0]["timestamp"], "info": None}


def license_cache_usable(obj: dict, before: str = "") -> bool:
    """キャッシュの 1 行を、上限 `before` の下で使えるか。

    判定に使った export JSON の timestamp が上限より前であること。上限なしで取得した新しい
    キャッシュを、取り直しに失敗したときに使ってしまわないため（Issue #5 の計画レビュー P1）。
    info が null の行（取得を試したが読めなかった）は、判定に何も使わないので常に使える。
    """
    if not before or obj.get("info") is None:
        return True
    return str(obj.get("timestamp", "")) < before


def collect_licenses(http: Http, host: str, rids: List[str], export_caps: Dict[str, List[dict]],
                     cache_path: Path, before: str = "") -> Tuple[Dict[str, dict], int]:
    """対象レコードのライセンス情報を集める。(rid → キャッシュ行, 通信エラーで未取得の件数)。

    `before` を指定したときは、`export_caps` も上限で絞ったものを渡す。返す行は、判定に使った
    export JSON の timestamp が上限より前のものだけ（満たさない古いキャッシュは、取り直しに
    失敗しても返さない。そのレコードは no_json になる）。
    """
    repair_jsonl_tail(cache_path)
    cache = load_license_cache(cache_path, host)
    todo = [r for r in rids if r in export_caps
            and cache.get(r, {}).get("latest") != export_caps[r][0]["timestamp"]]
    if todo:
        info(f"export JSON: {len(todo)} 件を取得します（1 件あたり数秒。中断しても続きから進みます）")
    errors = 0
    with cache_path.open("a", encoding="utf-8") as cf:
        for i, rid in enumerate(todo, 1):
            obj = fetch_license(http, host, rid, export_caps[rid])
            if obj is None:
                errors += 1
                # 以前の取得結果があれば、古くてもそれを使う
                warn(f"record {rid}: export JSON の取得に失敗（次回の実行で取り直します）")
                continue
            cf.write(json.dumps(obj, ensure_ascii=False) + "\n")
            cf.flush()
            cache[rid] = obj
            if obj["latest"] != export_caps[rid][0]["timestamp"]:
                errors += 1
                warn(f"record {rid}: 最新の export JSON を取得できず、古い保存で判定しました"
                     "（次回の実行で取り直します）")
            if i % 50 == 0:
                info(f"export JSON: {i}/{len(todo)} 件")
    return {r: cache[r] for r in rids if r in cache and r in export_caps
            and license_cache_usable(cache[r], before)}, errors


NO_JSON_REASONS = {
    "no_export": "export JSON の有効な保存（status 200、length が正）が無い",
    "none_before": "export JSON の保存はあるが、指定日時より前の保存が無い",
    "fetch_failed": "通信エラーで取得できなかった（次回の実行で取り直す）",
    "unreadable": "試した保存がすべて 404 か JSON でなかった",
}


def license_missing_reasons(rids: Iterable[str], export_all: Dict[str, List[dict]],
                            export_caps: Dict[str, List[dict]],
                            licenses: Dict[str, dict]) -> Dict[str, int]:
    """ライセンスが no_json になったレコードを、理由（NO_JSON_REASONS のキー）ごとに数える。

    `export_all` は上限で絞る前、`export_caps` は絞った後の候補。`licenses` は collect_licenses の結果。
    通信障害が「上限のため」に紛れないよう、上限前の候補が無いもの（none_before）と、候補はあるが
    取得できなかったもの（fetch_failed）を分ける。
    """
    out: Dict[str, int] = {}
    for rid in rids:
        lic = licenses.get(rid)
        if lic is not None and lic.get("info") is not None:
            continue  # no_json ではない
        if rid not in export_all:
            reason = "no_export"
        elif rid not in export_caps:
            reason = "none_before"
        elif lic is not None and lic.get("latest") == export_caps[rid][0]["timestamp"]:
            reason = "unreadable"  # 最新の候補まで試し終えて、読めなかった
        else:
            reason = "fetch_failed"
        out[reason] = out.get(reason, 0) + 1
    return out


DEFAULT_ENV_FILE = ".env"


def parse_dotenv(text: str) -> Dict[str, str]:
    """.env の KEY=VALUE を読む。空行と # で始まる行は無視し、前後の空白と、値を囲む
    引用符（" か '）を除く。行頭の export は許容する。エスケープ・変数展開・行末コメント・
    複数行の値は扱わない（値に # を含められるようにするため）。BOM は呼び出し側で
    utf-8-sig として読んで除く。"""
    out: Dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export ") or s.startswith("export\t"):
            s = s[len("export"):].strip()
        key, sep, value = s.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def parse_bool(s: str) -> bool:
    v = s.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"真偽値として解釈できません: {s!r}（true/false, 1/0, yes/no, on/off）")


# .env（または環境変数）で指定できる項目: キー -> (args の属性名, 変換関数, コード上の既定値)。
# --limit と --refresh は 1 回限りの指定なので対象外。WB_CONTACT はコマンドラインのオプションが無い。
# 既定値はここにまとめる（build_parser の既定値は None にして、未指定を区別する）。
ENV_OPTIONS: Dict[str, Tuple[str, object, object]] = {
    "WB_HOST": ("host", str, None),
    "WB_QUERY": ("query", str, None),  # None は「--host と同じ」（main で補う）
    "WB_OUT": ("out", str, "wayback_fulltext.csv"),
    "WB_CACHE_DIR": ("cache_dir", str, ".wb_cache"),
    "WB_INTERVAL": ("interval", float, 1.0),
    "WB_BULK": ("bulk", parse_bool, True),
    "WB_BULK_PAGE_SIZE": ("bulk_page_size", int, BULK_PAGE_SIZE),
    "WB_LICENSE": ("license", parse_bool, True),
    "WB_CAPTURE_BEFORE": ("capture_before", parse_capture_before, ""),  # "" は制限なし
    "WB_CONTACT": ("contact", str, ""),
}
_ENV_KEY_BY_DEST = {dest: key for key, (dest, _, _) in ENV_OPTIONS.items()}


def _env_help(dest: str, text: str, default_label: Optional[str] = None) -> str:
    """help の文に、ENV_OPTIONS の既定値と .env のキーを付ける。"""
    key = _ENV_KEY_BY_DEST[dest]
    if default_label is None:
        default = ENV_OPTIONS[key][2]
        if isinstance(default, bool):
            default_label = "有効" if default else "無効"
        else:
            default_label = str(default)
    return f"{text}（既定: {default_label}。.env では {key}）"


def resolve_options(args: argparse.Namespace, environ: Dict[str, str],
                    dotenv: Dict[str, str]) -> List[str]:
    """コマンドラインで指定されなかった（None の）項目を、環境変数・.env・コード上の既定値の
    順に補う。型変換は採用した値にだけ行う（使わない下位の設定が不正でも止めない）。
    変換に失敗したら、キーと出どころを含む ValueError を出す。戻り値は警告の一覧。"""
    warnings = [f".env の {k} は未知のキーなので無視します" for k in dotenv
                if k.startswith("WB_") and k not in ENV_OPTIONS]
    for key, (dest, conv, default) in ENV_OPTIONS.items():
        if getattr(args, dest, None) is not None:
            continue  # コマンドラインの指定が最優先
        for source, values in (("環境変数", environ), (".env", dotenv)):
            if key in values:
                raw = values[key]
                try:
                    value = conv(raw)
                except ValueError as ex:
                    raise ValueError(f"{source} の {key}={raw!r} が不正です（{ex}）") from None
                break
        else:
            value = default
        setattr(args, dest, value)
    return warnings


def _cli_capture_before(s: str) -> str:
    """argparse 用。不正な値を argparse のエラーとして表示する。"""
    try:
        return parse_capture_before(s)
    except ValueError as ex:
        raise argparse.ArgumentTypeError(str(ex)) from None


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # ENV_OPTIONS にある項目の既定値は None（未指定）。値は resolve_options で補う
    ap.add_argument("--host", default=None,
                    help=_env_help("host", "対象リポジトリのホスト（例: jircas.repo.nii.ac.jp）。"
                                   "URI がこのホストのものだけを対象にする。"
                                   "この指定か .env の WB_HOST が必須", "なし"))
    ap.add_argument("--query", default=None,
                    help=_env_help("query", "IRDB OpenSearch の q", "--host と同じ"))
    ap.add_argument("--out", default=None, help=_env_help("out", "出力する CSV"))
    ap.add_argument("--cache-dir", default=None, help=_env_help("cache_dir", "キャッシュの保存先"))
    ap.add_argument("--interval", type=float, default=None,
                    help=_env_help("interval", "リクエスト間隔（秒）"))
    ap.add_argument("--limit", type=int, default=0, help="スモーク用: 先頭 N 件のみ")
    ap.add_argument("--refresh", action="store_true", help="IRDB の一覧を取り直す")
    ap.add_argument("--bulk", action=argparse.BooleanOptionalAction, default=None,
                    help=_env_help("bulk", "/record/ 配下を一括で分割取得して突き合わせる。"
                                   "--no-bulk でレコードごとに照会する"))
    ap.add_argument("--bulk-page-size", type=int, default=None,
                    help=_env_help("bulk_page_size", "--bulk の 1 回あたり取得行数"))
    ap.add_argument("--license", action=argparse.BooleanOptionalAction, default=None,
                    help=_env_help("license", "Wayback に保存された export JSON からライセンス情報を"
                                   "取得する。--no-license で取得せず、ライセンスの列を空にする。"
                                   "--bulk のときだけ有効"))
    ap.add_argument("--capture-before", type=_cli_capture_before, default=None, metavar="DATE",
                    help=_env_help("capture_before",
                                   "採用する保存版の日時の上限。UTC で、この日時より前（未満）の保存版から"
                                   "最新の有効なものを選ぶ。YYYY-MM-DD、YYYYMMDD、YYYYMMDDhhmmss で指定し、"
                                   "日付だけなら 00:00:00 UTC（日本時間 09:00）。例: 2026-10-28 は "
                                   "2026-10-28 00:00:00 UTC より前。digest の比較は上限より後も含む全期間で行う。"
                                   "ライセンス判定に使う export JSON にも適用する。空文字で制限なし",
                                   "制限なし"))
    ap.add_argument("--env-file", default=None,
                    help=f"設定ファイル（既定: カレントディレクトリの {DEFAULT_ENV_FILE}。無ければ読まない）。"
                         "優先順はコマンドライン > 環境変数 > .env > 既定値")
    return ap


def load_options(argv: Optional[List[str]] = None,
                 environ: Optional[Dict[str, str]] = None) -> argparse.Namespace:
    """引数をすべて解析してから（--help はここで終了する）.env を読み、値を補う。"""
    ap = build_parser()
    args = ap.parse_args(argv)
    env_path = Path(args.env_file or DEFAULT_ENV_FILE)
    dotenv: Dict[str, str] = {}
    if env_path.is_file():
        try:
            dotenv = parse_dotenv(env_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError) as ex:
            ap.error(f"{env_path} を読めません（{ex}）")
        info(f"設定ファイルを読み込み: {env_path}")
    elif args.env_file:
        ap.error(f"--env-file に指定したファイルがありません: {env_path}")
    if environ is None:
        environ = dict(os.environ)
    try:
        for msg in resolve_options(args, environ, dotenv):
            warn(msg)
    except ValueError as ex:
        ap.error(str(ex))
    args.host = (args.host or "").strip()
    if not args.host:
        ap.error("--host か .env の WB_HOST を指定してください")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = load_options(argv)
    if args.license and not args.bulk:
        # export JSON の保存先は、一括取得した CDX の一覧から探すため
        warn("--no-bulk のときはライセンス情報を取得しません（ライセンスの列は空になります）")
        args.license = False
    host = args.host.strip().lower()
    query = args.query if args.query else host
    before = args.capture_before or ""
    if before:
        info(f"採用する保存版の日時の上限: {describe_before(before)}")

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    http = Http(args.interval, args.contact.strip())

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
            entries = fetch_entries_complete(http, query)
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
    licenses: Optional[Dict[str, dict]] = None
    export_all: Dict[str, List[dict]] = {}
    export_caps: Dict[str, List[dict]] = {}
    license_errors = 0
    if args.bulk:
        try:
            bulk_rows = fetch_bulk(http, host, cache_dir / "cdx_bulk.jsonl", args.bulk_page_size)
        except Exception as ex:  # noqa: BLE001
            # 取得が不完全なまま突き合わせると、保存済みを no_capture と誤判定する
            warn(f"バルク取得に失敗 ({ex})。CSV は出力しません。同じコマンドで再実行すると続きから進みます")
            return 1
        bulk = group_by_record(bulk_rows)
        info(f"バルク取得: {len(bulk)} レコード分")
        if args.license:
            export_all = group_export_json(bulk_rows)
            export_caps = group_export_json(bulk_rows, before)
            info(f"export JSON の保存があるレコード: {len(export_all)} 件"
                 + (f"（うち指定日時より前の保存があるもの {len(export_caps)} 件）" if before else ""))
            licenses, license_errors = collect_licenses(
                http, host, [rid for _, rid in targets], export_caps,
                cache_dir / "export_json.jsonl", before)

    cdx_cache_path = cache_dir / "cdx.jsonl"
    repair_jsonl_tail(cdx_cache_path)  # 読み込みより先に（UTF-8 の途中で切れていても読めるようにする）
    if bulk is None:
        drop_stale_cache_lines(cdx_cache_path)
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
                                     "item_files_captured": "",  # 不明（0 ではない）
                                     "integrity_status": "unknown"})
                    continue
                cf.write(json.dumps({"host": host, "rid": rid, "query": CDX_QUERY, "rows": rows},
                                    ensure_ascii=False) + "\n")
                cf.flush()
            item_rows = build_rows(entry, rid, rows, before)
            if licenses is not None:
                item_rows = add_license_columns(item_rows, licenses.get(rid))
            out_rows.extend(item_rows)
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
        label = ("指定日時より前の保存版を確認できた率" if before else "アイテム単位の保存確認率")
        info(f"{label}: {ok_items}/{judged} = {ok_items / judged:.1%}"
             "（1 件でも PDF の保存を確認できたアイテム。部分保存は検出できない）")
        if before and by_status.get("after_cutoff"):
            info(f"指定日時以降の保存しか無いアイテム（after_cutoff）: {by_status['after_cutoff']} 件")
    info(f"ファイル単位: 保存を確認できた PDF {pdf_files} 件（ok の行数）")
    by_int, later = summarize_integrity(out_rows)
    if by_int:
        info("保存履歴の digest（ファイル単位。取得済みの比較可能な履歴での判定）: "
             + ", ".join(f"{k}={by_int[k]}" for k in INTEGRITY_ORDER if k in by_int))
        info(f"採用した保存版より後に、別の digest の保存を観測したファイル: {later} 件")
        n_trunc, n_alt = summarize_truncation(out_rows)
        info(f"採用した保存版が、digest の違う保存版より小さいファイル（途中で切れている可能性）: "
             f"{n_trunc} 件（うち、{'基準日時より前に' if before else ''}大きい保存版へのリンクを出せるもの "
             f"{n_alt} 件）")
    if licenses is not None:
        by_lic, ok_cc = summarize_licenses(out_rows)
        info("ライセンス（アイテム単位）: "
             + ", ".join(f"{k}={v}" for k, v in sorted(by_lic.items())))
        info(f"PDF の保存があり、ライセンスが CC のアイテム: {ok_cc} 件")
        reasons = license_missing_reasons([rid for _, rid in targets], export_all, export_caps,
                                          licenses)
        for k, text in NO_JSON_REASONS.items():
            if reasons.get(k):
                info(f"ライセンス未確認（no_json）の理由: {text} {reasons[k]} 件")
    if errors:
        warn(f"CDX 照会の失敗 {errors} 件。同じコマンドで再実行すると、失敗分だけ取り直します")
    if license_errors:
        warn(f"export JSON の取得失敗 {license_errors} 件。同じコマンドで再実行すると、失敗分だけ取り直します")
    return 0


if __name__ == "__main__":
    sys.exit(main())
