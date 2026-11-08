# JAIRO Cloud メタデータ取得・検索ツール

機関リポジトリからIRDBにハーベストされたメタデータを取得します。
このデータを元に、本文 PDF が Internet Archive の Wayback Machine に保存されているかを調べ、CSVファイルに出力する小さなツールです。出力したCSVファイルは、静的な HTML ページで簡易ですが検索できます。
ページは、担当者向けの `viewer_staff.html` と、一般公開向けの `docs/sample/viewer_public.html` の2種類です。

JAIRO Cloud が停止している間の応急手段として作成しました。

## ご注意

- 元の機関リポジトリ上のすべてのPDFファイルが Wayback Machine に保存されている保証はありません。保存されるのはクローラが到達したものだけです。
- ランディングページが Wayback Machine に保存されていても、PDFファイルは保存されていないことがあります。
- 新しく登録されたアイテムほど保存されていない傾向があります。
- 国際農研のリポジトリ（`jircas.repo.nii.ac.jp`）で 2026-11-07 に確認したところ、全体では82%が Wayback Machine に保存されていました。レコード番号が 2000900 未満のアイテムでは 95%、2000900 以上では 42% が保存されているなど、最近のアイテムが保存されていないようでした。
- ライセンスは、Wayback Machine に保存された各アイテムのメタデータから判定します。メタデータが保存されていないアイテムは判定できません。国際農研のリポジトリ（906 件）で 2026-11-08 に確認したところ、メタデータを取得できたのは 677 件で、CC ライセンスと判定できたのは 34 件（うち PDF を取得できるものは 33 件）でした。
- 提供されているファイルのライセンスやメタデータの詳細は、IRDB上でご確認ください。
- 取得した PDF の再公開及び他機関への提供は、元のファイルのライセンスに従ってください。Internet Archive の利用条件と、IRDB上のメタデータに記述されたライセンス情報、また提供機関に確認してください。

## 免責事項

プログラムの動作確認等は作者によって行っておりますが、万全であることは保証できません。また、数万件単位など、大量のデータ取得についてはテストを行っていません。
ご利用とその結果については、利用者の責任において行ってください。作者はその責を負いません。

## 構成

| Path | Role |
| --- | --- |
| `wb_fulltext_csv.py` | CSV を出力する本体（標準ライブラリのみ） |
| `test_wb_fulltext_csv.py` | オフラインテスト（ネットワーク不使用） |
| `viewer_staff.html` | 担当者向けの閲覧ページ（CSV を表示し、入力した文字列で絞り込む静的ページ。すべての PDF へのリンクと、ライセンスの判定結果を表示する） |
| `result.csv` | 出力例（閲覧ページが読み込む既定のファイル名）国際農研機関リポジトリのデータです |
| `docs/` | GitHub Pages の公開内容（`index.html` と、`sample/` の一般公開向けサンプル `viewer_public.html`） |

## 使い方

Python 3 が必要です。
追加のインストールはありません。

```bash
WB_CONTACT="連絡先メールアドレス(任意)" python wb_fulltext_csv.py --host jircas.repo.nii.ac.jp --out result.csv
```

| Option | Meaning |
| --- | --- |
| `--host` | 対象リポジトリのホスト名（必須） |
| `--query` | IRDB OpenSearch の `q`（既定: `--host` と同じ） |
| `--out` | 出力する CSV（既定: `wayback_fulltext.csv`） |
| `--bulk` / `--no-bulk` | `/record/` 配下を一括で分割取得して突き合わせる（既定）。`--no-bulk` ではアイテムごとに照会する |
| `--license` / `--no-license` | Wayback に保存された各アイテムのメタデータ（WEKO3 の `export/json`）から、ライセンス情報を取得する（既定）。初回は数百件で数十分かかる。`--no-license` では取得せず、ライセンスの列を空にする。`--no-bulk` のときも取得しない |
| `--limit N` | 動作確認用に先頭 N 件だけ処理する |
| `--refresh` | IRDB の一覧を取り直す |
| `--cache-dir` | キャッシュの保存先（既定: `.wb_cache`） |
| `--interval` | リクエスト間隔の秒数（既定: 1.0） |

処理は次の順に進みます。

