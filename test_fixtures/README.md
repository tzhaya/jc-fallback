# ビューアの手動確認用 CSV

ビューア（`viewer_staff.html`、`docs/sample/viewer_public.html`、`docs/sample/viewer_public_naro.html`）の表示を目で確かめるための CSV です。
Python のテスト（`test_wb_fulltext_csv.py`）からは使いません。
ビューアの DOM にはテストが無いので、保存履歴の表示を変えたときに使います。

ビューアをブラウザでファイルとして開き、CSV をドラッグするか選択して読み込みます。

## viewer_integrity.csv

`--capture-before 2026-10-28` を指定した想定で、`wb_fulltext_csv.py` の `build_rows` から作った行です。
ホストは架空の `example.repo.nii.ac.jp`、レコード番号は 9000001〜9000013 です。

| レコード番号 | status | integrity_status | 確かめること |
|---|---|---|---|
| 9000001 | ok | consistent | 「履歴で一致」と「同じ digest の最初の保存」が出る |
| 9000002 | ok | changed | 「差異あり」と「この保存版より後に、digest の違う保存あり（2026-11-01）」が出る。PDF へのリンクも出る |
| 9000003 | ok | changed | 採用版の digest が欠けているので、「差異あり」だけが出て、日付の注記は出ない |
| 9000004 | ok | single | 「履歴不足」が出る |
| 9000005 | ok | unknown | 「判定不能」が出る |
| 9000006 | ok と after_cutoff | single | 状態が「取得可」と「基準日時以降のみ」の 2 行。`item_files_captured` は両方 1 |
| 9000007 | after_cutoff | single | 「基準日時以降のみ」。保存日とリンクは出ない |
| 9000008 | ok | consistent | 「同じ digest の最初の保存: 2024-01-01」が出る |
| 9000009 | cdx_error | unknown | 「照会エラー」と「判定不能」が出る |
| 9000010 | ok | changed | 途中で切れている可能性（CC）。注意と「別の保存版（2025-01-01）」のリンクが出る |
| 9000011 | ok | changed | 途中で切れている可能性（CC）。大きい版は基準日時以降にだけある。注意と「より大きい保存版は基準日時以降にだけあります（2026-11-01）」が出て、別の保存版へのリンクは出ない |
| 9000012 | ok | changed | 途中で切れている可能性（非 CC）。担当者向けには注意と別の保存版へのリンクが出る。一般向けには、注意も別の保存版へのリンクも出ない |
| 9000013 | ok | changed | 途中で切れている可能性（CC）。別の保存版は 2025-01-01、全期間で最大の版は 2026-11-01。担当者向けには「全期間で最大の保存: 2026-11-01（基準日時以降）」も出る。一般向けには出ない |

## viewer_legacy.csv

`viewer_integrity.csv` から、保存履歴の 5 列（`capture_digest`、`digest_variants`、`integrity_status`、`digest_first_seen`、`later_digest_date`）と、途中で切れている可能性の 4 列（`possibly_truncated`、`larger_capture_date`、`alt_capture_date`、`alt_archive_url`）を除いた旧形式です。
エラーにならず、保存日の下にラベルも注記も出ず、途中で切れている可能性の注意も出ないことを確かめます。

## 確かめる観点

- `viewer_staff.html`: 保存日の列に、保存履歴のラベルと注記が出る。
- `viewer_public.html` と `viewer_public_naro.html`: 保存履歴のラベルと注記は出ない。リンクの出し方（CC のものだけ PDF へのリンク）は変わらない。
- どのビューアでも、`after_cutoff` の行は「基準日時以降のみ」と表示され、PDF へのリンクは出ない。
- 途中で切れている可能性（9000010〜9000013）:
  - 担当者向けは保存日の列に、一般向けは「Wayback の PDF」のリンクの直後に、注意が出る。
  - 別の保存版へのリンクは、`alt_archive_url` があるときだけ出る（存在しないリンクへ誘導しない）。9000011 では注意と「基準日時以降にだけあります」だけが出る。
  - 一般向けでは、非 CC の 9000012 に注意も別の保存版へのリンクも出ない。
  - 検索欄に「可能性があります」と入れると、担当者向けでは 4 件、一般向けでは CC の 3 件が残る（「CC ライセンスのものだけを表示する」を外しても 3 件）。確認用の行の題名にも「途中で切れている可能性」が入っているので、その語では検索の確認にならない。
