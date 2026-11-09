# Wayback 代替取得ツール（JAIRO Cloud 停止時の本文 PDF 取得）

## 目的と背景
- JAIRO Cloud が緊急メンテナンスで約10日間停止（2026-11-07 時点で「向こう1週間ほど」継続の見込み）。
- 代替手段として、本文 URL から Wayback Machine を引き、保存済みの PDF を入手する簡易アプリを作る。
- 主な用途は自機関リポジトリの本文 PDF。保存が保証されないことは承知済み。
- 検証対象リポジトリ: `jircas.repo.nii.ac.jp`（WEKO3）。

## 作る2つのアプリ
1. **App 1（実装済み・2026-11-07 に全件で実通信を検証済み）**: IRDB から自機関アイテムを列挙し、本文 URL を元に Wayback の最新アーカイブ URL を取得して CSV 出力。担当者が手元で参照・ダウンロードする。登録件数が少ない機関向け。取得は、レコードごとの照会（既定）と `--bulk`（`/record/` 配下を一括で分割取得）の2通り。`--license`（2026-11-08 実装）で export JSON からライセンスを判定して列に出す。**`--bulk` と `--license` は既定で有効**（`BooleanOptionalAction`。`--no-bulk` / `--no-license` で無効）。`--no-license` と `--no-bulk` のときはライセンスの 7 列を空にする（`--no-bulk` では export JSON の保存先が分からないため、警告して取得しない）。
   - **保存履歴の digest（Issue #5、2026-11-09 実装）**: CDX の `digest` を同じ本文 URL（`file_key`）の capture どうしで比べ、`integrity_status` を出す。計画は Issue #5 の r2 とその補遺 r2.1。判定は全期間の有効な capture で、優先順は `changed`（比較可能な digest が 2 種類以上）> `unknown`（欠損あり）> `consistent`（2 回以上で 1 種類）> `single`（1 回）。`changed` は「保存されたバイト列の差異」であり、内容の変更とは限らない（途中で切れた保存、gzip のまま保存されたものでも起きる。下記の実測）。表示は「差異あり」。**`changed` でも PDF へのリンクは出す**（最終の判断は人間が行う。利用者の方針、2026-11-09）。digest は `normalize_digest`（空白除去、`sha1:` を大小無視で除去、英大文字化、`[A-Z2-7]{32}` 以外は欠損）。採用版の digest が欠損なら `digest_first_seen` と `later_digest_date` は空。
   - **`--capture-before`**: UTC で `timestamp < 指定値`（未満。日付だけなら 00:00:00 UTC = 日本時間 09:00）の capture から採用する。CDX は全期間を取得して手元で絞る（`to=` は使わない。上限より後の変化を見るため）。上限より後にしか無いファイルは `after_cutoff`（`file_url` と全期間の集計だけを埋め、採用版に依存する列は空。`item_files_captured` は採用できた PDF の数）。export JSON の候補も上限で絞り、上限を満たさないライセンスのキャッシュは、取り直しに失敗しても返さない（`license_cache_usable`。計画レビュー P1）。`parse_capture_before("")` は `""`（制限なし）を返し、`None`（未指定）と区別する（CLI の空文字で `.env` を打ち消せるように）。no_json の理由は `license_missing_reasons` で 4 つに分けて実行ログに出す。