1. IRDB の [OpenSearch API](https://support.irdb.nii.ac.jp/ja/about/manual/opensearch) で、対象ホストのアイテムを列挙します。
2. 本文 URL を Wayback Machine の CDX API に照会します。
3. PDF ごとに、最新の保存を選びます。
4. Wayback に保存された各アイテムのメタデータを取得し、権利情報とファイルのライセンス種別から、CC ライセンスかどうかを判定します（`--no-license` のときは行いません）。

既定の `--bulk` は、アイテムごとに照会するかわりに `/record/` 配下をまとめて取得するため、通信回数が少なくて済みます。

中断しても、同じ `--cache-dir` で再実行すれば取得済みの分は再利用されます。
キャッシュは、IRDB の検索条件とホストごとに分けて保存されます。
ライセンス情報もキャッシュに残るため、2回目以降は Wayback に新しい保存が現れたアイテムだけを取り直します。
図書館担当者の利用に限定する場合は、`--no-license` を付けるとライセンス情報の取得を省けます。

`WB_CONTACT` は User-Agent に付ける連絡先で、環境変数で渡します。
Wayback のレート制限は公表されていません。
既定では1秒間隔で照会し、429 や 503 が返ったときは待って再試行します。
CDX の応答には、1回あたり20〜60秒かかることがあります。

テストは次のコマンドで実行します。

```bash
python test_wb_fulltext_csv.py
```

## 取得する情報

PDF ファイルごとに1行を出力します。
保存が見つからないアイテムは、状態だけを書いた1行になります。

| Column | Meaning |
| --- | --- |
| `status` | `ok`（PDF の保存を確認）/ `no_capture`（保存なし）/ `no_pdf`（PDF 以外のみ、または無効な記録）/ `cdx_error`（照会に失敗） |
| `title`, `issn`, `publicationName`, `volume`, `number`, `startingPage`, `endingPage`, `publicationDate` | IRDB のメタデータ |
| `updated` | IRDB の更新日（個別の登録日ではありません） |
| `irdb_url`, `record_url`, `record_id` | IRDB の詳細ページ、リポジトリの詳細ページ、レコード番号 |
| `file_url` | 本文 PDF の元の URL |
| `capture_timestamp`, `capture_date` | 最新の保存日時と日付 |
| `mimetype`, `length` | CDX の値（参考。PDF でも `mimetype` が `application/octet-stream` になることがあります） |
| `n_captures` | そのファイルの保存回数 |
| `item_files_captured` | そのアイテムで保存を確認できた PDF の数 |
| `archive_url` | Wayback Machine 上のPDFファイルのURL（`…/web/{timestamp}id_/{original}`） |
| `license_status` | `--no-license` / `--no-bulk` のときは空。`cc`（CC ライセンス。NC や ND の条件が付くものも含む）/ `check`（要確認。権利情報とファイルのライセンス種別が食い違う）/ `not_cc`（権利情報はあるが CC ではない）/ `no_rights`（権利情報なし）/ `no_json`（メタデータを取得できない） |
| `license`, `rights`, `rights_uri`, `licensetype` | 判定した CC ライセンスの名前（例: `CC BY 4.0`）、権利情報の本文と URI、ファイルのライセンス種別（WEKO3 の値） |
| `license_capture_date`, `license_source_url` | 判定に使ったメタデータの保存日と、Wayback Machine 上の URL |

### 実行時の注意事項

- 複数ファイルのうち一部だけが保存されている場合でも、`ok` になります。ファイルの不足は検出できません。
- `capture_date` は Wayback が最後に取得した日です。その後にリポジトリ側で差し替えられた場合は、反映されていません。

## 閲覧ページの設置

閲覧ページは、CSV を表示してテキストボックスの入力で絞り込む単体のページです。
外部のライブラリは使いません。
用途に応じて、次の2種類を使い分けます。
タイトルや表示される説明内容は、適宜変更をお願いいたします。

| File | Use |
| --- | --- |
| `viewer_staff.html` | 担当者向け。すべての PDF へのリンクと、ライセンスの判定結果を表示します。「CC ライセンスのものだけを表示する」で絞り込めます。 |
| `docs/sample/viewer_public.html` | 一般公開向け。CC ライセンスを確認できた PDF だけにリンクを表示します。初期状態では CC ライセンスのものだけを表示します。冒頭のサンプルの説明は書き換えてください。 |

一般公開向けのページには、ライセンス情報を含む CSV（`--no-license` も `--no-bulk` も付けずに出力したもの）を使います。
ライセンスの列が無いか空の CSV では、PDF へのリンクを1件も表示しません。

1. 任意の Web サーバー（機関のWebサーバや GitHub Pages など）に、閲覧ページと出力した CSV を同じフォルダに置きます。CSVファイルのファイル名は `result.csv` にします。
2. 閲覧ページを開くと、同じ場所の `result.csv` を自動で読み込みます。

国際農研のデータを使ったサンプルを、GitHub Pages で公開しています。
https://tzhaya.github.io/jc-fallback/sample/viewer_public.html

手元で試すときは、CSV のあるフォルダで簡易サーバーを起動します。

```bash
python -m http.server 8000
```

その後、`http://localhost:8000/viewer_staff.html` を開きます。

閲覧ページをファイルとして直接開いた場合（`file://`）は、ブラウザの制限により CSV を自動では読み込めません。
画面の「CSV ファイルを選択」から選ぶか、ドラッグ&ドロップで渡してください。
ファイルは端末の中だけで処理され、送信されません。

絞り込みは、CSV のすべての列を対象にします。
空白で区切ると、すべてを含む行だけが残ります。
全角と半角、英字の大文字と小文字は区別しません。
読み込む CSV のファイル名を変えるときは、閲覧ページの `CSV_URL` を書き換えます。

ページの配色と文字組みは、[デジタル庁デザインシステム](https://design.digital.go.jp/dads/)を参考にしています。

## ライセンス

[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/deed.ja)（パブリックドメイン提供）。

## AI の利用

このアプリケーションの作成や更新では、生成 AI によるコーディング支援を利用しています。

## 作者

- Takanori Hayashi
