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
        p.write_text(json.dumps({"host": "h", "rid": "1", "rows": []}) + "\n"
                     + '{"host": "h", "rid": "2", "ro', encoding="utf-8")
        cache = w.load_cdx_cache(p, "h")
        assert list(cache) == ["1"]


def test_cdx_cache_is_separated_by_host():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cdx.jsonl"
        lines = [{"host": "a.example", "rid": "1", "rows": [_cap("20250101000000", "a.pdf")]},
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
        p.write_text(json.dumps({"host": "h", "rid": "1", "rows": []}) + "\n"
                     + '{"host": "h", "rid": "2", "ro', encoding="utf-8")
        w.repair_jsonl_tail(p)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"host": "h", "rid": "3", "rows": []}) + "\n")
        assert list(w.load_cdx_cache(p, "h")) == ["1", "3"]


def test_resume_after_cut_in_middle_of_multibyte_char():
    # 日本語ファイル名の UTF-8 の途中で書き込みが途切れても、修復→読み込みで復旧できる
    h = "h"
    good = json.dumps({"host": h, "rid": "1", "rows": []}, ensure_ascii=False) + "\n"
    broken = json.dumps({"host": h, "rid": "2", "rows": [{"original": "x/日本語.pdf"}]},
                        ensure_ascii=False).encode("utf-8")
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