2. **App 2（2026-11-08 実装、`extension/`）**: Chrome / Edge 向けの拡張機能（MV3。まずは「パッケージ化されていない拡張機能を読み込む」で配る）。**IRDB の詳細ページだけ**が対象（CiNii Research は対象外）。対象リポジトリは JAIRO Cloud 全体で、`*.repo.nii.ac.jp` と、独自ドメインで運用しているものの一覧（`lib.js` の `CUSTOM_HOSTS`、18 ホスト。利用者から提供、2026-11-08）。一覧外のホストは、WEKO3 と同じ URL の形でも対象外（JAIRO Cloud 以外は何も表示しない方針のため）。独自ドメインのうち naro、tottori-u、u-tokyo の 3 つは、IRDB の URL が `/records/{id}` と `/record/{id}/files/…` であることを確認済み。残り 15 は未確認。登録件数が多い機関向け。
   - **一般向け**: 本文 PDF へのリンクは、IRDB の「権利情報」が CC（NC/ND 付きも CC）の資料にだけ出す。CC の種類が食い違うときは出さない。ランディングページ（`/records/{id}`）の保存版へのリンクはすべての資料に出す。
   - 公開日（ファイルの `Available`）が未来のファイル、アクセス権が restricted / metadata only のもの、embargoed で公開日が無いものには、CC でもリンクを出さない（2001115 は CC だが Available 2027-05-13 のエンバーゴ）。
   - 保存の確認: 確認が終わるまで項目名はリンクにせず（`aria-disabled`）、点の連続（JS で 300 ms ごとに 1〜6 個へ書き換える。CSS の `content` のアニメーションでは、実環境で点が増えなかった）で確認中を示す。Service Worker が CDX を照会し、保存日付きのリンク・「保存されていません」・「保存版が壊れている可能性」（length 不正のみ）のどれかにする。照会に失敗したときだけ `…/web/20991231id_/{URL}`（ランディングは `id_` なし）をリンクにし、「再確認」ボタンを出す（2026-11-08 に、当初の「仮リンクを先に出す」方式 C から変更）。CDX は files（`/record/{id}/files/`）と landing（`/records/{id}`）の 2 回。非 CC の資料では files を照会しない。照会は 1 秒間隔・180 秒タイムアウト・429/502/503/504 は再試行。結果は `chrome.storage.local` に 6 時間キャッシュ（失敗はキャッシュしない）。

## ファイル
- `wb_fulltext_csv.py`: App 1 本体（標準ライブラリのみ）。使い方は先頭の docstring。
- オプションは `.env`（カレントディレクトリ、`--env-file` で変更。`utf-8-sig`）にも書ける（Issue #3、2026-11-09）。キーは `WB_` + オプション名（`ENV_OPTIONS`。`--limit`/`--refresh` は対象外、`WB_CONTACT` を含む）。優先順はコマンドライン > 環境変数 > `.env` > 既定値。argparse の既定値は `None` にし、`resolve_options` が**採用した値だけ**を型変換する（下位の不正値を CLI で上書きできるように）。`.env` は引数の解析後に読む（壊れていても `--help` が出るように）。未知の `WB_*` キーは警告。テストは手元の `.env` を読まないよう `--env-file` と dict の環境変数を渡す。
- README.md と `docs/index.html` は使い方（オプション表など）を二重に書いている。片方を直したらもう片方にも反映する。
- 取得対象は実行時に `--host`（または `.env` の `WB_HOST`。どちらか必須）で指定する。`--query` の既定は `--host` と同じ。キャッシュ（`irdb_entries.json` は検索条件、`cdx*.jsonl` はホストと照会条件 `CDX_QUERY`〈`fl` と `filter`〉）は条件ごとに識別し、不一致は再利用しない（旧形式のキャッシュは無効扱いで取り直す。CDX のキャッシュは `drop_stale_cache_lines` が照会条件の違う行をファイルから消し、1 回だけ警告する）。JSONL は追記前に壊れた末尾を除去する。IRDB 一覧は実際の取得件数でページ送りし、totalResults に達する前に空ページが来たらキャッシュせず終了する。
- ビューアは 3 つある。
  - `viewer_staff.html`: 担当者向け。全 PDF へのリンク、ライセンス列、「CC のみ」絞り込みがある。
  - `docs/sample/viewer_public.html`: 一般公開向け。CC と判定した PDF にだけ Wayback へのリンクを出す。初期状態は CC のみ表示。
  - `docs/sample/viewer_public_naro.html`: `viewer_public.html` の農研機構版。`result_naro.csv` を読む。
  - 3 つのコードは同じで、違うのは `PUBLIC` 定数（担当者向けだけ `false`）、`CSV_URL`（naro 版だけ `result_naro.csv`）、見出しと説明文だけ。1 つを直したら、残りの 2 つにも反映する。
  - 旧名は `viewer.html` と `docs/sample/view.html`（2026-11-08 に改名）。
