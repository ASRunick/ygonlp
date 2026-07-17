"""ygonlp CLI。"""

from __future__ import annotations

import argparse
from pathlib import Path

from .collect import collect, dry_run_lines as collect_dry_run_lines
from .measure import MeasureError, dry_run_lines as measure_dry_run_lines, measure
from .preprocess import (
    PreprocessError,
    cleanup_preprocess,
    dry_run_lines as preprocess_dry_run_lines,
    preprocess,
    verify_preprocessed_cache,
)
from .summarize import SummarizeError, dry_run_lines as summarize_dry_run_lines, summarize
from .timeseries import TimeSeriesError, analyze_timeseries, dry_run_lines as timeseries_dry_run_lines


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
    summarize_parser = subparsers.add_parser("summarize", help="測定済みテキスト指標を集計・出力する")
    summarize_parser.add_argument("--input-metadata", type=Path, required=True, help="measurement metadata JSON")
    summarize_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    summarize_parser.add_argument("--dry-run", action="store_true", help="出力せずに入力・集計計画を検証")
    summarize_parser.add_argument("--force", action="store_true", help="有効な集計出力を無視して再生成")
    timeseries_parser = subparsers.add_parser(
        "analyze-timeseries",
        help="TCG初出候補年別のLength Metricsを集計する",
        description="TCG初出候補年別のLength Metricsを集計する",
    )
    timeseries_parser.add_argument("--input-metadata", type=Path, required=True, help="measurement metadata JSON")
    timeseries_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    timeseries_parser.add_argument("--dry-run", action="store_true", help="出力せずに時系列分析計画を検証")
    timeseries_parser.add_argument("--force", action="store_true", help="有効な時系列分析出力を無視して再生成")
    verify_preprocess_parser = subparsers.add_parser(
        "verify-preprocess",
        help="前処理cacheを全record単位で深く検証する",
    )
    verify_preprocess_parser.add_argument(
        "--input-metadata",
        type=Path,
        required=True,
        help="preprocessing metadata JSON",
    )
    cleanup_preprocess_parser = subparsers.add_parser(
        "cleanup-preprocess",
        help="未参照の前処理JSONL generationを検出・整理する",
    )
    cleanup_preprocess_parser.add_argument("--output", type=Path, required=True, help="前処理output directory")
    cleanup_preprocess_parser.add_argument("--delete", action="store_true", help="候補の未参照JSONLを削除する")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command not in {
        "collect",
        "preprocess",
        "verify-preprocess",
        "cleanup-preprocess",
        "measure",
        "summarize",
        "analyze-timeseries",
    }:
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
        elif args.command == "verify-preprocess":
            result = verify_preprocessed_cache(args.input_metadata)
            print(f"status: {result['status']}")
            print(f"metadata path: {result['metadata_path']}")
            print(f"data file path: {result['data_path']}")
            print(f"record count: {result['record_count']}")
            print(f"preprocessing cache key: {result['preprocessing_cache_key']}")
        elif args.command == "cleanup-preprocess":
            result = cleanup_preprocess(args.output, delete=args.delete)
            for path in result["deleted"] if args.delete else result["candidates"]:
                print(path)
        elif args.command == "analyze-timeseries" and args.dry_run:
            print("\n".join(timeseries_dry_run_lines(args.input_metadata, args.output, force=args.force)))
        elif args.command == "analyze-timeseries":
            result = analyze_timeseries(args.input_metadata, args.output, force=args.force)
            print(f"status: {result['status']}")
            print(f"timeseries metadata path: {result['output_metadata_path']}")
        elif args.dry_run:
            if args.command == "preprocess":
                print("\n".join(preprocess_dry_run_lines(args.input_metadata, args.output, force=args.force)))
            elif args.command == "measure":
                print("\n".join(measure_dry_run_lines(args.input_metadata, args.output, force=args.force)))
            else:
                print("\n".join(summarize_dry_run_lines(args.input_metadata, args.output, force=args.force)))
        elif args.command == "measure":
            result = measure(args.input_metadata, args.output, force=args.force)
            print(f"status: {result['status']}")
            print(f"output data file path: {result['output_data_path']}")
            print(f"measurement metadata path: {result['output_metadata_path']}")
        elif args.command == "summarize":
            result = summarize(args.input_metadata, args.output, force=args.force)
            print(f"status: {result['status']}")
            for name, path in result["output_paths"].items():
                print(f"{name} output file path: {path}")
            print(f"summary metadata path: {result['output_metadata_path']}")
        else:
            result = preprocess(args.input_metadata, args.output, force=args.force)
            print(f"status: {result['status']}")
            print(f"output data file path: {result['output_data_path']}")
            print(f"preprocessing metadata path: {result['output_metadata_path']}")
            for code, count in result["warnings"].items():
                print(f"warning: {code}: {count} records", file=__import__("sys").stderr)
        return 0
    except (RuntimeError, PreprocessError, MeasureError, SummarizeError, TimeSeriesError) as exc:
        import sys

        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
