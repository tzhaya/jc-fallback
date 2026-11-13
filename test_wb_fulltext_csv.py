"""wb_fulltext_csv.py のオフラインテスト（ネットワーク不使用・固定 fixture）。

実行: python3 test_wb_fulltext_csv.py
"""

import csv
import json
import sys
import tempfile
from pathlib import Path

import wb_fulltext_csv as w

# IRDB OpenSearch (Atom) の実応答から抜粋（2026-11-07 取得）。
ATOM_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/" xml:lang="ja">
<title>IRDB OpenSearch</title>
<opensearch:totalResults>929</opensearch:totalResults>
<opensearch:startIndex>1</opensearch:startIndex>
<opensearch:itemsPerPage>20</opensearch:itemsPerPage>
<entry>
<title>Local Biochar Use for Sustainable Agriculture in Asia</title>
<link href="https://irdb.nii.ac.jp/07496/0006763363"/>
<URI>https://jircas.repo.nii.ac.jp/records/2000879</URI>
<prism:publicationDate>2026-03-25</prism:publicationDate>
<irname>国際農林水産業研究センター</irname>
</entry>
<entry>
<title>他機関のアイテム</title>
<link href="https://irdb.nii.ac.jp/00001/0000000001"/>
<URI>https://example.repo.nii.ac.jp/records/5</URI>
</entry>
</feed>
"""

# お示しいただいた実際の CDX 応答（mimetype が application/octet-stream）。
REAL_CDX = json.dumps([
    ["timestamp", "original", "mimetype", "statuscode", "length"],
    ["20250722192940",
     "https://jircas.repo.nii.ac.jp/record/2000885/files/gars-5_ja.pdf",
     "application/octet-stream", "200", "3427839"],
])


def test_parse_atom():
    total, entries = w.parse_atom(ATOM_FIXTURE)
    assert total == 929
    assert len(entries) == 2
    e = entries[0]
    assert e["irdb_url"] == "https://irdb.nii.ac.jp/07496/0006763363"
    assert e["uri"] == "https://jircas.repo.nii.ac.jp/records/2000879"
    assert e["publicationDate"] == "2026-03-25"
    assert entries[1]["publicationDate"] == ""
    assert entries[1]["issn"] == ""  # 要素が無ければ空


# 指定いただいた prism:* の例。publicationName は言語違いが 2 つある場合も確認する。
PRISM_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
<opensearch:totalResults>1</opensearch:totalResults>
<entry>
<title>t</title>
<URI>https://jircas.repo.nii.ac.jp/records/2001056</URI>
<prism:issn>2588-3143</prism:issn>
<prism:publicationName lang="en">International journal of horticultural science and technology</prism:publicationName>
<prism:publicationName lang="ja">国際園芸科学技術誌</prism:publicationName>
<prism:volume>13</prism:volume>
<prism:number>3</prism:number>
<prism:startingPage>575</prism:startingPage>
<prism:endingPage>592</prism:endingPage>
<prism:publicationDate>2026-07</prism:publicationDate>
<updated>2026-06-07</updated>
</entry>
</feed>
"""


def test_parse_atom_prism_fields_and_csv_columns():
    _, entries = w.parse_atom(PRISM_FIXTURE)
    e = entries[0]
    assert e["issn"] == "2588-3143" and e["volume"] == "13" and e["number"] == "3"
    assert (e["startingPage"], e["endingPage"], e["publicationDate"]) == ("575", "592", "2026-07")
    # lang の値は出さず、内容だけを " | " でつなぐ
    assert e["publicationName"] == ("International journal of horticultural science and "
                                    "technology | 国際園芸科学技術誌")
    # 列は title の直後に prism: を除いた名前で並ぶ
    cols = w.CSV_COLUMNS
    assert cols[:2] == ["status", "title"]
    assert cols[2:10] == ["issn", "publicationName", "volume", "number",
                          "startingPage", "endingPage", "publicationDate", "updated"]
    assert e["updated"] == "2026-06-07"
    row = w.build_rows(e, "2001056", [])[0]
    assert row["issn"] == "2588-3143" and row["publicationDate"] == "2026-07"
    assert row["updated"] == "2026-06-07"


def test_record_id_and_host():
    assert w.record_id("https://jircas.repo.nii.ac.jp/records/2001056") == "2001056"
    assert w.record_id("https://jircas.repo.nii.ac.jp/record/2000885/files/a.pdf") == "2000885"
    assert w.record_id("https://jircas.repo.nii.ac.jp/search?page=1") is None
    assert w.uri_host("https://JIRCAS.repo.nii.ac.jp/records/1") == "jircas.repo.nii.ac.jp"


def test_parse_cdx_real_and_empty():
    rows = w.parse_cdx(REAL_CDX)
    assert len(rows) == 1 and rows[0]["mimetype"] == "application/octet-stream"
    assert w.parse_cdx("") == []
    assert w.parse_cdx("[]") == []


def test_octet_stream_pdf_is_kept():
    # 実測: mimetype が octet-stream でも、拡張子が .pdf なら PDF として扱う。
    picked = w.pick_latest_pdfs(w.parse_cdx(REAL_CDX))
    assert len(picked) == 1
    assert picked[0]["timestamp"] == "20250722192940"


def test_pick_latest_per_file_ignores_scheme_and_query():
    rows = [
        {"timestamp": "20230101000000", "original": "http://h/record/1/files/a.pdf",
         "mimetype": "application/pdf", "statuscode": "200", "length": "10"},
        {"timestamp": "20250101000000", "original": "https://h/record/1/files/a.pdf?download=1",
         "mimetype": "application/pdf", "statuscode": "200", "length": "11"},
        {"timestamp": "20240101000000", "original": "https://h/record/1/files/b.PDF",
         "mimetype": "application/pdf", "statuscode": "200", "length": "12"},
        {"timestamp": "20260101000000", "original": "https://h/record/1/files/a.pdf",
         "mimetype": "text/html", "statuscode": "404", "length": "1"},
        {"timestamp": "20260101000000", "original": "https://h/record/1/files/data.xlsx",
         "mimetype": "application/vnd.ms-excel", "statuscode": "200", "length": "5"},
    ]
    picked = w.pick_latest_pdfs(rows)
    assert len(picked) == 2  # a.pdf と b.PDF。404 と xlsx は除外
    a = next(p for p in picked if p["original"].lower().split("?")[0].endswith("a.pdf"))
    assert a["timestamp"] == "20250101000000"
    assert a["n_captures"] == 2  # 200 の capture のみ数える（404 は含めない）


def test_archive_url_and_date():
    u = w.archive_url("20250722192940",
                      "https://jircas.repo.nii.ac.jp/record/2000885/files/gars-5_ja.pdf")
    assert u == ("https://web.archive.org/web/20250722192940id_/"
                 "https://jircas.repo.nii.ac.jp/record/2000885/files/gars-5_ja.pdf")
    assert w.capture_date("20250722192940") == "2025-07-22"
    assert w.capture_date("bad") == ""


def test_build_rows_statuses():
    _, entries = w.parse_atom(ATOM_FIXTURE)
    e = entries[0]
    ok = w.build_rows(e, "2000879", w.parse_cdx(REAL_CDX))
    assert ok[0]["status"] == "ok" and ok[0]["archive_url"].endswith("gars-5_ja.pdf")
    none = w.build_rows(e, "2000879", [])
    assert none == [{**none[0], "status": "no_capture"}] and len(none) == 1
    only_html = w.build_rows(e, "2000879", [
        {"timestamp": "20250101000000", "original": "https://h/record/1/files/x.xlsx",
         "mimetype": "x", "statuscode": "200", "length": "1"}])
    assert only_html[0]["status"] == "no_pdf"


def _cap(ts, name, rid="1"):
    return {"timestamp": ts, "original": f"https://h/record/{rid}/files/{name}",
            "mimetype": "application/octet-stream", "statuscode": "200", "length": "1"}


def test_build_rows_multiple_pdfs_item_files_captured():
    _, entries = w.parse_atom(ATOM_FIXTURE)
    rows = w.build_rows(entries[0], "1", [
        _cap("20240101000000", "a.pdf"), _cap("20250101000000", "a.pdf"),
        _cap("20240601000000", "b.pdf")])
    assert [r["file_url"].rsplit("/", 1)[1] for r in rows] == ["a.pdf", "b.pdf"]
    assert [r["n_captures"] for r in rows] == [2, 1]  # ファイル単位の capture 数
    assert all(r["item_files_captured"] == 2 for r in rows)  # アイテム単位の PDF 数
    assert w.build_rows(entries[0], "1", [])[0]["item_files_captured"] == 0


