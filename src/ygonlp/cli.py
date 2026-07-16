"""ygonlp CLI。"""

from __future__ import annotations

import argparse
from pathlib import Path

from .collect import collect, dry_run_lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ygonlp", description="遊戯王カードテキスト研究CLI")
    subparsers = parser.add_subparsers(dest="command")
    collect_parser = subparsers.add_parser("collect", help="カードデータを収集する")
    collect_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    collect_parser.add_argument("--dry-run", action="store_true", help="通信・ファイル変更なしで実行計画を表示")
    collect_parser.add_argument("--force", action="store_true", help="有効なキャッシュを無視して再取得")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "collect":
        build_parser().print_help()
        return 0
    try:
        if args.dry_run:
            print("\n".join(dry_run_lines(args.output, force=args.force)))
        else:
            result = collect(args.output, force=args.force)
            print(f"status: {result['status']}")
            print(f"attempts: {result['attempts']}")
            print(f"data file path: {result['data_path']}")
            print(f"metadata file path: {result['metadata_path']}")
        return 0
    except RuntimeError as exc:
        print(f"エラー: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