- ライセンスの判定（`license_for_file`）: 権利情報の URI か本文の CC URL、またはファイルの `licensetype`（license_0〜12）を CC とし、**NC/ND 付きも CC と同じ扱い**（利用者の判断）。CC の種類が食い違う、または `license_no` なのに権利情報が CC のものは `check`（要確認）。export JSON の取得結果は `export_json.jsonl` にキャッシュし、CDX に新しい capture が現れたら取り直す。最新の capture が通信エラーで取れず古い capture で判定したときは、`latest` に使った capture の timestamp を入れ、次回の実行で最新を取り直す（PR #1 の Codex レビューで指摘）。
- `extension/`: App 2。`lib.js`（DOM に依存しない判定ロジック。content script・Service Worker・Node テストで共有）、`content.js`（IRDB の表を th の見出し〈ファイル/File、権利情報/Rights、資源識別子/Identifier、アクセス権/Accessrights〉で読み、パネルを表の直前に挿入）、`background.js`（CDX 照会とキャッシュ）、`content.css`、`manifest.json`。テストは `node --test extension/test/*.test.js`（Node 標準のみ。DOM 部分は対象外で、IRDB の実ページ 9 件にスクリプトを注入して確認した）。
- `test_fixtures/`: ビューアの手動確認用 CSV（`viewer_integrity.csv` は保存履歴の判定と `after_cutoff` を含む、`viewer_legacy.csv` は新しい 5 列の無い旧形式）。Python のテストからは使わない。内容は同フォルダの README.md。
- `test_wb_fulltext_csv.py`: オフラインテスト。`python3 test_wb_fulltext_csv.py` で全 `test_*` を実行（pytest は使わない）。

## 確認済みの事実
- **IRDB OpenSearch**: `https://irdb.nii.ac.jp/opensearch/search`。パラメータは q/title/author/journal/issn/publisher/fulltext(ALL|1)/count(20,50,100)/start/format(rss|atom)。JSON は無い。レート制限・利用条件は仕様ページに記載なし。
- **Atom の entry に本文ファイル URL は無い**。得られるのは IRDB 詳細ページ（`link`）、ソース元詳細ページ（`URI` = `https://jircas.repo.nii.ac.jp/records/{id}`）、`irname`、`prism:*`。
- 本文 URL は `https://jircas.repo.nii.ac.jp/record/{id}/files/{ファイル名}.pdf`（クエリなし）。`{id}` は `URI` の番号と同じ。ファイル名は IRDB から導けないため、CDX の**レコード番号配下のプレフィックス照会**で引く方式にした。
- 実測（レコード 2000885）: CDX に1件あり。`20250722192940`、status 200、length 3427839、**mimetype は `application/octet-stream`**。
  → mimetype で PDF を絞ってはいけない。URL の拡張子で判定する（実装済み）。
- `q=jircas.repo.nii.ac.jp&fulltext=1` で totalResults=929（表示した20件はすべて jircas）。`q` は横断検索なので、URI のホストで絞る（実装済み）。`start` を進めて 929 件を完走できた。ホストで絞ると **906 レコード**（重複なし）。
- **IRDB 一覧の取得は、ページ送りの間に並びが変わると、重複と欠落が同数出る**（最初の取得で 36 件の重複＝36 件の欠落が出て、870 レコードに見えた。取り直すと 906 件で重複なし）。2026-11-08 は取り直しても 2 回続けて 36 件の重複が出た（`updated` が同じ日のものが 98% で、同順位の並びが要求ごとに変わるためと推測）。→ `fetch_entries_complete` で一覧を最大 5 回取り、詳細ページ URL で和集合をとる（実測で 893 → 901 → 929/929 件、3 回で揃った）。5 回で揃わなければ `IncompleteListing` で終了し、一覧をキャッシュしない（和集合は重複が消えるので、重複チェックでは欠落に気付けないため。PR #1 の Codex レビューで指摘）。
- **CiNii Research OpenSearch**（`https://cir.nii.ac.jp/opensearch/v2/all?...&format=json`）は `appid` なしでも応答した（仕様上は必須。将来拒否される可能性がある）。`q=jircas.repo.nii.ac.jp` は 0 件で、機関名での検索は通る。`dc:source` に返るのは**詳細ページ URL**（`/records/{id}`）で、本文ファイル名は無い。→ 完全一致照会には使えない。DOI（`10.34556/000{id}`）は record ID と対応する。
- **CDX の prefix 照会は、`/record/` で終わる指定でも `/records/…` を返す**（実測）。詳細ページ、`export/json`、`export/bibtex`、`/records/{id}/file_details/x.pdf`（**HTML。拡張子だけ .pdf**）が混ざる。`--bulk` では `/record/{id}/files/…` だけに絞る（`group_by_record`）。これを怠ると PDF と誤判定する（実際に 1,477 行中 704 行が偽だった）。
- `/record/` 配下を一括取得すると、`filter=statuscode:200` で 8,600 行・2 ページ（5,000 + 3,600）。`/records/{id}/files/…` の行は **0 件**で、`record/` 固定の判断を裏付ける。`/record/{id}/files/` 配下の拡張子は .pdf が 1,681 行、.jpg が 5 行。
- CDX の応答は 1 回 20〜60 秒かかり、時間は返す行数ではなく照会ごとの固定コストが支配的（`limit=5` でも約 21 秒）。Internet Archive は一時的に「Temporarily Offline」（503）になることがある。タイムアウトは 180 秒にし、`--bulk` は途中で失敗したら CSV を出さずに終了する（再実行で続きから進む）。

