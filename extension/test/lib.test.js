// オフラインテスト。実行: node --test extension/test/
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const W = require("../lib.js");

const HOST = "jircas.repo.nii.ac.jp";

// 2026-11-08 に実際に返った CDX 応答（/records/2000879 の prefix 照会）
const LANDING_CDX = JSON.stringify([
  ["timestamp", "original", "statuscode", "length"],
  ["20250519211236", "https://jircas.repo.nii.ac.jp/records/2000879", "200", "24131"],
  ["20260831071651", "https://jircas.repo.nii.ac.jp/records/2000879", "200", "25606"],
  ["20260417123049", "https://jircas.repo.nii.ac.jp/records/2000879", "200", "24051"],
  ["20260901000000", "https://jircas.repo.nii.ac.jp/records/2000879/export/json", "200", "5220"],
  ["20260902000000", "https://jircas.repo.nii.ac.jp/records/2000879/file_details/gars-e_4.pdf", "200", "12586"],
  ["20260903000000", "https://jircas.repo.nii.ac.jp/records/20008790", "200", "100"],
]);

test("parseRecordUrl は JAIRO Cloud の詳細ページだけを受け付ける", () => {
  assert.deepEqual(W.parseRecordUrl("https://jircas.repo.nii.ac.jp/records/2000879"), { host: HOST, id: "2000879" });
  assert.deepEqual(W.parseRecordUrl("https://Jircas.repo.nii.ac.jp/records/2000879/"), { host: HOST, id: "2000879" });
  assert.equal(W.parseRecordUrl("https://jircas.repo.nii.ac.jp/records/2000879/export/json"), null);
  assert.equal(W.parseRecordUrl("https://repository.example.ac.jp/records/2005976"), null);
  assert.equal(W.parseRecordUrl("http://hdl.handle.net/2433/252817"), null);
});

test("parseFileUrl はファイル名を復号する", () => {
  assert.deepEqual(
    W.parseFileUrl("https://jircas.repo.nii.ac.jp/record/2001157/files/Plant_Direct-2026-10%283%29_e70160.pdf"),
    { host: HOST, id: "2001157", name: "Plant_Direct-2026-10(3)_e70160.pdf" });
  assert.equal(W.parseFileUrl("https://repository.kulib.kyoto-u.ac.jp/bitstreams/a94f/download"), null);
  assert.equal(W.parseFileUrl("https://jircas.repo.nii.ac.jp/records/2000879/file_details/x.pdf"), null);
});

test("isPdfName は拡張子で判定する", () => {
  assert.ok(W.isPdfName("a.PDF"));
  assert.ok(!W.isPdfName("gars-e_01_cover_mini.jpg"));
});

test("ccFromRights は URI・本文の CC を拾い、NC/ND も CC とする", () => {
  assert.deepEqual(W.ccFromRights(["https://creativecommons.org/licenses/by/4.0/deed.en"]), { status: "cc", name: "CC BY 4.0" });
  assert.deepEqual(W.ccFromRights(["... http://creativecommons.org/licenses/by-nc-nd/4.0/deed/en ..."]),
    { status: "cc", name: "CC BY-NC-ND 4.0" });
  assert.deepEqual(W.ccFromRights(["https://creativecommons.org/publicdomain/zero/1.0/"]), { status: "cc", name: "CC0 1.0" });
  // 同じ URI が本文とリンクの両方にあっても 1 つ
  assert.equal(W.ccFromRights(["x https://creativecommons.org/licenses/by/4.0/", "https://creativecommons.org/licenses/by/4.0/"]).status, "cc");
  assert.equal(W.ccFromRights(["© by Japan International Research Center for Agricultural Sciences"]).status, "not_cc");
  assert.equal(W.ccFromRights([]).status, "not_cc");
  assert.equal(W.ccFromRights(["https://creativecommons.org/licenses/by/4.0/", "https://creativecommons.org/licenses/by-nc/4.0/"]).status, "conflict");
  // 1 つの権利情報の本文に、異なる CC の URL が並んでいる場合も conflict（PR #2 の Codex レビューで指摘）
  assert.deepEqual(
    W.ccFromRights(["See https://creativecommons.org/licenses/by/4.0/ or https://creativecommons.org/licenses/by-nc/4.0/"]),
    { status: "conflict", name: "CC BY 4.0 / CC BY-NC 4.0" });
  // 同じライセンスの表記揺れ（http/https、deed）が並ぶだけなら cc
  assert.deepEqual(
    W.ccFromRights(["http://creativecommons.org/licenses/by/4.0/ https://creativecommons.org/licenses/by/4.0/deed.ja"]),
    { status: "cc", name: "CC BY 4.0" });
});