def test_summarize_item_and_file_counts():
    _, entries = w.parse_atom(ATOM_FIXTURE)
    e = entries[0]
    rows = (w.build_rows(e, "1", [_cap("20240101000000", "a.pdf", "1"),
                                  _cap("20240101000000", "b.pdf", "1")])  # ok・PDF 2 件
            + w.build_rows(e, "2", [_cap("20240101000000", "a.pdf", "2")])  # ok・PDF 1 件
            + w.build_rows(e, "3", [])  # no_capture
            + [{**w.build_rows(e, "4", [])[0], "status": "cdx_error"}])
    by_status, pdf_files = w.summarize(rows)
    assert by_status == {"ok": 2, "no_capture": 1, "cdx_error": 1}
    assert pdf_files == 3  # 行数（4 アイテム中 ok 2 件でも、ファイルは 3 件）


# 実測した --bulk 応答の形（limit=5 + showResumeKey=true）。末尾が [] と resumeKey
BULK_PAGE1 = (
    '[["timestamp","original","mimetype","statuscode","length"],\n'
    '["20240711042514","https://jircas.repo.nii.ac.jp/record/2000087/files/gars-j_01.pdf",'
    '"application/octet-stream","200","3629498"],\n'
    '["20260208222603","https://jircas.repo.nii.ac.jp/record/2000087/files/gars-j_01.pdf",'
    '"application/octet-stream","200","3625816"],\n'
    '[],\n["eJwNxkkKgDAM"]]')
BULK_PAGE2 = (
    '[["timestamp","original","mimetype","statuscode","length"],\n'
    '["20250722192940","https://jircas.repo.nii.ac.jp/record/2000885/files/gars-5_ja.pdf",'
    '"application/octet-stream","200","3427839"]]')


def test_parse_cdx_page_resume_key():
    rows, resume = w.parse_cdx_page(BULK_PAGE1)
    assert len(rows) == 2 and rows[0]["statuscode"] == "200"
    assert resume == "eJwNxkkKgDAM"
    rows, resume = w.parse_cdx_page(BULK_PAGE2)  # 最終ページ
    assert len(rows) == 1 and resume is None
    assert w.parse_cdx_page("") == ([], None) and w.parse_cdx_page("[]") == ([], None)


class _FakeHttp:
    def __init__(self, pages):
        self.pages, self.urls = list(pages), []

    def get(self, url, **kw):
        self.urls.append(url)
        return self.pages.pop(0)


def test_fetch_bulk_paginates_and_resumes():
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d) / "cdx_bulk.jsonl"
        http = _FakeHttp([BULK_PAGE1, BULK_PAGE2])
        rows = w.fetch_bulk(http, "jircas.repo.nii.ac.jp", cache, page_size=2)
        assert len(rows) == 3 and len(http.urls) == 2
        assert "resumeKey" not in http.urls[0] and "resumeKey=eJwNxkkKgDAM" in http.urls[1]
        assert "jircas.repo.nii.ac.jp%2Frecord%2F" in http.urls[0] and "limit=2" in http.urls[0]
        grouped = w.group_by_record(rows)
        assert sorted(grouped) == ["2000087", "2000885"] and len(grouped["2000087"]) == 2
        # 完了済みのキャッシュからは通信しない
        http2 = _FakeHttp([])
        assert len(w.fetch_bulk(http2, "jircas.repo.nii.ac.jp", cache)) == 3 and not http2.urls
        # 1 ページ目だけ済んだ状態からは、resumeKey で続きを取る
        cache.write_text(cache.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
        http3 = _FakeHttp([BULK_PAGE2])
        assert len(w.fetch_bulk(http3, "jircas.repo.nii.ac.jp", cache)) == 3
        assert len(http3.urls) == 1 and "resumeKey=eJwNxkkKgDAM" in http3.urls[0]


def test_group_by_record_keeps_only_record_files_path():
    base = "https://jircas.repo.nii.ac.jp"
    rows = [
        _cap("20250101000000", "a.pdf", "1"),  # 本物: /record/1/files/a.pdf
        {"timestamp": "20250101000000", "original": f"{base}/records/1/file_details/a.pdf",
         "mimetype": "text/html", "statuscode": "200", "length": "1"},  # HTML（実測）
        {"timestamp": "20250101000000", "original": f"{base}/records/1",
         "mimetype": "text/html", "statuscode": "200", "length": "1"},
        {"timestamp": "20250101000000", "original": f"{base}/records/1/export/json",
         "mimetype": "text/plain", "statuscode": "200", "length": "1"},
        {"timestamp": "20250101000000", "original": f"{base}/records/1/files/x.pdf",
         "mimetype": "x", "statuscode": "200", "length": "1"},  # /records/…/files/ も対象外
    ]
    grouped = w.group_by_record(rows)
    assert list(grouped) == ["1"] and len(grouped["1"]) == 1
    assert grouped["1"][0]["original"].endswith("/record/1/files/a.pdf")
    # 詳細ページ等だけのアイテムは、キー自体が無い（→ no_capture 扱い）
    assert w.group_by_record(rows[1:4]) == {}


def test_invalid_length_capture_is_ignored():
    # 実測: status 200 でも length が負の capture は、取得すると 404 ページだった（record 2000936）
    bad = {**_cap("20250930180523", "a.pdf"), "length": "-10490845558"}
    good = {**_cap("20250101000000", "a.pdf"), "length": "1220065"}
    assert w.pick_latest_pdfs([bad]) == []
    picked = w.pick_latest_pdfs([good, bad])  # 無効な方が新しくても、有効な方を選ぶ
    assert len(picked) == 1 and picked[0]["timestamp"] == "20250101000000"
    assert picked[0]["n_captures"] == 1
    assert not w.has_valid_length({"length": "0"}) and not w.has_valid_length({"length": "x"})
    assert w.has_valid_length({"length": "5518"}) and w.has_valid_length({})
    _, entries = w.parse_atom(ATOM_FIXTURE)
    assert w.build_rows(entries[0], "1", [bad])[0]["status"] == "no_pdf"


def test_count_duplicates():
    es = [{"irdb_url": "u1"}, {"irdb_url": "u2"}, {"irdb_url": "u1"}]
    assert w.count_duplicates(es) == 1
    assert w.count_duplicates([]) == 0 and w.count_duplicates(es[:2]) == 0


def test_write_csv_roundtrip():
    _, entries = w.parse_atom(ATOM_FIXTURE)
    rows = w.build_rows(entries[0], "2000879", w.parse_cdx(REAL_CDX))
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "out.csv"
        w.write_csv(p, rows)
        raw = p.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")  # BOM 付き（Excel 向け）
        back = list(csv.DictReader(p.open(encoding="utf-8-sig", newline="")))
        assert back[0]["title"].startswith("Local Biochar")
        assert back[0]["capture_date"] == "2025-07-22"


def test_cdx_cache_skips_broken_tail():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cdx.jsonl"
        p.write_text(json.dumps({"host": "h", "rid": "1", "query": w.CDX_QUERY, "rows": []}) + "\n"
                     + '{"host": "h", "rid": "2", "ro', encoding="utf-8")
        cache = w.load_cdx_cache(p, "h")
        assert list(cache) == ["1"]


def test_cdx_cache_is_separated_by_host():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cdx.jsonl"
        lines = [{"host": "a.example", "rid": "1", "query": w.CDX_QUERY,
                  "rows": [_cap("20250101000000", "a.pdf")]},
                 {"rid": "2", "rows": []}]  # host 欄の無い旧形式
        p.write_text("".join(json.dumps(o) + "\n" for o in lines), encoding="utf-8")
        assert list(w.load_cdx_cache(p, "a.example")) == ["1"]
        assert w.load_cdx_cache(p, "b.example") == {}  # 同じ record 番号でも別ホストは使わない


def test_bulk_cache_is_separated_by_host():
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d) / "cdx_bulk.jsonl"
        w.fetch_bulk(_FakeHttp([BULK_PAGE1, BULK_PAGE2]), "a.example", cache, page_size=2)
        http_b = _FakeHttp([BULK_PAGE2])
        rows = w.fetch_bulk(http_b, "b.example", cache)
        assert len(http_b.urls) == 1 and "b.example" in http_b.urls[0]  # 通信して取り直す
        assert len(rows) == 1
        http_a = _FakeHttp([])  # 元のホストのキャッシュは残っていて再利用できる
        assert len(w.fetch_bulk(http_a, "a.example", cache)) == 3 and not http_a.urls