## 実測の結果（2026-11-07、906 レコード、`--bulk`）
- アイテム単位: ok = 743（**82.0%**）、no_capture = 156、no_pdf = 7（下記の length 不正）。ファイル単位: 保存を確認できた PDF は 766 件。複数 PDF を持つ ok のアイテムは 19 件。
- 82.0% は**上限寄りの値**。複数ファイルのうち一部だけ保存されている場合も ok に入り、検出できない（IRDB にも CDX にも、ファイル総数の情報が無い）。
- **登録順（record ID）で見ると境目がはっきりしている**（WEKO3 の ID は登録順に採番される。担当者に確認済み）。ID < 2000900: 648/679（95.4%）、ID ≧ 2000900: 95/227（**41.9%**）。100 件刻みでは 2000800 番台が 95/95、2000900 番台 20/68（29%）、2001000 番台 49/99（49%）、2001100 番台 26/60（43%）。境目は ID 2000890 前後。**その ID の登録日が確認できないため、境目の原因（クローラの到達範囲など）は未解明**で、調査はここで打ち切った。
- 公開年（`publicationDate`。刊行年月であり登録日ではない）別の ok: 1994〜2016 年 261/266（98.1%）、2017〜2024 年 277/293（94.5%）、2025〜2026 年 205/347（59.1%）。2026 年は 1 月 9/10、2 月 23/24、**3 月 2/28**。
- 「新しい（登録が遅い）アイテムほど保存されていない」。JAIRO Cloud 停止中に必要になる新しい本文ほど Wayback では取れない。App 2 に進む判断では、この点を考慮する。
- IRDB の `updated` は、**登録日・個別の更新日としては使えない**。906 件中 890 件（98%）が 2026-06-07 の同一日（一括更新と思われる）で、ID との対応も検証できない。6 月以外の 16 件の ok は 2 件だけで、「更新されると再クロールされる」仮説は、件数が少なく確認できていない。`updated` は CSV の列として出力している。
- 詳細ページだけが保存され、本文は保存されていないアイテムが相当数ある（`/record/{id}/files/` の絞り込み前に `no_pdf` だった 124 件はすべてこれ。画像ではない）。現在の出力では no_capture に含まれる。
- **CDX の `length` が負の値の capture がある**（status 200 なのに）。7 件（record 2000927、2000932〜2000936、2000941。いずれも 2025-09-27〜30 の capture）。実際に確認できたのは 2000936 の 1 件で、`archive_url` が **HTTP 404**（Wayback の HTML エラーページ）だった。残り 6 件は未確認。`has_valid_length` で length が正でない capture を無効とし、該当アイテムは `no_pdf`（7 件）になる。
- **ダウンロード確認済み**: `archive_url`（`…/web/{timestamp}id_/{original}`）で、3 件（2000258、2001135、2000885）の原本 PDF を取得できた。いずれも先頭が `%PDF-1.6`、末尾に `%%EOF`。CDX の `length`（圧縮後の WARC 内サイズ）は実サイズと一致しない（例 3,427,839 に対し 3,631,163 B）。