test("parseCdxJson は空応答を [] にする", () => {
  assert.deepEqual(W.parseCdxJson(""), []);
  assert.deepEqual(W.parseCdxJson("[]"), []);
  assert.equal(W.parseCdxJson(LANDING_CDX).length, 6);
});

test("latestLandingCapture はパスが一致する最新の capture を選ぶ", () => {
  const r = W.latestLandingCapture(W.parseCdxJson(LANDING_CDX), HOST, "2000879");
  assert.deepEqual(r, { status: "ok", timestamp: "20260831071651", original: "https://jircas.repo.nii.ac.jp/records/2000879" });
  assert.deepEqual(W.latestLandingCapture([], HOST, "2000879"), { status: "none" });
});

test("latestFileCaptures は length 不正を除き、ファイルごとに最新を選ぶ", () => {
  const rows = [
    { timestamp: "20250722192940", original: "https://jircas.repo.nii.ac.jp/record/2000885/files/gars-5_ja.pdf", statuscode: "200", length: "3427839" },
    { timestamp: "20260101000000", original: "http://jircas.repo.nii.ac.jp/record/2000885/files/gars-5_ja.pdf", statuscode: "200", length: "3427800" },
    { timestamp: "20260201000000", original: "https://jircas.repo.nii.ac.jp/record/2000885/files/gars-5_ja.pdf", statuscode: "200", length: "-1" },
    { timestamp: "20250927000000", original: "https://jircas.repo.nii.ac.jp/record/2000885/files/broken.pdf", statuscode: "200", length: "-5" },
    { timestamp: "20250927000000", original: "https://jircas.repo.nii.ac.jp/record/2000885/files/a%20b.pdf", statuscode: "200", length: "10" },
    { timestamp: "20250927000000", original: "https://jircas.repo.nii.ac.jp/record/2000885/files/cover.jpg", statuscode: "200", length: "10" },
    { timestamp: "20250927000000", original: "https://jircas.repo.nii.ac.jp/record/20008851/files/other.pdf", statuscode: "200", length: "10" },
  ];
  const r = W.latestFileCaptures(rows, HOST, "2000885");
  assert.deepEqual(Object.keys(r).sort(), ["a b.pdf", "broken.pdf", "gars-5_ja.pdf"]);
  assert.deepEqual(r["gars-5_ja.pdf"], {
    status: "ok", timestamp: "20260101000000", original: "http://jircas.repo.nii.ac.jp/record/2000885/files/gars-5_ja.pdf", n: 2,
  });
  assert.deepEqual(r["broken.pdf"], { status: "invalid" });
});

test("latestFileCaptures は不正な capture の後に有効なものがあれば ok にする", () => {
  const rows = [
    { timestamp: "20260201000000", original: "https://jircas.repo.nii.ac.jp/record/1/files/x.pdf", statuscode: "200", length: "-1" },
    { timestamp: "20250101000000", original: "https://jircas.repo.nii.ac.jp/record/1/files/x.pdf", statuscode: "200", length: "9" },
  ];
  assert.equal(W.latestFileCaptures(rows, HOST, "1")["x.pdf"].timestamp, "20250101000000");
});