def test_repair_jsonl_tail():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.jsonl"
        good = json.dumps({"a": 1}) + "\n"
        p.write_text(good + '{"a": 2, "b', encoding="utf-8")  # 不完全な末尾
        w.repair_jsonl_tail(p)
        assert p.read_text(encoding="utf-8") == good
        p.write_text(good + json.dumps({"a": 3}), encoding="utf-8")  # 完全だが改行なし
        w.repair_jsonl_tail(p)
        assert p.read_text(encoding="utf-8") == good + json.dumps({"a": 3}) + "\n"
        p.write_text('{"a": 2, "b', encoding="utf-8")  # 1 行だけで不完全
        w.repair_jsonl_tail(p)
        assert p.read_text(encoding="utf-8") == ""
        w.repair_jsonl_tail(Path(d) / "none.jsonl")  # 無いファイルは何もしない


def test_fetch_bulk_resume_after_broken_tail_keeps_new_page():
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d) / "cdx_bulk.jsonl"
        h = "jircas.repo.nii.ac.jp"
        w.fetch_bulk(_FakeHttp([BULK_PAGE1, BULK_PAGE2]), h, cache, page_size=2)
        first = cache.read_text(encoding="utf-8").splitlines()[0]
        cache.write_text(first + "\n" + '{"host": "x", "kind": "rec', encoding="utf-8")
        rows = w.fetch_bulk(_FakeHttp([BULK_PAGE2]), h, cache)
        assert len(rows) == 3
        # 再度読み込んでも、再開後に保存したページが残る
        http = _FakeHttp([])
        assert len(w.fetch_bulk(http, h, cache)) == 3 and not http.urls


def test_cdx_cache_append_after_broken_tail_is_readable():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cdx.jsonl"
        p.write_text(json.dumps({"host": "h", "rid": "1", "query": w.CDX_QUERY, "rows": []}) + "\n"
                     + '{"host": "h", "rid": "2", "ro', encoding="utf-8")
        w.repair_jsonl_tail(p)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"host": "h", "rid": "3", "query": w.CDX_QUERY, "rows": []}) + "\n")
        assert list(w.load_cdx_cache(p, "h")) == ["1", "3"]


def test_resume_after_cut_in_middle_of_multibyte_char():
    # 日本語ファイル名の UTF-8 の途中で書き込みが途切れても、修復→読み込みで復旧できる
    h = "h"
    good = json.dumps({"host": h, "rid": "1", "query": w.CDX_QUERY, "rows": []}, ensure_ascii=False) + "\n"
    broken = json.dumps({"host": h, "rid": "2", "query": w.CDX_QUERY,
                         "rows": [{"original": "x/日本語.pdf"}]}, ensure_ascii=False).encode("utf-8")
    cut = broken.index("本".encode("utf-8")) + 1  # 「本」の 1 バイト目だけ
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cdx.jsonl"
        p.write_bytes(good.encode("utf-8") + broken[:cut])
        w.repair_jsonl_tail(p)
        assert list(w.load_cdx_cache(p, h)) == ["1"]
        # 修復をしない読み込みだけでも例外にならない
        p.write_bytes(good.encode("utf-8") + broken[:cut])
        assert list(w.load_cdx_cache(p, h)) == ["1"]
        # バルクも同様に、再開できて再読込でも残る
        b = Path(d) / "cdx_bulk.jsonl"
        w.fetch_bulk(_FakeHttp([BULK_PAGE1, BULK_PAGE2]), h, b, page_size=2)
        first = b.read_bytes().split(b"\n")[0] + b"\n"
        b.write_bytes(first + broken[:cut])
        assert len(w.fetch_bulk(_FakeHttp([BULK_PAGE2]), h, b)) == 3
        http = _FakeHttp([])
        assert len(w.fetch_bulk(http, h, b)) == 3 and not http.urls


def _atom_page(total, n, offset=0):
    items = "".join(
        f"<entry><title>t{offset + i}</title><link href=\"https://irdb/{offset + i}\"/>"
        f"<URI>https://h/records/{offset + i}</URI></entry>" for i in range(n))
    return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
            f"<opensearch:totalResults>{total}</opensearch:totalResults>{items}</feed>")


def test_fetch_entries_complete_merges_passes_until_no_gap():
    # 1 回目: 0,1 のあと 1 が重複（2 が欠落）。2 回目: 0,2,2（1 が欠落）。和集合で 3 件揃う
    p1 = [_atom_page(3, 2), _atom_page(3, 1, offset=1)]
    p2 = [_atom_page(3, 1), _atom_page(3, 2, offset=2).replace("t3", "t2").replace("/3", "/2")]
    http = _FakeHttp(p1 + p2 + [_atom_page(3, 3)])
    entries = w.fetch_entries_complete(http, "q")
    assert sorted(e["irdb_url"] for e in entries) == ["https://irdb/0", "https://irdb/1", "https://irdb/2"]
    assert len(http.urls) == 4  # 揃った時点で止まる（3 回目は取らない）


def test_fetch_entries_complete_raises_when_still_incomplete():
    # 毎回 0,1 と 1 の重複（2 が欠落）。5 回重ねても 2/3 件なので、不完全として失敗させる
    one_pass = [_atom_page(3, 2), _atom_page(3, 1, offset=1)]
    http = _FakeHttp(one_pass * 5)
    try:
        w.fetch_entries_complete(http, "q", passes=5)
    except w.IncompleteListing:
        pass
    else:
        raise AssertionError("IncompleteListing が出ていない")
    assert len(http.urls) == 10


def test_fetch_entries_short_page_continues_from_actual_count():
    # totalResults=3、要求 100 件に対し最初の応答が 2 件。次は start=3（101 ではない）
    http = _FakeHttp([_atom_page(3, 2), _atom_page(3, 1, offset=2)])
    entries = w.fetch_entries(http, "q", count=100)
    assert [e["title"] for e in entries] == ["t0", "t1", "t2"]
    assert "start=1&" in http.urls[0] and "start=3&" in http.urls[1]


def test_fetch_entries_empty_page_before_total_is_incomplete():
    http = _FakeHttp([_atom_page(3, 2), _atom_page(3, 0)])
    try:
        w.fetch_entries(http, "q")
    except w.IncompleteListing:
        pass
    else:
        raise AssertionError("IncompleteListing が出るはず")
    # 1 件目から空でも totalResults > 0 なら不完全
    try:
        w.fetch_entries(_FakeHttp([_atom_page(5, 0)]), "q")
    except w.IncompleteListing:
        pass
    else:
        raise AssertionError("IncompleteListing が出るはず")


def test_fetch_entries_legitimately_zero():
    http = _FakeHttp([_atom_page(0, 0)])
    assert w.fetch_entries(http, "q") == [] and len(http.urls) == 1


def test_entries_cache_is_keyed_by_query():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "irdb_entries.json"
        assert w.load_entries_cache(p, "a") is None
        w.save_entries_cache(p, "a", [{"title": "x"}])
        assert w.load_entries_cache(p, "a") == [{"title": "x"}]
        assert w.load_entries_cache(p, "b") is None  # 検索条件が違えば使わない
        p.write_text(json.dumps([{"title": "old"}]), encoding="utf-8")  # 旧形式
        assert w.load_entries_cache(p, "a") is None
        p.write_text("{broken", encoding="utf-8")
        assert w.load_entries_cache(p, "a") is None
        w.save_entries_cache(p, "a", [])  # 正当な 0 件はキャッシュできる
        assert w.load_entries_cache(p, "a") == []


# ---------------------------------------------------------------------------
# ライセンス情報（export JSON）
# ---------------------------------------------------------------------------