## ライセンス情報の取得（2026-11-08 調査）
- 取得元は Wayback 上の `/records/{id}/export/json`（WEKO3 のレコード JSON）。**プロパティキーはリポジトリごと・アイテムタイプごとに異なる**ので、`item_30002_rights6` を決め打ちしない。
  - `item_{ID}_{名前}{連番}` の ID はコピー元アイテムタイプの ID、連番はアイテムタイプ内の並び順。jircas は `item_type_id` が 40039 だが、キーは `item_30002_*`（30002 をコピーして作成）。
  - つくば（tsukuba.repo.nii.ac.jp）は `item_5_rights_12` と `item_1742563285404`（権利情報が2つ）。ファイル情報も `item_files`。同一リポジトリ内でもアイテムタイプは複数ある（実測で 4、5、10、12）。出典: jc-import-file-maker の `docs/weko3_property_key_naming.md`、`docs/itemtype_field_mapping_tsukuba.md`。
- **キーに依存しない読み方**: `metadata` の各値のうち、`attribute_value_mlt` の要素に `subitem_rights` / `subitem_rights_resource` を持つものが権利情報（サブフィールド名はアイテムタイプ間で共通）。`attribute_type == "file"` のものがファイル情報。`attribute_name`（表示名）は編集できるので使わない。
- ファイルごとの `licensetype`（`license_0`〜`license_12` が CC。`license_0` = CC BY 4.0、`license_12` = CC0）もある。`license_no`、`license_free`、つくばの `license_note` は CC ではない。WEKO3 内部のファイル用ライセンス種別で、JPCOAR の rights とは別物（両者の一致は保証されない）。
- 実測（jircas 2000879）: rights に `https://creativecommons.org/licenses/by/4.0/deed.en`、PDF とサムネイルの `licensetype` が `license_0`。2000258 は rights も licensetype も無い。エクスポート JSON は値の無い項目を出さない。
- **IRDB OpenSearch の Atom / RSS に権利情報は無い**（2026-11-08、`irdb.nii.ac.jp/07496/0007463258` = jircas 2001157 で確認。詳細ページ HTML には表示されている）。Atom の entry は title/link/id/author/subject/language/publisher/URI/prism:*/content/irname/updated だけ。RSS の `copyright` はチャネル単位の NII の表示。
- IRDB には OAI-PMH（`https://irdb.nii.ac.jp/oai`）があり、Identify・ListMetadataFormats（junii2、jpcoar、jpcoar_1.0、jpcoar_2.0、oai_dc）・ListSets（jalc、crossref、datacite、ndl、ciniid、ciniia、ciniir、irdb、oer。機関別の set は無い）は応答する。ただし GetRecord・ListIdentifiers・ListRecords は、試した引数の組み合わせがすべて `badArgument` で、識別子の形式は不明。IRDB の robots.txt には `Disallow: /*?*` がある。
  - 識別子は JPCOAR のウェビナー資料によると `oai:irdb.nii.ac.jp:{機関コード}:{番号}`（Atom の `id` から作れる。例 `oai:irdb.nii.ac.jp:07496:0007463258`）。この形式でも、存在しない ID（`…:00000:0000000000`）でも、同じ `badArgument` になる（`idDoesNotExist` にならない）。GET と POST、`:` のエンコード、引数の順序、`oai_dc` でも同じ。ListMetadataFormats は `identifier=bogus` でも一覧を返す。→ **レコード取得の verb は、識別子の形式によらず一般公開されていない**と見られる（2026-11-08）。使えるかは NII（ir@nii.ac.jp）に確認が要る。
