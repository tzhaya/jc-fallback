# ビューアの手動確認用 CSV

ビューア（`viewer_staff.html`、`docs/sample/viewer_public.html`、`docs/sample/viewer_public_naro.html`）の表示を目で確かめるための CSV です。
Python のテスト（`test_wb_fulltext_csv.py`）からは使いません。
ビューアの DOM にはテストが無いので、保存履歴の表示を変えたときに使います。

ビューアをブラウザでファイルとして開き、CSV をドラッグするか選択して読み込みます。

## viewer_integrity.csv

`--capture-before 2026-10-28` を指定した想定で、`wb_fulltext_csv.py` の `build_rows` から作った行です。
ホストは架空の `example.repo.nii.ac.jp`、レコード番号は 9000001〜9000009 です。

| レコード番号 | status | integrity_status | 確かめること |
|---|---|---|---|
| 9000001 | ok | consistent | 「履歴で一致」と「同じ内容の最初の保存」が出る |
| 9000002 | ok | changed | 「変更あり」と「この保存版より後に、別の内容の保存を確認（2026-11-01）」が出る |
| 9000003 | ok | changed | 採用版の digest が欠けているので、「変更あり」だけが出て、日付の注記は出ない |
| 9000004 | ok | single | 「履歴不足」が出る |
| 9000005 | ok | unknown | 「判定不能」が出る |
| 9000006 | ok と after_cutoff | single | 状態が「取得可」と「基準日時以降のみ」の 2 行。`item_files_captured` は両方 1 |
| 9000007 | after_cutoff | single | 「基準日時以降のみ」。保存日とリンクは出ない |
| 9000008 | ok | consistent | 「同じ内容の最初の保存: 2024-01-01」が出る |
| 9000009 | cdx_error | unknown | 「照会エラー」と「判定不能」が出る |

## viewer_legacy.csv

`viewer_integrity.csv` から、保存履歴の 5 列（`capture_digest`、`digest_variants`、`integrity_status`、`digest_first_seen`、`later_digest_date`）を除いた旧形式です。
エラーにならず、保存日の下にラベルも注記も出ないことを確かめます。

## 確かめる観点

- `viewer_staff.html`: 保存日の列に、保存履歴のラベルと注記が出る。
- `viewer_public.html` と `viewer_public_naro.html`: 保存履歴のラベルと注記は出ない。リンクの出し方（CC のものだけ PDF へのリンク）は変わらない。
- どのビューアでも、`after_cutoff` の行は「基準日時以降のみ」と表示され、PDF へのリンクは出ない。