# jircas 2000879 の export JSON（Wayback 20250812102524）から抜粋。
EXPORT_JIRCAS = {
    "id": 2000879,
    "metadata": {
        "item_type_id": "40039",
        "item_30002_rights6": {
            "attribute_name": "権利情報",
            "attribute_value_mlt": [
                {"subitem_rights": "Creative Commons Attribution 4.0 International",
                 "subitem_rights_language": "en",
                 "subitem_rights_resource": "https://creativecommons.org/licenses/by/4.0/deed.en"},
                {"subitem_rights": "©Japan International Research Center for Agricultural Sciences (JIRCAS), 2025",
                 "subitem_rights_language": "en"},
            ]},
        "item_30002_file35": {
            "attribute_name": "ファイル情報", "attribute_type": "file",
            "attribute_value_mlt": [
                {"filename": "gars-e_4.pdf", "licensetype": "license_0", "accessrole": "open_access"},
                {"filename": "gars-4_en-_Thumbnail.jpg", "licensetype": "license_0"},
            ]},
        "item_30002_title0": {"attribute_name": "タイトル",
                              "attribute_value_mlt": [{"subitem_title": "x"}]},
    },
}


def _other_itemtype(rights_vals, files):
    """つくばのように、キーが item_5_rights_12 / タイムスタンプ / item_files の形のもの。"""
    return {"metadata": {
        "item_5_rights_12": {"attribute_name": "Rights", "attribute_value_mlt": rights_vals[:1]},
        "item_1742563285404": {"attribute_name": "権利情報（2）", "attribute_value_mlt": rights_vals[1:]},
        "item_files": {"attribute_name": "ファイル", "attribute_type": "file",
                       "attribute_value_mlt": files},
    }}


def test_extract_license_info_jircas():
    info = w.extract_license_info(EXPORT_JIRCAS)
    assert len(info["rights"]) == 2
    assert info["rights"][0]["uri"].startswith("https://creativecommons.org/licenses/by/4.0")
    assert [f["filename"] for f in info["files"]] == ["gars-e_4.pdf", "gars-4_en-_Thumbnail.jpg"]
    lic = w.license_for_file(info, "gars-e_4.pdf")
    assert lic["license_status"] == "cc" and lic["license"] == "CC BY 4.0", lic
    assert lic["licensetype"] == "license_0"
    assert "JIRCAS" in lic["rights"]


def test_extract_license_info_ignores_property_keys():
    rec = _other_itemtype(
        [{"subitem_rights": "© 2024 Someone"},
         {"subitem_rights": "x", "subitem_rights_resource": "http://creativecommons.org/licenses/by-nc-nd/4.0/"}],
        [{"filename": "a.pdf"}])
    info = w.extract_license_info(rec)
    assert len(info["rights"]) == 2 and info["files"][0]["filename"] == "a.pdf"
    lic = w.license_for_file(info, "a.pdf")
    assert lic["license_status"] == "cc" and lic["license"] == "CC BY-NC-ND 4.0", lic


def test_cc_license_name_variants():
    assert w.cc_license_name("https://creativecommons.org/licenses/by/4.0/deed.en") == "CC BY 4.0"
    assert w.cc_license_name("http://creativecommons.org/licenses/by-nc-nd/4.0/deed/en") == "CC BY-NC-ND 4.0"
    assert w.cc_license_name("https://creativecommons.org/licenses/by-nc/4.0") == "CC BY-NC 4.0"
    assert w.cc_license_name("https://creativecommons.org/publicdomain/zero/1.0/") == "CC0 1.0"
    assert w.cc_license_name("This article is licensed under ... http://creativecommons.org/licenses/by/4.0/.") == "CC BY 4.0"
    assert w.cc_license_name("https://www.jircas.go.jp/ja/about/guide") is None
    assert w.cc_license_name("Creative Commons Attribution 4.0 International") is None
    assert w.cc_license_name("") is None


def test_license_cc_url_only_in_rights_text():
    info = {"rights": [{"text": "https://creativecommons.org/licenses/by/4.0/", "uri": ""}], "files": []}
    assert w.license_for_file(info, "a.pdf")["license_status"] == "cc"


def test_license_filetype_only_is_cc():
    info = {"rights": [], "files": [{"filename": "a.pdf", "licensetype": "license_5"}]}
    lic = w.license_for_file(info, "a.pdf")
    assert lic["license_status"] == "cc" and lic["license"] == "CC BY-NC-ND 4.0", lic


def test_license_conflict_is_check():
    info = {"rights": [{"text": "", "uri": "https://creativecommons.org/licenses/by/4.0/"}],
            "files": [{"filename": "a.pdf", "licensetype": "license_3"}]}
    lic = w.license_for_file(info, "a.pdf")
    assert lic["license_status"] == "check" and lic["license"] == "CC BY 4.0 / CC BY-NC 4.0", lic
    info["files"][0]["licensetype"] = "license_no"
    assert w.license_for_file(info, "a.pdf")["license_status"] == "check"


def test_license_not_cc_and_no_rights_and_no_json():
    info = {"rights": [{"text": "© JIRCAS", "uri": "https://www.jircas.go.jp/ja/about/guide"}],
            "files": [{"filename": "a.pdf", "licensetype": "license_note"}]}
    assert w.license_for_file(info, "a.pdf")["license_status"] == "not_cc"
    assert w.license_for_file({"rights": [], "files": []}, "a.pdf")["license_status"] == "no_rights"
    assert w.license_for_file(None, "a.pdf") == {"license_status": "no_json"}


def test_license_item_level_uses_all_pdfs():
    info = {"rights": [], "files": [{"filename": "a.pdf", "licensetype": "license_0"},
                                    {"filename": "t.jpg", "licensetype": "license_3"}]}
    lic = w.license_for_file(info, None)
    assert lic["license_status"] == "cc" and lic["license"] == "CC BY 4.0", lic


def test_add_license_columns_matches_encoded_filename():
    rows = [{"status": "ok", "record_id": "1",
             "file_url": "https://h/record/1/files/IJHST_Volume%2013.pdf"},
            {"status": "no_capture", "record_id": "1"}]
    lic = {"timestamp": "20250812102524", "original": "https://h/records/1/export/json",
           "info": {"rights": [], "files": [{"filename": "IJHST_Volume 13.pdf", "licensetype": "license_0"}]}}
    out = w.add_license_columns(rows, lic)
    assert out[0]["license_status"] == "cc"
    assert out[0]["license_capture_date"] == "2025-08-12"
    assert out[0]["license_source_url"] == "https://web.archive.org/web/20250812102524id_/https://h/records/1/export/json"
    assert out[1]["license_status"] == "cc"  # アイテム単位（PDF 全体）で判定
    assert w.add_license_columns(rows, None)[0]["license_status"] == "no_json"
    assert w.add_license_columns(rows, {"latest": "1", "info": None})[0]["license_status"] == "no_json"


def test_group_export_json_filters_and_sorts():
    rows = [
        {"timestamp": "20240101000000", "original": "https://h/records/5/export/json", "statuscode": "200", "length": "100"},
        {"timestamp": "20250101000000", "original": "https://h/records/5/export/json", "statuscode": "200", "length": "100"},
        {"timestamp": "20260101000000", "original": "https://h/records/5/export/json", "statuscode": "200", "length": "-1"},
        {"timestamp": "20250101000000", "original": "https://h/records/5/export/bibtex", "statuscode": "200", "length": "1"},
        {"timestamp": "20250101000000", "original": "https://h/records/5.1/export/json", "statuscode": "200", "length": "1"},
        {"timestamp": "20250101000000", "original": "https://h/record/5/files/a.pdf", "statuscode": "200", "length": "1"},
    ]
    g = w.group_export_json(rows)
    assert list(g) == ["5"]
    assert [r["timestamp"] for r in g["5"]] == ["20250101000000", "20240101000000"]


def test_decode_body_gzip():
    import gzip
    body = json.dumps({"metadata": {}}).encode("utf-8")
    assert w.decode_body(gzip.compress(body), "gzip") == body.decode("utf-8")
    assert w.decode_body(gzip.compress(body), "") == body.decode("utf-8")  # ヘッダが無くても先頭で判定
    assert w.decode_body("日本語".encode("utf-8")) == "日本語"


class _UrlHttp:
    def __init__(self, responses):
        self.responses = responses  # url -> str | Exception
        self.calls = []

    def get(self, url, retries=5, timeout=60):
        self.calls.append(url)
        r = self.responses[url]
        if isinstance(r, Exception):
            raise r
        return r


def _http_error(code):
    import urllib.error
    return urllib.error.HTTPError("u", code, "x", {}, None)


def test_fetch_license_falls_back_to_older_capture():
    caps = [{"timestamp": "2", "original": "https://h/records/1/export/json"},
            {"timestamp": "1", "original": "https://h/records/1/export/json"}]
    http = _UrlHttp({w.archive_url("2", caps[0]["original"]): _http_error(404),
                      w.archive_url("1", caps[1]["original"]): json.dumps(EXPORT_JIRCAS)})
    obj = w.fetch_license(http, "h", "1", caps)
    assert obj["timestamp"] == "1" and obj["latest"] == "2"
    assert obj["info"]["rights"]