- **IRDB の詳細ページ HTML（`/07496/0007463258`）には、権利情報（`http://creativecommons.org/licenses/by/4.0/`）と本文ファイル URL（`https://jircas.repo.nii.ac.jp/record/2001157/files/Plant_Direct-2026-10(3)_e70160.pdf`）の両方が載っている**（curl で取得。クエリなしのパスは robots.txt の Disallow に当たらない）。App 2 で「詳細ページの DOM から本文 URL が読めるか」の確認はこの1件で通った。
- export JSON には本文ファイルの URL（`url.url`）と `objectType`（fulltext / thumbnail）も入っている。
- **全件取得の実測（2026-11-08、jircas 906 レコード）**: CDX に export/json があるのは 692 レコード（IRDB 一覧外を含む）。最新の有効な capture（length が正）から最大 3 件試し、JSON として読めたのは 906 中 677 件。失敗は length が負の capture しか無いもの 11 件（2000888、2000927〜2000947 付近）、HTTP 404 が 1 件（2000158）。
  - 2026-11-08 夕方に `--bulk` の一覧を取り直す（旧形式キャッシュのため）と、export/json の保存があるのは 681 レコードに変わった（PDF の結果は ok = 743 で同じ）。ライセンスの集計値（CC 34、うち ok 33）も同じ。CC の内訳はアイテム単位で CC BY 4.0 = 29、CC BY-NC-ND 4.0 = 4、CC BY-NC 4.0 = 1（PDF 取得可では 29 / 3 / 1）。
  - 1 件（2000999）は **`Content-Encoding: gzip`** で返り、`Http.get` がそのまま UTF-8 で読もうとして失敗した。展開すれば読める。実装では gzip を展開すること。
  - 取得中に接続拒否（WinError 10061）が 73 回あった（1 秒間隔）。再試行で回復した。
  - 権利情報のキーは全件 `item_30002_rights6`（187 件）。残り 490 件には権利情報が無い（ほぼ ID < 2000900）。
  - CC と判定できたのは 34 レコード（PDF 取得可 743 件のうち 33 件 = 4.4%。ID < 2000900 で 21/648、ID ≧ 2000900 で 12/95）。rights の CC URI と PDF の `licensetype`（license_0 = 32、license_5 = 4、license_3 = 1 ファイル）は**全件で一致**し、片方だけのものは無かった。
  - CC の URI は `subitem_rights_resource` だけでなく `subitem_rights`（本文）に書かれていることもある（http / https、`/deed.en`、`/deed/en` の揺れあり）。
  - 非 CC の rights 153 件の多くは「©JIRCAS」「転載には許可が必要」など。jircas の利用規定ページ（`https://www.jircas.go.jp/ja/about/guide` など）を URI にしたものが 3 件ある。
  - Wayback で取れる PDF のファイル名は、すべて export JSON のファイル一覧に含まれていた。
- 保存状況（`cdx_bulk.jsonl` から集計、statuscode 200）: export/json があるのは 906 中 689 レコード（うち 11 件は length が負の capture だけ）。ok の 743 件のうち 668 件。ID ≧ 2000900 では 227 中 63 件、ok 95 件のうち 48 件だけ。

## 保存履歴の digest の実測（2026-11-09、jircas 906 レコード、Issue #5）
- バルクを digest 付きで取り直すと 8,600 行（以前と同じ）。digest の欠損や正規化が必要な値（`sha1:` 付き、小文字）は 0 件。アイテム単位の結果（ok 743、no_capture 156、no_pdf 7）とライセンスの集計（CC 34、うち ok 33）は以前と同じ。
- ファイル単位（ok 766）: consistent 445、changed 56（56 レコード。ID < 2000900 が 38、≧ 2000900 が 18。digest が 2 種類 53、3 種類 3）、single 265、unknown 0。
- jircas の最新の PDF の capture は 2026-07-27。`--capture-before 2026-10-28` でも結果は変わらない（after_cutoff 0、`later_digest_date` のあるファイル 0）。export JSON も全件が上限より前。no_json 229 件の内訳は、有効な保存が無い 228、読めない 1。
- **CDX の digest は、`id_` で取得した本文の SHA-1 の Base32 と一致する**。changed の 4 ファイルで、digest の違う capture 8 件を取得し、8 件すべて一致した（照合できなかったものは無い）。全件を照合したわけではない。
- **changed の多くは、途中で切れた capture（truncated）が原因とみられる**。照合した 4 ファイルでは、違う digest の capture の本文が、ちょうど 1,048,576 B（1 MiB）か 5,242,880 B（5 MiB）だった（先頭は `%PDF-` だが、全体より短い）。内容が差し替えられたのではなく、クローラのサイズ上限で切れたものと見られる。
- **採用した最新の capture が途中で切れている例がある**: 2000419 と 2000404 の `archive_url` は、どちらも 5,242,880 B で、末尾に `%%EOF` が無かった（HTTP 200）。CDX の length（圧縮後のレコード長）からは見分けられない。既存の「最新を採用」の方針のままでは、壊れた PDF へのリンクを出すことがある。対応（例: changed のとき、切れていない capture を選ぶ）は未決。
  - Chrome の PDF ビューアで開くと、途中で切れた 3 件（2000404 の 1 MiB と 5 MiB、2000419 の 5 MiB）はどれも「PDF ドキュメントを読み込めませんでした」になり、1 ページも表示されなかった（Linearized の PDF でも同じ）。切れていない版は、2000404 が 120 ページ、2000419 が 92 ページ表示された。
