/*
 * App 2 の共通ロジック（DOM に依存しない純粋関数）。
 * content script・Service Worker・Node のテスト（node --test）の 3 か所から読む。
 * 判定の考え方は wb_fulltext_csv.py（App 1）と揃える。
 */
(function (root) {
  "use strict";

  const WAYBACK = "https://web.archive.org";
  const CDX_ENDPOINT = WAYBACK + "/cdx/search/cdx";
  // 遠い未来の日付を指定すると、Wayback は最新の capture へ 302 で転送する（2026-11-08 に確認）。
  // 保存が無い URL は 404 になる。CDX の結果を待つ間の仮リンクに使う。
  const LATEST = "20991231";

  /*
   * 対象は JAIRO Cloud だけ。*.repo.nii.ac.jp に加えて、独自ドメインで運用しているものを一覧で持つ。
   * 新しく判明したら、この一覧にホスト名（小文字）を足す。
   * repository.naro.go.jp、repository.lib.tottori-u.ac.jp、repository.dl.itc.u-tokyo.ac.jp は、
   * IRDB の URL が /records/{id} と /record/{id}/files/… の形であることを確認済み（2026-11-08）。
   */
  const CUSTOM_HOSTS = [
    "repository.nii.ac.jp",
    "d-repo.ier.hit-u.ac.jp",
    "repository.lib.tottori-u.ac.jp",
    "ismrepo.ism.ac.jp",
    "repository.ninjal.ac.jp",
    "ir.soken.ac.jp",
    "repository.dl.itc.u-tokyo.ac.jp",
    "teapot.lib.ocha.ac.jp",
    "kutarr.kochi-tech.ac.jp",
    "ir.jikei.ac.jp",
    "ir.kagoshima-u.ac.jp",
    "amcor.asahikawa-med.ac.jp",
    "repository.ffpri.go.jp",
    "repository.jircas.go.jp",
    "repository.naro.go.jp",
    "ir.ide.go.jp",
    "repo.qst.go.jp",
    "repo-tkfd.jp",
  ];
  const CUSTOM_HOST_SET = new Set(CUSTOM_HOSTS);
  const JAIRO_SUBDOMAIN_RE = /^[a-z0-9-]+\.repo\.nii\.ac\.jp$/;

  /** JAIRO Cloud のホストか（*.repo.nii.ac.jp または一覧にある独自ドメイン）。 */
  function isJairoHost(host) {
    const h = String(host || "").toLowerCase();
    return JAIRO_SUBDOMAIN_RE.test(h) || CUSTOM_HOST_SET.has(h);
  }

  const RECORD_RE = /^https?:\/\/([a-z0-9.-]+)\/records\/(\d+)\/?(?:[?#].*)?$/i;
  const FILE_RE = /^https?:\/\/([a-z0-9.-]+)\/record\/(\d+)\/files\/([^?#]+)/i;

  function safeDecode(s) {
    try {
      return decodeURIComponent(s);
    } catch (e) {
      return s;
    }
  }

  /** JAIRO Cloud の詳細ページ（ランディングページ）の URL なら {host, id} を返す。 */
  function parseRecordUrl(href) {
    const m = RECORD_RE.exec(String(href || "").trim());
    if (!m || !isJairoHost(m[1])) return null;
    return { host: m[1].toLowerCase(), id: m[2] };
  }

  /** JAIRO Cloud の本文ファイルの URL なら {host, id, name}（name は復号したファイル名）を返す。 */
  function parseFileUrl(href) {
    const m = FILE_RE.exec(String(href || "").trim());
    if (!m || !isJairoHost(m[1])) return null;
    return { host: m[1].toLowerCase(), id: m[2], name: safeDecode(m[3]) };
  }

  /** 拡張子で PDF を判定する（CDX の mimetype は application/octet-stream のことがあるため使わない）。 */
  function isPdfName(name) {
    return /\.pdf$/i.test(String(name || ""));
  }

  const CC_URL_RE = /creativecommons\.org\/(?:licenses\/([a-z]+(?:-[a-z]+)*)|publicdomain\/(zero))\/(\d+(?:\.\d+)?)/i;

  /** 文字列中の CC の URL から `CC BY-NC-ND 4.0` / `CC0 1.0` のような名前を返す（App 1 の cc_license_name と同じ）。 */
  function ccLicenseName(s) {
    const m = CC_URL_RE.exec(String(s || ""));
    return m ? ccNameFromMatch(m) : null;
  }

  function ccNameFromMatch(m) {
    if (m[2]) return "CC0 " + m[3];
    return "CC " + m[1].toUpperCase() + " " + m[3];
  }

  /** 文字列中のすべての CC の URL から、ライセンス名の一覧を返す。 */
  function ccLicenseNames(s) {
    const re = new RegExp(CC_URL_RE.source, "gi");
    const out = [];
    for (const m of String(s || "").matchAll(re)) out.push(ccNameFromMatch(m));
    return out;
  }

  /**
   * 権利情報の文字列（本文とリンク先）から CC を判定する。
   * NC/ND 付きも CC と同じ扱い。種類が食い違うときは conflict（要確認。リンクは出さない）。
   * 1 つの文字列に複数の CC の URL が書かれていることもあるので、すべての URL を見る。
   */
  function ccFromRights(texts) {
    const names = [];
    for (const t of texts || []) {
      for (const n of ccLicenseNames(t)) {
        if (names.indexOf(n) < 0) names.push(n);
      }
    }
    if (names.length === 0) return { status: "not_cc", name: null };
    if (names.length > 1) return { status: "conflict", name: names.join(" / ") };
    return { status: "cc", name: names[0] };
  }

  /** CDX の JSON（先頭行が列名）を行オブジェクトの配列にする。空応答は []。 */
  function parseCdxJson(text) {
    const t = String(text || "").trim();
    if (!t) return [];
    const data = JSON.parse(t);
    if (!Array.isArray(data) || data.length < 2) return [];
    const head = data[0];
    return data.slice(1).map((row) => {
      const o = {};
      head.forEach((k, i) => { o[k] = row[i]; });
      return o;
    });
  }

  /** length が正の整数か。status 200 なのに負の値の capture があり、実体は 404 ページだった。 */
  function hasValidLength(r) {
    const v = r.length;
    if (v === undefined || v === null || v === "") return true;
    const n = Number(v);
    return Number.isInteger(n) && n > 0;
  }

  function urlParts(original) {
    try {
      const u = new URL(original);
      return { host: u.hostname.toLowerCase(), path: u.pathname };
    } catch (e) {
      return null;
    }
  }

  /**
   * /record/{id}/files/ 配下の capture から、PDF ごとの最新の有効な capture を選ぶ。
   * 返り値は { 復号したファイル名: {status: "ok", timestamp, original, n} | {status: "invalid"} }。
   * invalid は capture はあるが有効なもの（status 200 かつ length が正）が無い場合。
   */
  function latestFileCaptures(rows, host, id) {
    const prefix = "/record/" + id + "/files/";
    const out = {};
    for (const r of rows || []) {
      const p = urlParts(r.original);
      if (!p || p.host !== host || p.path.indexOf(prefix) !== 0) continue;
      const name = safeDecode(p.path.slice(prefix.length));
      if (!isPdfName(name)) continue;
      const valid = String(r.statuscode) === "200" && hasValidLength(r);
      const cur = out[name];
      if (!valid) {
        if (!cur) out[name] = { status: "invalid" };
        continue;
      }
      if (!cur || cur.status !== "ok") {
        out[name] = { status: "ok", timestamp: r.timestamp, original: r.original, n: 1 };
      } else {
        cur.n += 1;
        if (r.timestamp > cur.timestamp) {
          cur.timestamp = r.timestamp;
          cur.original = r.original;
        }
      }
    }
    return out;
  }

  /**
   * /records/{id} の capture から最新の有効なものを選ぶ。
   * prefix 照会は export/json や file_details、桁の多い別 ID も返すので、パスが一致するものだけにする。
   */
  function latestLandingCapture(rows, host, id) {
    const want = "/records/" + id;
    let best = null;
    for (const r of rows || []) {
      const p = urlParts(r.original);
      if (!p || p.host !== host) continue;
      if (p.path !== want && p.path !== want + "/") continue;
      if (String(r.statuscode) !== "200" || !hasValidLength(r)) continue;
      if (!best || r.timestamp > best.timestamp) best = { timestamp: r.timestamp, original: r.original };
    }
    return best ? { status: "ok", timestamp: best.timestamp, original: best.original } : { status: "none" };
  }

  /** CDX の照会 URL。kind は "files"（/record/{id}/files/）か "landing"（/records/{id}）。 */
  function cdxQueryUrl(kind, host, id) {
    const target = kind === "files" ? host + "/record/" + id + "/files/" : host + "/records/" + id;
    const params = new URLSearchParams([
      ["url", target],
      ["matchType", "prefix"],
      ["filter", "statuscode:200"],
      ["fl", "timestamp,original,statuscode,length"],
      ["output", "json"],
    ]);
    return CDX_ENDPOINT + "?" + params.toString();
  }

  /** 原本（id_ 付き）または Wayback の閲覧画面の URL。timestamp を省くと最新への転送 URL。 */
  function archiveUrl(original, timestamp, raw) {
    return WAYBACK + "/web/" + (timestamp || LATEST) + (raw ? "id_" : "") + "/" + original;
  }

  /** "20250722192940" → "2025-07-22" */
  function captureDate(ts) {
    const m = /^(\d{4})(\d{2})(\d{2})/.exec(String(ts || ""));
    return m ? m[1] + "-" + m[2] + "-" + m[3] : "";
  }

  /** 文字列から最初の YYYY-MM-DD を取り出す。 */
  function findDate(s) {
    const m = /(\d{4}-\d{2}-\d{2})/.exec(String(s || ""));
    return m ? m[1] : null;
  }

  function todayString(d) {
    const x = d || new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return x.getFullYear() + "-" + pad(x.getMonth() + 1) + "-" + pad(x.getDate());
  }

  /**
   * そのファイルへのリンクを出してよいか（ライセンス以外の条件）。
   * 公開日（Available）が未来のもの、アクセス権が restricted / metadata only のもの、
   * embargoed で公開日が書かれていないものは出さない。返り値は null（可）か理由のコード。
   */
  function fileBlockReason(access, available, today) {
    const a = String(access || "").toLowerCase();
    if (a.indexOf("restricted") >= 0 || a.indexOf("metadata only") >= 0) return "restricted";
    if (available && available > today) return "embargo";
    if (a.indexOf("embargoed") >= 0 && !available) return "embargo";
    return null;
  }

  const api = {
    LATEST,
    parseRecordUrl,
    CUSTOM_HOSTS,
    isJairoHost,
    parseFileUrl,
    isPdfName,
    ccLicenseName,
    ccFromRights,
    parseCdxJson,
    hasValidLength,
    latestFileCaptures,
    latestLandingCapture,
    cdxQueryUrl,
    archiveUrl,
    captureDate,
    findDate,
    todayString,
    fileBlockReason,
  };

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.WBF = api;
  }
})(typeof self !== "undefined" ? self : globalThis);