def test_fetch_license_transient_then_fallback_stays_retryable():
    orig = "https://h/records/1/export/json"
    caps = [{"timestamp": "2", "original": orig}, {"timestamp": "1", "original": orig}]
    http = _UrlHttp({w.archive_url("2", orig): TimeoutError(),
                     w.archive_url("1", orig): json.dumps(EXPORT_JIRCAS)})
    obj = w.fetch_license(http, "h", "1", caps)
    assert obj["timestamp"] == "1" and obj["latest"] == "1", obj  # 最新（2）は未処理のまま
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "export_json.jsonl"
        got, err = w.collect_licenses(http, "h", ["1"], {"1": caps}, p)
        assert err == 1 and got["1"]["timestamp"] == "1"  # 今回は古いスナップショットで判定する
        # 次回は最新を取り直す
        http2 = _UrlHttp({w.archive_url("2", orig): json.dumps(EXPORT_JIRCAS)})
        got, err = w.collect_licenses(http2, "h", ["1"], {"1": caps}, p)
        assert http2.calls == [w.archive_url("2", orig)] and err == 0
        assert got["1"]["timestamp"] == "2" and got["1"]["latest"] == "2"


def test_fetch_license_definitive_failure_and_transient():
    caps = [{"timestamp": "2", "original": "https://h/records/1/export/json"}]
    url = w.archive_url("2", caps[0]["original"])
    obj = w.fetch_license(_UrlHttp({url: "<html>not json</html>"}), "h", "1", caps)
    assert obj["info"] is None and obj["latest"] == "2"
    assert w.fetch_license(_UrlHttp({url: TimeoutError()}), "h", "1", caps) is None


def test_collect_licenses_uses_cache_and_refetches_newer_capture():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "export_json.jsonl"
        orig = "https://h/records/1/export/json"
        p.write_text(json.dumps({"host": "h", "rid": "1", "latest": "1", "timestamp": "1",
                                 "original": orig, "info": {"rights": [], "files": []}}) + "\n",
                     encoding="utf-8")
        http = _UrlHttp({})
        got, err = w.collect_licenses(http, "h", ["1", "2"], {"1": [{"timestamp": "1", "original": orig}]}, p)
        assert http.calls == [] and err == 0 and list(got) == ["1"]
        # 新しい capture が現れたら取り直す
        http = _UrlHttp({w.archive_url("3", orig): json.dumps(EXPORT_JIRCAS)})
        got, err = w.collect_licenses(http, "h", ["1"], {"1": [{"timestamp": "3", "original": orig}]}, p)
        assert len(http.calls) == 1 and got["1"]["timestamp"] == "3"
        # 再読み込みでも新しい方が使われる
        assert w.load_license_cache(p, "h")["1"]["timestamp"] == "3"
        assert w.load_license_cache(p, "other") == {}