- **digest が違っても内容が同じことがある（gzip）**: 2000419 の 2025-06-18 の capture は、Wayback が `Content-Encoding: gzip` 付きで返し、本文は gzip のままだった（digest はこの gzip のバイト列の SHA-1）。展開すると、2025-10-14 の capture とバイト単位で同じだった。したがって `changed` には、途中で切れたものと gzip のものという、内容の変更ではない例が含まれる。ブラウザはこのヘッダを見て展開するので、ブラウザでダウンロードすれば PDF として読めるはずだが、確かめていない（ヘッダを無視するツールで取ると gzip のまま保存される）。
- **`warc/revisit` の行がある**: `filter=statuscode:200` を外したバルクの 1 ページ目（5,000 行）では、`/record/{id}/files/` の 2,002 行のうち 156 行が revisit（statuscode `-`）だった（ほかは 200 が 1,682、503 が 139、302 が 16、404 が 7、301 が 2）。現在は 200 だけを数えるので、`single` と `n_captures` は少なめに出る。対象に含めるかは未決。

## App 2 のための確認（2026-11-08）
- `…/web/20991231id_/{URL}` は最新の capture へ 302 で転送され、`id_` も保たれる（2000885 で確認）。保存が無い URL は 404。ただし status や length を見ずに最新を選ぶので、length が負の capture に飛ぶ可能性がある。→ CDX で確認できたら timestamp 指定のリンクに置き換える。
- **CDX API は `Access-Control-Allow-Origin` を返さない**（ページの JS からは読めない）。Availability API（`archive.org/wayback/available`）は `*` を返すが length が無い。→ 拡張機能の Service Worker と `host_permissions` で CDX を引く。
- IRDB の詳細ページはサーバー側描画。`<th>ファイル</th>` の td に `li.mb-2` ごとのファイル（`span` の objectType ラベル、本文 URL の `a`、`Available` の日付）、`<th>資源識別子</th>` に `URI` のリンク（`/records/{id}`）、`<th>権利情報</th>` に CC の URI（リンクと本文）がある。英語版（`/en/…`）は th が File / Identifier / Rights / Accessrights。
- IRDB には JAIRO Cloud 以外のリポジトリ（DSpace の `/bitstreams/…/download` など）の資料もある。JAIRO Cloud にも独自ドメインで運用しているものがある（`repository.dl.itc.u-tokyo.ac.jp` など。当初は JAIRO Cloud 以外の WEKO3 と誤認していた）。App 2 は `*.repo.nii.ac.jp` と `CUSTOM_HOSTS` 以外を対象外にした。
- IRDB に載っているファイル名と、Wayback に保存されたファイル名は違うことがある（2000879 は IRDB が `gars-4_en-2nd_ed.pdf`、保存されているのは差し替え前の `gars-e_4.pdf`）。App 2 は IRDB に載っているファイルだけを出す。

