#!/usr/bin/env python3
"""一般公開用の CSV を作る（標準ライブラリのみ）。

CC ライセンスと判定できない行（license_status が cc 以外）の archive_url と
alt_archive_url（本文 PDF の Wayback へのリンク）を空にする。
一般向けビューアは CC 以外の行にリンクを出さないが、CSV を直接開くと
全件の PDF の URL が分かってしまうため、公開する CSV にはあらかじめ含めない。

使い方: python3 make_public_csv.py 入力.csv [出力.csv]   （出力を省略すると入力を上書き）
"""
import csv
import sys

BLANK_COLUMNS = ("archive_url", "alt_archive_url")


def sanitize_rows(rows):
    """CC 以外の行の PDF リンクを空にした新しい行のリストを返す。"""
    out = []
    for r in rows:
        r = dict(r)
        if r.get("license_status") != "cc":
            for k in BLANK_COLUMNS:
                if k in r:
                    r[k] = ""
        out.append(r)
    return out


def main(argv):
    if not 2 <= len(argv) <= 3:
        print(__doc__, file=sys.stderr)
        return 2
    src, dst = argv[1], argv[-1]
    with open(src, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        fields = rd.fieldnames or []
        rows = sanitize_rows(rd)
    with open(dst, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
