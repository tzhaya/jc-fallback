/*
 * IRDB の詳細ページに、Wayback Machine のアーカイブ版へのリンクを差し込む。
 * - ランディングページ（/records/{id}）のアーカイブ版へのリンクは、すべての資料に出す。
 * - 本文 PDF のアーカイブ版へのリンクは、権利情報が CC の資料にだけ出す（一般向け）。
 * - CDX でアーカイブ状況を確認し終えるまでは項目名をリンクにせず、点の連続で確認中であることを示す。
 *   確認できたらアーカイブ日を付けてリンクにし、スナップショットが無ければ「アーカイブされていません」と出す。
 *   照会に失敗したときだけ、最新のスナップショットへの転送 URL をリンクにする。
 * JAIRO Cloud（*.repo.nii.ac.jp と、lib.js の CUSTOM_HOSTS にある独自ドメイン）以外のリポジトリのページでは何もしない。
 */
(function () {
  "use strict";
  if (document.getElementById("wbfb-panel")) return;

  const EN = /^en/i.test(document.documentElement.lang || "");
  const T = EN ? {
    paren: (s) => " (" + s + ")",
    sep: ": ",
    heading: "Archived copies in the Wayback Machine",
    lead: "Links to copies saved in the Internet Archive's Wayback Machine. Use them when the JAIRO Cloud repository is unavailable. Some items have not been saved.",
    landing: "Landing page",
    fulltext: "Full text (PDF)",
    open: "Open the archived page",
    checking: "Checking the archive",
    saved: (d) => "saved on " + d,
    none: "Not archived",
    invalid: "The archived copy may be broken, so no link is shown",
    error: "Could not check the archive (the link may not open)",
    retry: "Check again",
    truncated: "This archived copy may be incomplete (cut off partway).",
    truncatedNote: "This is estimated from the archived data size. It may also appear when the file was replaced or archived in compressed form.",
    tryAlt: "If it does not open, try another archived copy: ",
    altLink: (d) => "copy saved on " + d,
    cc: (n) => "The rights statement specifies " + n + ", so links to the archived PDFs are shown.",
    notCc: "No Creative Commons license was found in the rights statement, so links to the full-text PDFs are not shown.",
    conflict: (n) => "The rights statement lists different licenses (" + n + "), so links to the full-text PDFs are not shown.",
    noPdf: "This item has no PDF files.",
    embargo: "Not yet available (embargo), so no link is shown",
    restricted: "Access is restricted, so no link is shown",
    note: [
      "These links point to copies as they were when saved by the Internet Archive, and may differ from the latest version. Use them in accordance with the license.",
      "This is an unofficial tool. It is not provided by the organization operating the source institutional repository, the National Institute of Informatics, JPCOAR, or the Internet Archive. We do not guarantee that the linked files are identical to the original data.",
    ],
  } : {
    paren: (s) => "（" + s + "）",
    sep: "：",
    heading: "Wayback Machine のアーカイブ版",
    lead: "Internet Archive の Wayback Machine にアーカイブされた版へのリンクです。JAIRO Cloud のリポジトリに接続できないときにご利用ください。アーカイブされていない資料もあります。",
    landing: "ランディングページ",
    fulltext: "本文（PDF）",
    open: "アーカイブ版を開く",
    checking: "アーカイブ状況を確認中",
    saved: (d) => "アーカイブ日：" + d,
    none: "アーカイブされていません",
    invalid: "アーカイブ版が壊れている可能性があるため、リンクを表示しません",
    error: "アーカイブ状況を確認できませんでした（リンク先を開けない場合があります）",
    retry: "再確認",
    truncated: "アーカイブ版が途中で切れている可能性があります。",
    truncatedNote: "Wayback Machine が記録したデータの大きさからの推定です。ファイルの差し替えや、圧縮されたままアーカイブされた場合にも表示されます。",
    tryAlt: "開けないときは、別のアーカイブ版を試してください：",
    altLink: (d) => d + " のアーカイブ版",
    cc: (n) => "権利情報に " + n + " が指定されているため、本文 PDF のアーカイブ版へのリンクを表示します。",
    notCc: "権利情報に CC ライセンスが見つからないため、本文 PDF へのリンクは表示しません。",
    conflict: (n) => "権利情報に異なるライセンス（" + n + "）が書かれているため、本文 PDF へのリンクは表示しません。",
    noPdf: "この資料には PDF ファイルがありません。",
    embargo: "公開日より前のため、リンクを表示しません",
    restricted: "アクセスが制限されているため、リンクを表示しません",
    note: [
      "これらのリンク先は、Internet Archive がアーカイブした時点のもので、最新版とは異なることがあります。ライセンスに従って利用してください。",
      "非公式のツールです。元のデータを提供している機関リポジトリの運営機関、国立情報学研究所、JPCOAR、Internet Archive が提供するものではありません。また、リンク先のファイルの内容が、元のデータと同じであることは保証しません。",
    ],
  };

  const LABELS = {
    file: ["ファイル", "File"],
    rights: ["権利情報", "Rights"],
    identifier: ["資源識別子", "Identifier"],
    access: ["アクセス権", "Accessrights"],
  };

  /** 表の見出し（th）で行を探し、その td を返す。 */
  function findCells(kind) {
    const out = [];
    for (const th of document.querySelectorAll("tr > th")) {
      if (LABELS[kind].indexOf(th.textContent.trim()) >= 0) {
        const td = th.parentElement.querySelector("td");
        if (td) out.push(td);
      }
    }
    return out;
  }

  /** ファイルの行の、リンクを含む項目から公開日（Available）を読む。 */
  function availableDate(link) {
    const box = link.closest("li.mb-2") || link.parentElement;
    if (!box) return null;
    for (const span of box.querySelectorAll("span")) {
      if (span.textContent.trim() === "Available") {
        return WBF.findDate(span.nextSibling ? span.nextSibling.textContent : "") ||
          WBF.findDate(span.parentElement.textContent);
      }
    }
    return null;
  }

  function extract() {
    const fileCells = findCells("file");
    const scope = fileCells.length ? fileCells : [document];
    const files = [];
    const seen = new Set();
    for (const cell of scope) {
      for (const a of cell.querySelectorAll("a[href]")) {
        const f = WBF.parseFileUrl(a.href);
        if (!f || seen.has(a.href)) continue;
        seen.add(a.href);
        files.push({ ...f, href: a.href, available: availableDate(a) });
      }
    }

    // 対象の資料（host と id）。本文ファイルの URL を優先し、無ければ資源識別子の URI を使う。
    let target = null;
    const ids = new Set(files.map((f) => f.host + "/" + f.id));
    if (ids.size === 1) {
      target = { host: files[0].host, id: files[0].id };
    } else {
      const cells = findCells("identifier");
      for (const a of (cells.length ? cells[0] : document).querySelectorAll("a[href]")) {
        const r = WBF.parseRecordUrl(a.href);
        if (r) { target = r; break; }
      }
    }
    if (!target) return null;

    const rightsTexts = [];
    for (const td of findCells("rights")) {
      rightsTexts.push(td.textContent);
      for (const a of td.querySelectorAll("a[href]")) rightsTexts.push(a.href);
    }
    const accessCell = findCells("access")[0];
    return {
      ...target,
      license: WBF.ccFromRights(rightsTexts),
      access: accessCell ? accessCell.textContent.trim() : "",
      files: files.filter((f) => f.host === target.host && f.id === target.id && WBF.isPdfName(f.name)),
    };
  }

  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v;
      else e.setAttribute(k, v);
    }
    for (const c of children) e.append(c);
    return e;
  }

  function link(href, text) {
    return el("a", { href, target: "_blank", rel: "noopener noreferrer" }, text);
  }

  function ask(kind, host, id) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type: "cdx", kind, host, id }, (res) => {
          if (chrome.runtime.lastError || !res) resolve({ ok: false });
          else resolve(res);
        });
      } catch (e) {
        resolve({ ok: false }); // 拡張機能を再読み込みした直後など
      }
    });
  }

  /*
   * 確認中の点を 1〜6 個まで順に増やす。CSS の content のアニメーションは環境によって動かず、
   * prefers-reduced-motion の設定でも止まるため、JS で書き換える。
   * 点は装飾なので、読み上げの対象から外す（aria-hidden）。
   */
  const DOTS_MAX = 6;
  let dotsTimer = null;

  function tickDots() {
    const spans = document.querySelectorAll("#wbfb-panel .wbfb-dots");
    if (!spans.length) {
      clearInterval(dotsTimer);
      dotsTimer = null;
      return;
    }
    for (const s of spans) s.textContent = ".".repeat((s.textContent.length % DOTS_MAX) + 1);
  }

  function checking() {
    if (!dotsTimer) dotsTimer = setInterval(tickDots, 300);
    return [T.sep + T.checking, el("span", { class: "wbfb-dots", "aria-hidden": "true" }, ".")];
  }

  /** 確認が終わるまでの、リンクにしない項目名（無効なリンクとして示す）。 */
  function pending(label) {
    return el("span", { class: "wbfb-pending", role: "link", "aria-disabled": "true" }, label);
  }

  /**
   * 1 項目分の表示。確認中は項目名をリンクにせず、CDX の結果が届いてからリンクにする。
   * 照会に失敗したときは、最新のスナップショットへの転送 URL（provisional）をリンクにする。
   * apply(result) は {href, timestamp, state}（state: ok / none / invalid）を返す。
   * 本文の PDF では、ok のときに truncated（途中で切れている可能性）と alt（{href, timestamp}。
   * 別のアーカイブ版）も返し、注意と別のアーカイブ版へのリンクを出す（Issue #7）。ランディングページは返さない。
   */
  function item(label, provisional, kind, data, apply) {
    const status = el("span", { class: "wbfb-status", role: "status" }, ...checking());
    const holder = el("span", { class: "wbfb-link" }, pending(label));
    const extra = el("span", { class: "wbfb-trunc" });
    const li = el("li", {}, holder, status, extra);

    async function run() {
      holder.replaceChildren(pending(label));
      extra.replaceChildren();
      status.replaceChildren(...checking());
      const res = await ask(kind, data.host, data.id);
      if (!res.ok) {
        const btn = el("button", { type: "button", class: "wbfb-retry" }, T.retry);
        btn.addEventListener("click", run);
        holder.replaceChildren(link(provisional, label));
        status.replaceChildren(T.sep + T.error + " ", btn);
        return;
      }
      const r = apply(res.result);
      if (r.state === "ok") {
        holder.replaceChildren(link(r.href, label));
        status.textContent = T.paren(T.saved(WBF.captureDate(r.timestamp)));
        if (r.truncated) {
          extra.replaceChildren(el("span", {}, el("span", { class: "wbfb-warning" }, T.truncated), " ", T.truncatedNote));
          if (r.alt) {
            extra.append(el("span", {}, T.tryAlt, link(r.alt.href, T.altLink(WBF.captureDate(r.alt.timestamp)))));
          }
        }
      } else {
        holder.replaceChildren(el("span", { class: "wbfb-name" }, label));
        status.textContent = T.sep + (r.state === "invalid" ? T.invalid : T.none);
      }
    }
    return { li, run };
  }

  function render(data) {
    const jobs = [];
    const landingUrl = "https://" + data.host + "/records/" + data.id;

    const landing = item(T.open, WBF.archiveUrl(landingUrl), "landing", data, (r) =>
      r.status === "ok"
        ? { state: "ok", href: WBF.archiveUrl(r.original, r.timestamp, false), timestamp: r.timestamp }
        : { state: "none" });
    jobs.push(landing.run);

    const body = [];
    if (data.license.status === "cc") {
      body.push(el("p", {}, T.cc(data.license.name)));
      if (!data.files.length) {
        body.push(el("p", {}, T.noPdf));
      } else {
        const ul = el("ul", {});
        const today = WBF.todayString();
        for (const f of data.files) {
          const reason = WBF.fileBlockReason(data.access, f.available, today);
          if (reason) {
            ul.append(el("li", {}, el("span", { class: "wbfb-name" }, f.name),
              el("span", { class: "wbfb-status" }, T.sep + T[reason])));
            continue;
          }
          const it = item(f.name, WBF.archiveUrl(f.href, null, true), "files", data, (r) => {
            const c = r[f.name];
            if (c && c.status === "ok") {
              return {
                state: "ok", href: WBF.archiveUrl(c.original, c.timestamp, true), timestamp: c.timestamp,
                truncated: !!c.possiblyTruncated,
                alt: c.alt ? { href: WBF.archiveUrl(c.alt.original, c.alt.timestamp, true), timestamp: c.alt.timestamp } : null,
              };
            }
            return { state: c ? "invalid" : "none" };
          });
          ul.append(it.li);
          jobs.push(it.run);
        }
        body.push(ul);
      }
    } else {
      body.push(el("p", {}, data.license.status === "conflict" ? T.conflict(data.license.name) : T.notCc));
    }

    const panel = el("section", { id: "wbfb-panel", class: "wbfb", "aria-labelledby": "wbfb-heading" },
      el("h2", { id: "wbfb-heading" }, T.heading),
      el("p", { class: "wbfb-lead" }, T.lead),
      el("h3", {}, T.landing),
      el("ul", {}, landing.li),
      el("h3", {}, T.fulltext),
      ...body,
      ...T.note.map((s) => el("p", { class: "wbfb-note" }, s)));

    const anchor = (findCells("file")[0] || findCells("identifier")[0] || null);
    const table = anchor && anchor.closest("table");
    if (table && table.parentElement) table.parentElement.insertBefore(panel, table);
    else document.body.prepend(panel);

    // files の照会は 1 回にまとめられる（Service Worker 側で同じ照会を共有する）。
    for (const run of jobs) run();
  }

  const data = extract();
  if (data) render(data);
})();