## 注意点（ハマりどころ）
- CDX の `collapse=urlkey` は**最古**の capture を残す。「最新1件」は全 capture を取ってクライアント側で最大 timestamp を選ぶ（実装済み）。
- WEKO3 の詳細ページは `/records/{id}`、本文ファイルは `/record/{id}/files/…`。本文 URL の path は **`/record/` 固定**で照会する（暫定。WEKO3 の GitHub リポジトリ RCOSDP/weko が 404 でソース確認不可。ファイルは単一の保存先を指すため複数表記の必要性は低いと判断）。
- 原本を取るには `…/web/{timestamp}id_/{original}`（4 件で確認済み）。`id_` なしはビューア HTML が返る。ダウンロード後の `%PDF-` ヘッダ検証は、ツールには未実装（手動で確認した）。status 200 の capture でも、実体が 404 ページのことがある（`length` が負の値のもの）。
- Wayback のレート制限は未公表（第三者情報で約1リクエスト/秒が目安）。既定 1 秒間隔、429/503 は待って再試行。User-Agent の連絡先は環境変数 `WB_CONTACT` から取る。**メールアドレスをコードに埋め込まない**。
- IRDB の HTML ページと jircas のページは、調査時の取得ツールが `robots_disallowed` で拒否された（robots.txt 本体は未確認）。HTML のスクレイピングは避け、OpenSearch 等の公式口を使う。
- Internet Archive の利用規約は全文を読めていない（JS 描画のため）。取得した PDF の再公開・他機関提供を伴う場合は、規約全文と所内の著作権担当に確認する。

## 未検証（実通信で確認したいこと）
1. 全件のダウンロード。確認したのは 4 件（PDF 3 件、404 の 1 件）だけ。`length` が負の残り 6 件も未確認。
2. 部分保存（複数ファイルのうち一部だけ保存）の検出。現状はできない。
3. no_capture 156 件のうち、詳細ページだけ保存されているものの内訳（出力を分けるかは未決）。
4. IRDB OpenURL は書誌事項から詳細ページへリダイレクトする仕様。本文 URL は返さない（用途が限られる）。
5. CiNii Research API の `appid` なし利用がいつまで通るか。

## CSV の列
`status, title, issn, publicationName, volume, number, startingPage, endingPage, publicationDate, updated, irdb_url, record_url, record_id, file_url, capture_timestamp, capture_date, mimetype, length, n_captures, item_files_captured, archive_url, license_status, license, rights, rights_uri, licensetype, license_capture_date, license_source_url, capture_digest, digest_variants, integrity_status, digest_first_seen, later_digest_date`（ライセンスの 7 列は `--no-license` / `--no-bulk` のときは空。保存履歴の 5 列は Issue #5 で末尾に追加し、既存の列の位置は変えていない）。
`prism:*` は列名から `prism:` を除いた名前。同名要素が複数ある場合（言語違い）は lang を出さず、内容を ` | ` で連結する。`n_captures` はファイル単位の capture 数（`--capture-before` の上限より後も含む全期間）、`item_files_captured` はアイテム単位の、上限内で採用できた PDF 数（`cdx_error` の行は空欄）。`status` に `after_cutoff` がある（`--capture-before` 指定時のみ）。以前の `pub_date` は `publicationDate` に置き換えた。

## 次の作業
1. 公開済みの GitHub Pages の URL が `sample/view.html` から `sample/viewer_public.html` に変わった。旧 URL を残すか（転送用ページ）は未決。
2. 実装の改善（任意）: ダウンロードした PDF の先頭 `%PDF-` 検証、`robots` や CDX の応答エラーの扱い、no_capture の細分化。
3. Issue #5 の続き（要判断）: 途中で切れた capture を採用しない方法（上記の実測を参照）と、`warc/revisit` の行を比較の対象に含めるか。含めるなら `CDX_FILTER` が変わり、キャッシュは取り直しになる。
4. App 2: 実際に拡張機能として読み込んだ状態での動作確認（Service Worker の CDX 照会とキャッシュ、長い照会中に Service Worker が止まらないか）。必要なら Chrome Web Store での公開を検討する。

## 開発上の約束
- 依存は標準ライブラリのみ。テストは固定 fixture でネットワークに依存させない。
- 件数や保存率は、実データで再計算してから報告する。