test("cdxQueryUrl と archiveUrl", () => {
  const q = new URL(W.cdxQueryUrl("files", HOST, "2000885"));
  assert.equal(q.origin + q.pathname, "https://web.archive.org/cdx/search/cdx");
  assert.equal(q.searchParams.get("url"), "jircas.repo.nii.ac.jp/record/2000885/files/");
  assert.equal(q.searchParams.get("matchType"), "prefix");
  assert.equal(new URL(W.cdxQueryUrl("landing", HOST, "2000879")).searchParams.get("url"), "jircas.repo.nii.ac.jp/records/2000879");
  const f = "https://jircas.repo.nii.ac.jp/record/2000885/files/gars-5_ja.pdf";
  assert.equal(W.archiveUrl(f, "20250722192940", true), "https://web.archive.org/web/20250722192940id_/" + f);
  assert.equal(W.archiveUrl(f, null, true), "https://web.archive.org/web/20991231id_/" + f);
  assert.equal(W.archiveUrl(f), "https://web.archive.org/web/20991231/" + f);
});

test("fileBlockReason は公開日前と制限付きのファイルを除く", () => {
  const today = "2026-11-08";
  assert.equal(W.fileBlockReason("open access", "2026-03-25", today), null);
  assert.equal(W.fileBlockReason("open access", null, today), null);
  assert.equal(W.fileBlockReason("embargoed access", "2027-05-13", today), "embargo");
  assert.equal(W.fileBlockReason("embargoed access", "2026-01-01", today), null);
  assert.equal(W.fileBlockReason("embargoed access", null, today), "embargo");
  assert.equal(W.fileBlockReason("open access", "2027-01-01", today), "embargo");
  assert.equal(W.fileBlockReason("restricted access", null, today), "restricted");
  assert.equal(W.fileBlockReason("metadata only access", null, today), "restricted");
});

test("captureDate・findDate・todayString", () => {
  assert.equal(W.captureDate("20250722192940"), "2025-07-22");
  assert.equal(W.findDate("Available 2026-03-25"), "2026-03-25");
  assert.equal(W.findDate("none"), null);
  assert.equal(W.todayString(new Date(2026, 0, 9)), "2026-01-09");
});

test("独自ドメインの JAIRO Cloud は一覧にあるものだけを対象にする", () => {
  // IRDB で確認した実例（2026-11-08）
  assert.deepEqual(W.parseRecordUrl("https://repository.naro.go.jp/records/2001242"), { host: "repository.naro.go.jp", id: "2001242" });
  assert.deepEqual(W.parseFileUrl("https://repository.lib.tottori-u.ac.jp/record/2002494/files/Glycoconjugate%20J43(1)_5.pdf"),
    { host: "repository.lib.tottori-u.ac.jp", id: "2002494", name: "Glycoconjugate J43(1)_5.pdf" });
  assert.deepEqual(W.parseFileUrl("https://Repository.DL.itc.u-tokyo.ac.jp/record/2015394/files/vol77.pdf"),
    { host: "repository.dl.itc.u-tokyo.ac.jp", id: "2015394", name: "vol77.pdf" });
  // 一覧にないホストは、同じ形の URL でも対象外
  assert.equal(W.parseRecordUrl("https://other-weko3.example.jp/records/1"), null);
  assert.equal(W.parseFileUrl("https://other-weko3.example.jp/record/1/files/a.pdf"), null);
  // 一覧のホストを含むだけの別ホストも対象外
  assert.equal(W.isJairoHost("evil.repository.naro.go.jp"), false);
  assert.equal(W.isJairoHost("repository.naro.go.jp.example.com"), false);
  assert.equal(W.isJairoHost("repo.nii.ac.jp"), false);
  assert.equal(W.isJairoHost("a.b.repo.nii.ac.jp"), false);
  assert.ok(W.isJairoHost("jircas.repo.nii.ac.jp"));
  assert.ok(W.isJairoHost("repo-tkfd.jp"));
  // 一覧は小文字で、重複が無い
  assert.equal(new Set(W.CUSTOM_HOSTS).size, W.CUSTOM_HOSTS.length);
  for (const h of W.CUSTOM_HOSTS) assert.equal(h, h.toLowerCase());
});