def _opts(argv, environ=None, dotenv_text=None):
    """手元の .env や os.environ を読まないよう、一時ファイルと dict を渡して load_options を呼ぶ。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.env"
        p.write_text(dotenv_text or "", encoding="utf-8")
        return w.load_options([*argv, "--env-file", str(p)], environ or {})


def _exits(fn):
    """SystemExit の終了コードを返す（stdout/stderr は捨てる）。終了しなければ None。"""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            fn()
        except SystemExit as ex:
            return ex.code
    return None


def test_parser_defaults_bulk_and_license_on():
    a = _opts(["--host", "h"])
    assert a.bulk is True and a.license is True
    assert a.out == "wayback_fulltext.csv" and a.cache_dir == ".wb_cache"
    assert a.interval == 1.0 and a.bulk_page_size == w.BULK_PAGE_SIZE
    assert a.query is None and a.contact == ""  # query の None は main で --host に補う
    a = _opts(["--host", "h", "--no-license"])
    assert a.bulk is True and a.license is False
    a = _opts(["--host", "h", "--no-bulk"])
    assert a.bulk is False


def test_parse_dotenv():
    text = ("# コメント\n\n"
            "  WB_OUT = out.csv  \n"
            'WB_QUERY="x y"\n'
            "WB_CONTACT='a=b#c'\n"
            "export WB_INTERVAL=2.5\n"
            "NOEQUAL\n"
            "=novalue\n")
    got = w.parse_dotenv(text)
    assert got["WB_OUT"] == "out.csv"
    assert got["WB_QUERY"] == "x y"
    assert got["WB_CONTACT"] == "a=b#c"
    assert got["WB_INTERVAL"] == "2.5"
    assert "NOEQUAL" not in got and "" not in got


def test_dotenv_with_bom_is_read_as_utf8_sig():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / ".env"
        p.write_bytes("WB_HOST=jircas.repo.nii.ac.jp\nWB_QUERY=国際農林\n".encode("utf-8-sig"))
        a = w.load_options(["--env-file", str(p)], {})
    assert a.host == "jircas.repo.nii.ac.jp" and a.query == "国際農林"


def test_parse_bool():
    for s in ("true", "TRUE", "1", "yes", "On"):
        assert w.parse_bool(s) is True, s
    for s in ("false", "0", "No", "off"):
        assert w.parse_bool(s) is False, s
    try:
        w.parse_bool("maybe")
    except ValueError:
        pass
    else:
        raise AssertionError("maybe で ValueError にならない")


def test_options_precedence_cli_env_dotenv_default():
    dotenv = "WB_HOST=a\nWB_BULK=false\nWB_OUT=dotenv.csv\nWB_INTERVAL=3\nWB_CONTACT=d\n"
    a = _opts(["--host", "b", "--bulk"], {"WB_OUT": "env.csv"}, dotenv)
    assert a.host == "b"            # CLI > .env
    assert a.bulk is True           # CLI > .env
    assert a.out == "env.csv"       # 環境変数 > .env
    assert a.interval == 3.0        # .env > 既定値（型変換される）
    assert a.contact == "d"
    assert a.cache_dir == ".wb_cache"  # どこにも無ければ既定値
    a = _opts([], {}, "WB_HOST=a\nWB_LICENSE=off\nWB_BULK_PAGE_SIZE=100\n")
    assert a.host == "a" and a.license is False and a.bulk_page_size == 100


def test_empty_env_contact_overrides_dotenv():
    a = _opts(["--host", "h"], {"WB_CONTACT": ""}, "WB_CONTACT=d\n")
    assert a.contact == ""


def test_invalid_lower_setting_can_be_overridden_by_cli():
    # 採用しない値は変換しない（P2-1 の回帰テスト）
    a = _opts(["--host", "h", "--interval", "1"], {}, "WB_INTERVAL=abc\n")
    assert a.interval == 1.0
    a = _opts(["--host", "h", "--no-bulk"], {}, "WB_BULK=maybe\n")
    assert a.bulk is False
    a = _opts(["--host", "h"], {"WB_INTERVAL": "2"}, "WB_INTERVAL=abc\n")
    assert a.interval == 2.0


def test_invalid_adopted_setting_reports_key():
    import contextlib
    import io
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            _opts(["--host", "h"], {}, "WB_INTERVAL=abc\n")
        except SystemExit as ex:
            assert ex.code == 2
        else:
            raise AssertionError("不正な WB_INTERVAL で終了しない")
    assert "WB_INTERVAL" in err.getvalue() and ".env" in err.getvalue()


def test_help_works_even_if_dotenv_is_broken():
    # P2-2 の回帰テスト: 設定が壊れていてもヘルプは表示できる
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.env"
        p.write_bytes(b"WB_INTERVAL=abc\nWB_BULK=maybe\n\xff\xfe")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                w.main(["--help", "--env-file", str(p)])
            except SystemExit as ex:
                assert ex.code == 0
            else:
                raise AssertionError("--help で終了しない")
    assert "usage" in out.getvalue() and "WB_HOST" in out.getvalue()


def test_host_required_and_missing_env_file():
    assert _exits(lambda: _opts([])) == 2
    assert _exits(lambda: w.load_options(["--host", "h", "--env-file", "no/such/.env"], {})) == 2


def test_unknown_wb_key_warns():
    args = w.build_parser().parse_args(["--host", "h"])
    warns = w.resolve_options(args, {}, {"WB_HOTS": "x", "OTHER": "y", "WB_LIMIT": "5"})
    assert any("WB_HOTS" in m for m in warns)
    assert any("WB_LIMIT" in m for m in warns)  # --limit は対象外
    assert not any("OTHER" in m for m in warns)
    assert args.limit == 0


def test_no_license_leaves_license_columns_empty():
    # --no-license では add_license_columns を通さない。CSV にはライセンスの列があり、値は空
    rows = w.build_rows({"title": "t", "uri": "https://h/records/1"}, "1", [])
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "o.csv"
        w.write_csv(p, rows)
        got = list(csv.DictReader(p.open(encoding="utf-8-sig")))
    for k in ("license_status", "license", "rights", "rights_uri", "licensetype",
              "license_capture_date", "license_source_url"):
        assert k in got[0] and got[0][k] == "", k


# ---------------------------------------------------------------------------
# スナップショットの履歴の digest 値と --capture-before（Issue #5）
# ---------------------------------------------------------------------------

# 形式が正しい 32 文字の Base32（実測値。A は 2000258、B は 2000885 の digest）
DIG_A = "LIMIPSTYP2V6M7NO6TR4S42OIEP7QPKP"
DIG_B = "VVFNXLLVFCO7OEK53VKCEKDYGBPX27VQ"


def _dcap(ts, digest, name="a.pdf", rid="1", scheme="https", **kw):
    return {"timestamp": ts, "original": f"{scheme}://h/record/{rid}/files/{name}",
            "mimetype": "application/octet-stream", "statuscode": "200", "length": "100",
            "digest": digest, **kw}


def _one(rows, before=""):
    files = w.summarize_pdf_files(rows, before)
    assert len(files) == 1, files
    return files[0]


def test_parse_capture_before():
    assert w.parse_capture_before("2026-10-28") == "20261028000000"
    assert w.parse_capture_before("20261028") == "20261028000000"
    assert w.parse_capture_before(" 20261028123456 ") == "20261028123456"
    assert w.parse_capture_before("") == "" and w.parse_capture_before(None) == ""
    for bad in ("2026/10/28", "2026-1-2", "202610281234", "2026-02-30", "20261028250000", "x"):
        try:
            w.parse_capture_before(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} で ValueError にならない")
    assert w.describe_before("20261028000000") == (
        "2026-10-28 00:00:00 UTC（日本時間 2026-10-28 09:00:00）より前")


def test_normalize_digest():
    assert w.normalize_digest(DIG_A) == DIG_A
    assert w.normalize_digest(" " + DIG_A.lower() + " ") == DIG_A
    for prefix in ("sha1:", "SHA1:", "Sha1:"):
        assert w.normalize_digest(prefix + DIG_A) == DIG_A
    for bad in (None, "", "-", DIG_A[:31], DIG_A + "A", "0" * 32, "1" * 32, DIG_A[:31] + "8"):
        assert w.normalize_digest(bad) == "", bad


def test_integrity_status_priority():
    assert w.integrity_status([DIG_A, DIG_B, ""]) == "changed"  # 欠損があっても違いを示す
    assert w.integrity_status([DIG_A, ""]) == "unknown"          # 一致とはしない
    assert w.integrity_status([DIG_A, DIG_A, ""]) == "unknown"
    assert w.integrity_status([DIG_A, DIG_A]) == "consistent"
    assert w.integrity_status([DIG_A]) == "single"
    assert w.integrity_status([""]) == "unknown"


def test_digest_columns_consistent_and_changed():
    f = _one([_dcap("20250101000000", DIG_A), _dcap("20250601000000", "sha1:" + DIG_A)])
    assert f["integrity_status"] == "consistent" and f["digest_variants"] == 1
    assert f["capture_digest"] == DIG_A and f["digest_first_seen"] == "2025-01-01"
    assert f["later_digest_date"] == ""
    # 上限より後でだけ内容が変わる。比較は全期間、採用は上限より前
    rows = [_dcap("20250101000000", DIG_A), _dcap("20260201000000", DIG_A),
            _dcap("20261101000000", DIG_B), _dcap("20261201000000", DIG_B)]
    f = _one(rows, "20261028000000")
    assert f["adopted"]["timestamp"] == "20260201000000"
    assert f["integrity_status"] == "changed" and f["digest_variants"] == 2
    assert f["n_captures"] == 4  # 全期間の数
    assert f["capture_digest"] == DIG_A and f["digest_first_seen"] == "2025-01-01"
    assert f["later_digest_date"] == "2026-11-01"
    # 上限なしなら最新（B）を採用し、その後に別の digest は無い
    f = _one(rows)
    assert f["capture_digest"] == DIG_B and f["digest_first_seen"] == "2026-11-01"
    assert f["later_digest_date"] == ""


def test_digest_dates_when_digest_is_missing():
    # 採用版の digest が欠損し、後続に A と B がある → changed だが、日付の 2 列は空
    rows = [_dcap("20250101000000", "-"), _dcap("20261101000000", DIG_A),
            _dcap("20261201000000", DIG_B)]
    f = _one(rows, "20261028000000")
    assert f["integrity_status"] == "changed" and f["digest_variants"] == 2
    assert f["capture_digest"] == "" and f["digest_first_seen"] == "" and f["later_digest_date"] == ""
    # 採用版が A で、後続は欠損だけ → 欠損は「別の digest」と数えない
    rows = [_dcap("20250101000000", DIG_A), _dcap("20261101000000", "")]
    f = _one(rows, "20261028000000")
    assert f["integrity_status"] == "unknown" and f["digest_variants"] == 1
    assert f["capture_digest"] == DIG_A and f["later_digest_date"] == ""
    # digest の列が無い旧形式の行
    f = _one([{k: v for k, v in _dcap("20250101000000", DIG_A).items() if k != "digest"}])
    assert f["integrity_status"] == "unknown" and f["digest_variants"] == 0


def test_capture_before_boundary_is_exclusive_utc():
    rows = [_dcap("20261027235959", DIG_A), _dcap("20261028000000", DIG_A)]
    f = _one(rows, w.parse_capture_before("2026-10-28"))
    assert f["adopted"]["timestamp"] == "20261027235959"  # 指定日の 00:00:00 UTC は含まない
    assert f["integrity_status"] == "consistent" and f["n_captures"] == 2
    picked = w.pick_latest_pdfs(rows, "20261028000000")
    assert [p["timestamp"] for p in picked] == ["20261027235959"] and picked[0]["n_captures"] == 2
    assert w.pick_latest_pdfs(rows, "20261027235959") == []  # 上限より前に無いファイルは除く


def test_digest_comparison_ignores_invalid_length_and_revisit_and_scheme():
    rows = [
        _dcap("20250101000000", DIG_A, scheme="http"),
        _dcap("20250601000000", DIG_A, scheme="https"),           # http と https は同じファイル
        _dcap("20250930000000", DIG_B, length="-10490845558"),    # length が負: 比較に使わない
        {**_dcap("20251001000000", DIG_B), "mimetype": "warc/revisit", "statuscode": "-"},  # revisit
    ]
    f = _one(rows)
    assert f["n_captures"] == 2 and f["integrity_status"] == "consistent"
    assert f["adopted"]["timestamp"] == "20250601000000"


def test_after_cutoff_rows_and_item_files_captured():
    _, entries = w.parse_atom(ATOM_FIXTURE)
    before = "20261028000000"
    rows = w.build_rows(entries[0], "1", [
        _dcap("20250101000000", DIG_A, "a.pdf"), _dcap("20261101000000", DIG_A, "a.pdf"),
        _dcap("20261101000000", DIG_B, "b.pdf")], before)
    a, b = rows
    assert a["status"] == "ok" and b["status"] == "after_cutoff"
    assert a["item_files_captured"] == 1 and b["item_files_captured"] == 1
    assert b["file_url"].endswith("/b.pdf") and b["n_captures"] == 1
    assert b["digest_variants"] == 1 and b["integrity_status"] == "single"
    for k in ("capture_timestamp", "capture_date", "mimetype", "length", "capture_digest",
              "digest_first_seen", "later_digest_date", "archive_url"):
        assert not b.get(k), k  # 採用版に依存する列は空
    assert a["archive_url"].startswith("https://web.archive.org/web/20250101000000id_/")
    assert a["capture_digest"] == DIG_A and a["integrity_status"] == "consistent"
    only_after = w.build_rows(entries[0], "2", [_dcap("20261101000000", DIG_B, "b.pdf", "2")], before)
    assert [r["status"] for r in only_after] == ["after_cutoff"]
    assert only_after[0]["item_files_captured"] == 0
    # アイテム単位: ok > after_cutoff
    by_status, pdf_files = w.summarize(rows + only_after + w.build_rows(entries[0], "3", []))
    assert by_status == {"ok": 1, "after_cutoff": 1, "no_capture": 1} and pdf_files == 1
    # 上限なしなら after_cutoff は現れない
    assert all(r["status"] == "ok" for r in w.build_rows(entries[0], "2",
                                                         [_dcap("20261101000000", DIG_B, "b.pdf", "2")]))


def test_changed_row_still_has_archive_url():
    # changed はバイト列の差異で、内容の変更とは限らない（途中で切れたスナップショット、gzip）。
    # 判断は利用者が行うので、changed でも PDF へのリンクは出す
    _, entries = w.parse_atom(ATOM_FIXTURE)
    rows = w.build_rows(entries[0], "1", [_dcap("20250101000000", DIG_A),
                                          _dcap("20250601000000", DIG_B)])
    assert rows[0]["status"] == "ok" and rows[0]["integrity_status"] == "changed"
    assert rows[0]["archive_url"] == w.archive_url("20250601000000", "https://h/record/1/files/a.pdf")


def test_summarize_integrity():
    rows = [{"status": "ok", "integrity_status": "consistent"},
            {"status": "ok", "integrity_status": "changed", "later_digest_date": "2026-11-01"},
            {"status": "ok", "integrity_status": "changed"},
            {"status": "after_cutoff", "integrity_status": "single"},
            {"status": "ok", "integrity_status": ""},
            {"status": "no_capture"}, {"status": "cdx_error", "integrity_status": "unknown"}]
    by, later = w.summarize_integrity(rows)
    assert by == {"consistent": 1, "changed": 2, "single": 1, "unknown": 1} and later == 1


def test_digest_columns_are_appended_at_end():
    cols = w.CSV_COLUMNS
    assert cols[-9:-4] == ["capture_digest", "digest_variants", "integrity_status",
                           "digest_first_seen", "later_digest_date"]
    assert cols.index("license_source_url") == len(cols) - 10  # 既存の列の位置は変わらない
    assert cols[-4:] == ["possibly_truncated", "larger_capture_date", "alt_capture_date",
                         "alt_archive_url"]  # Issue #7 の列は末尾
    assert "digest" in w.CDX_FIELDS and w.CDX_QUERY["filter"] == "statuscode:200"


def test_stale_cdx_cache_lines_are_dropped_and_refetched():
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d) / "cdx_bulk.jsonl"
        h = "jircas.repo.nii.ac.jp"
        old_rows = w.parse_cdx_page(BULK_PAGE2)[0]
        lines = [
            {"host": h, "kind": "record", "rows": old_rows, "resume": None},  # 旧形式（query なし）
            {"host": h, "kind": "record", "rows": old_rows, "resume": None,
             "query": {"fl": w.CDX_FIELDS, "filter": "statuscode:301"}},    # 条件が違う
        ]
        cache.write_text("".join(json.dumps(o) + "\n" for o in lines), encoding="utf-8")
        assert w.load_bulk_cache(cache, h) == {}  # どちらも使わない
        http = _FakeHttp([BULK_PAGE1, BULK_PAGE2])
        rows = w.fetch_bulk(http, h, cache, page_size=2)
        assert len(http.urls) == 2 and len(rows) == 3  # 取り直す
        assert "digest" in http.urls[0]  # fl に digest を含める
        kept = [json.loads(x) for x in cache.read_text(encoding="utf-8").splitlines()]
        assert len(kept) == 2 and all(o["query"] == w.CDX_QUERY for o in kept)  # 古い行は消える
        http2 = _FakeHttp([])
        assert len(w.fetch_bulk(http2, h, cache)) == 3 and not http2.urls
        assert w.drop_stale_cache_lines(cache) == 0
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cdx.jsonl"
        p.write_text(json.dumps({"host": "h", "rid": "1", "rows": []}) + "\n"
                     + json.dumps({"host": "h", "rid": "2", "query": w.CDX_QUERY, "rows": []}) + "\n",
                     encoding="utf-8")
        assert list(w.load_cdx_cache(p, "h")) == ["2"]
        assert w.drop_stale_cache_lines(p) == 1 and list(w.load_cdx_cache(p, "h")) == ["2"]


EXPORT_ORIG = "https://h/records/1/export/json"
_CC_INFO = {"rights": [{"text": "", "uri": "https://creativecommons.org/licenses/by/4.0/"}],
            "files": []}


def test_collect_licenses_does_not_reuse_cache_after_cutoff():
    # 計画レビュー P1 の回帰テスト: 上限なしで取得済み → 上限を付けて再実行 → 通信失敗
    before = "20261028000000"
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "export_json.jsonl"
        p.write_text(json.dumps({"host": "h", "rid": "1", "latest": "20261101000000",
                                 "timestamp": "20261101000000", "original": EXPORT_ORIG,
                                 "info": _CC_INFO}) + "\n", encoding="utf-8")
        all_caps = {"1": [{"timestamp": "20261101000000", "original": EXPORT_ORIG},
                          {"timestamp": "20261001000000", "original": EXPORT_ORIG}]}
        caps = {"1": all_caps["1"][1:]}
        http = _UrlHttp({w.archive_url("20261001000000", EXPORT_ORIG): TimeoutError()})
        got, err = w.collect_licenses(http, "h", ["1"], caps, p, before)
        assert err == 1 and got == {}
        rows = w.add_license_columns([{"file_url": ""}], got.get("1"))
        assert rows[0]["license_status"] == "no_json"
        assert w.license_missing_reasons(["1"], all_caps, caps, got) == {"fetch_failed": 1}
        # 上限なしなら、従来どおり古いキャッシュを使う
        got, err = w.collect_licenses(http, "h", ["1"], all_caps, p)
        assert got["1"]["timestamp"] == "20261101000000"
        # 取り直しに成功すれば、上限より前の export JSON で判定する
        http = _UrlHttp({w.archive_url("20261001000000", EXPORT_ORIG): json.dumps(EXPORT_JIRCAS)})
        got, err = w.collect_licenses(http, "h", ["1"], caps, p, before)
        assert err == 0 and got["1"]["timestamp"] == "20261001000000"


def test_no_export_json_before_cutoff():
    # 上限より前に export JSON が 1 件も無い → 候補の辞書に入らず、no_json
    rows = [{"timestamp": "20261101000000", "original": EXPORT_ORIG, "statuscode": "200",
             "length": "100"}]
    all_caps = w.group_export_json(rows)
    caps = w.group_export_json(rows, "20261028000000")
    assert list(all_caps) == ["1"] and caps == {}
    with tempfile.TemporaryDirectory() as d:
        http = _UrlHttp({})
        got, err = w.collect_licenses(http, "h", ["1", "2"], caps, Path(d) / "e.jsonl",
                                      "20261028000000")
        assert got == {} and err == 0 and http.calls == []
    reasons = w.license_missing_reasons(["1", "2"], all_caps, caps, got)
    assert reasons == {"none_before": 1, "no_export": 1}
    # 試したがすべて読めなかったもの（info が null で、最新の候補まで処理済み）
    lic = {"1": {"latest": "20261101000000", "info": None}}
    assert w.license_missing_reasons(["1"], all_caps, all_caps, lic) == {"unreadable": 1}
    # 判定できたものは数えない
    lic = {"1": {"latest": "20261101000000", "timestamp": "20261101000000", "info": _CC_INFO}}
    assert w.license_missing_reasons(["1"], all_caps, all_caps, lic) == {}


def test_capture_before_option_precedence():
    a = _opts(["--host", "h"])
    assert a.capture_before == ""  # 既定は制限なし
    a = _opts(["--host", "h"], {}, "WB_CAPTURE_BEFORE=2026-10-28\n")
    assert a.capture_before == "20261028000000"
    a = _opts(["--host", "h"], {"WB_CAPTURE_BEFORE": "20261001"}, "WB_CAPTURE_BEFORE=2026-10-28\n")
    assert a.capture_before == "20261001000000"  # 環境変数 > .env
    a = _opts(["--host", "h", "--capture-before", "2026-09-01"], {"WB_CAPTURE_BEFORE": "20261001"})
    assert a.capture_before == "20260901000000"  # CLI > 環境変数
    a = _opts(["--host", "h", "--capture-before", ""], {}, "WB_CAPTURE_BEFORE=2026-10-28\n")
    assert a.capture_before == ""  # CLI の空文字で .env を打ち消す
    a = _opts(["--host", "h"], {"WB_CAPTURE_BEFORE": ""}, "WB_CAPTURE_BEFORE=2026-10-28\n")
    assert a.capture_before == ""  # 環境変数の空文字でも打ち消す
    a = _opts(["--host", "h", "--capture-before", "2026-10-28"], {}, "WB_CAPTURE_BEFORE=bad\n")
    assert a.capture_before == "20261028000000"  # 下位の不正値は CLI で上書きできる
    assert _exits(lambda: _opts(["--host", "h", "--capture-before", "2026/10/28"])) == 2
    assert _exits(lambda: _opts(["--host", "h"], {}, "WB_CAPTURE_BEFORE=bad\n")) == 2

# ---- 途中で切れている可能性（Issue #7） ----

DIG_C = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA2"
TRUNC_EMPTY = {"possibly_truncated": "", "larger_capture_date": "", "alt_capture_date": "",
               "alt_archive_url": ""}


def _trunc(f):
    return {k: f[k] for k in w.TRUNCATION_COLUMNS}


def test_length_int():
    assert w.length_int({"length": "100"}) == 100 and w.length_int({"length": " 7 "}) == 7
    for bad in ({}, {"length": ""}, {"length": "-"}, {"length": None}, {"length": "1.5"}):
        assert w.length_int(bad) is None, bad


def test_truncation_adopted_smaller_than_other_digest():
    # 採用版（新しい A）が、古い B より小さい → 警告。上限なしなら代替版 = 全期間の最大
    f = _one([_dcap("20250101000000", DIG_B, length="200"), _dcap("20260101000000", DIG_A, length="50")])
    assert f["adopted"]["timestamp"] == "20260101000000"  # 採用は変えない
    assert _trunc(f) == {
        "possibly_truncated": "1", "larger_capture_date": "2025-01-01",
        "alt_capture_date": "2025-01-01",
        "alt_archive_url": w.archive_url("20250101000000", "https://h/record/1/files/a.pdf")}


def test_truncation_mixed_cutoff_keeps_alt_before_cutoff():
    # 計画レビューの例: 上限内の B=100、採用版 A=50、上限後の B=200
    rows = [_dcap("20250101000000", DIG_B, length="100"), _dcap("20260101000000", DIG_A, length="50"),
            _dcap("20261101000000", DIG_B, length="200")]
    f = _one(rows, "20261028000000")
    assert f["adopted"]["timestamp"] == "20260101000000"
    assert f["possibly_truncated"] == "1" and f["larger_capture_date"] == "2026-11-01"
    assert f["alt_capture_date"] == "2025-01-01"
    assert f["alt_archive_url"].startswith("https://web.archive.org/web/20250101000000id_/")


def test_truncation_larger_only_after_cutoff():
    rows = [_dcap("20260101000000", DIG_A, length="50"), _dcap("20261101000000", DIG_B, length="200")]
    f = _one(rows, "20261028000000")
    assert _trunc(f) == {**TRUNC_EMPTY, "possibly_truncated": "1", "larger_capture_date": "2026-11-01"}
    # 上限ちょうどの版は代替にしない（未満だけ）
    rows = [_dcap("20260101000000", DIG_A, length="50"), _dcap("20261028000000", DIG_B, length="200")]
    f = _one(rows, "20261028000000")
    assert f["possibly_truncated"] == "1" and f["alt_archive_url"] == ""
    assert f["larger_capture_date"] == "2026-10-28"


def test_truncation_not_flagged():
    # 採用版の方が大きい（正規の差し替えで大きくなった場合など）
    assert _trunc(_one([_dcap("20250101000000", DIG_B, length="50"),
                        _dcap("20260101000000", DIG_A, length="200")])) == TRUNC_EMPTY
    # digest が 1 種類なら、長さが違っても警告しない
    assert _trunc(_one([_dcap("20250101000000", DIG_A, length="200"),
                        _dcap("20260101000000", DIG_A, length="50")])) == TRUNC_EMPTY
    # 採用版の digest が欠損
    assert _trunc(_one([_dcap("20250101000000", DIG_B, length="200"),
                        _dcap("20260101000000", "-", length="50")])) == TRUNC_EMPTY
    # 採用版の length が空（has_valid_length は有効とみなす）
    assert _trunc(_one([_dcap("20250101000000", DIG_B, length="200"),
                        _dcap("20260101000000", DIG_A, length="")])) == TRUNC_EMPTY
    # 比較相手の length が空、または digest が欠損 → 比べない
    assert _trunc(_one([_dcap("20250101000000", DIG_B, length=""),
                        _dcap("20250201000000", "", length="900"),
                        _dcap("20260101000000", DIG_A, length="50")])) == TRUNC_EMPTY
    # 採用版が無い（上限より後にしか無い）
    assert _trunc(_one([_dcap("20261101000000", DIG_A, length="50"),
                        _dcap("20261201000000", DIG_B, length="200")], "20261028000000")) == TRUNC_EMPTY


def test_truncation_picks_largest_then_newest():
    # 同じ長さが 2 件 → 新しい方
    f = _one([_dcap("20250101000000", DIG_B, length="200"), _dcap("20250301000000", DIG_B, length="200"),
              _dcap("20260101000000", DIG_A, length="50")])
    assert f["alt_capture_date"] == "2025-03-01" and f["larger_capture_date"] == "2025-03-01"
    # digest が 3 種類 → 最大の length
    f = _one([_dcap("20250101000000", DIG_C, length="900"), _dcap("20250301000000", DIG_B, length="300"),
              _dcap("20260101000000", DIG_A, length="50")])
    assert f["integrity_status"] == "changed" and f["digest_variants"] == 3
    assert f["alt_capture_date"] == "2025-01-01"
    # 採用版と同じ digest の大きい版は、比較対象にしない
    f = _one([_dcap("20250101000000", DIG_A, length="900"), _dcap("20250301000000", DIG_B, length="60"),
              _dcap("20260101000000", DIG_A, length="50")])
    assert f["alt_capture_date"] == "2025-03-01"


def test_truncation_columns_in_build_rows():
    _, entries = w.parse_atom(ATOM_FIXTURE)
    before = "20261028000000"
    rows = w.build_rows(entries[0], "1", [
        _dcap("20250101000000", DIG_B, "a.pdf", length="100"),
        _dcap("20260101000000", DIG_A, "a.pdf", length="50"),
        _dcap("20261101000000", DIG_A, "b.pdf", length="50"),
        _dcap("20261201000000", DIG_B, "b.pdf", length="900")], before)
    a, b = rows
    assert a["status"] == "ok" and a["possibly_truncated"] == "1"
    assert a["alt_archive_url"] == w.archive_url("20250101000000", "https://h/record/1/files/a.pdf")
    assert a["archive_url"].startswith("https://web.archive.org/web/20260101000000id_/")  # 採用は不変
    assert b["status"] == "after_cutoff"
    assert all(not b.get(k) for k in w.TRUNCATION_COLUMNS)


def test_summarize_truncation():
    rows = [{"status": "ok", "possibly_truncated": "1", "alt_archive_url": "https://x"},
            {"status": "ok", "possibly_truncated": "1", "alt_archive_url": ""},
            {"status": "ok", "possibly_truncated": ""},
            {"status": "after_cutoff", "possibly_truncated": "1", "alt_archive_url": "https://x"},
            {"status": "no_capture"}]
    assert w.summarize_truncation(rows) == (2, 1)


def test_make_public_csv():
    import make_public_csv as m
    rows = [{"license_status": "cc", "archive_url": "u1", "alt_archive_url": "a1"},
            {"license_status": "no_rights", "archive_url": "u2", "alt_archive_url": "a2"},
            {"license_status": "check", "archive_url": "u3", "alt_archive_url": ""},
            {"license_status": "", "archive_url": "u4"}]
    out = m.sanitize_rows(rows)
    assert out[0] == rows[0]
    assert out[1]["archive_url"] == "" and out[1]["alt_archive_url"] == ""
    assert out[2]["archive_url"] == "" and out[3]["archive_url"] == ""
    assert rows[1]["archive_url"] == "u2"  # 入力は変えない


def _run_all():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e!r}")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
