"""ygonlp CLI。"""

from __future__ import annotations

import argparse
from pathlib import Path

from .collect import collect, dry_run_lines as collect_dry_run_lines
from .measure import MeasureError, dry_run_lines as measure_dry_run_lines, measure
from .preprocess import PreprocessError, dry_run_lines as preprocess_dry_run_lines, preprocess


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ygonlp", description="遊戯王カードテキスト研究CLI")
    subparsers = parser.add_subparsers(dest="command")
    collect_parser = subparsers.add_parser("collect", help="カードデータを収集する")
    collect_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    collect_parser.add_argument("--dry-run", action="store_true", help="通信・ファイル変更なしで実行計画を表示")
    collect_parser.add_argument("--force", action="store_true", help="有効なキャッシュを無視して再取得")
    preprocess_parser = subparsers.add_parser("preprocess", help="収集済みカードデータを正規化する")
    preprocess_parser.add_argument("--input-metadata", type=Path, required=True, help="raw metadata JSON")
    preprocess_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    preprocess_parser.add_argument("--dry-run", action="store_true", help="出力せずに入力・変換計画を検証")
    preprocess_parser.add_argument("--force", action="store_true", help="有効な前処理出力を無視して再生成")
    measure_parser = subparsers.add_parser("measure", help="前処理済みテキストの基本指標を測定する")
    measure_parser.add_argument("--input-metadata", type=Path, required=True, help="preprocessing metadata JSON")
    measure_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    measure_parser.add_argument("--dry-run", action="store_true", help="出力せずに入力・測定計画を検証")
    measure_parser.add_argument("--force", action="store_true", help="有効な測定出力を無視して再生成")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command not in {"collect", "preprocess", "measure"}:
        build_parser().print_help()
        return 0
    try:
        if args.command == "collect" and args.dry_run:
            print("\n".join(collect_dry_run_lines(args.output, force=args.force)))
        elif args.command == "collect":
            result = collect(args.output, force=args.force)
            print(f"status: {result['status']}")
            print(f"attempts: {result['attempts']}")
            print(f"data file path: {result['data_path']}")
            print(f"metadata file path: {result['metadata_path']}")
        elif args.dry_run:
            if args.command == "preprocess":
                print("\n".join(preprocess_dry_run_lines(args.input_metadata, args.output, force=args.force)))
            else:
                print("\n".join(measure_dry_run_lines(args.input_metadata, args.output, force=args.force)))
        elif args.command == "measure":
            result = measure(args.input_metadata, args.output, force=args.force)
            print(f"status: {result['status']}")
            print(f"output data file path: {result['output_data_path']}")
            print(f"measurement metadata path: {result['output_metadata_path']}")
        else:
            result = preprocess(args.input_metadata, args.output, force=args.force)
            print(f"status: {result['status']}")
            print(f"output data file path: {result['output_data_path']}")
            print(f"preprocessing metadata path: {result['output_metadata_path']}")
            for code, count in result["warnings"].items():
                print(f"warning: {code}: {count} records", file=__import__("sys").stderr)
        return 0
    except (RuntimeError, PreprocessError, MeasureError) as exc:
        import sys

        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
