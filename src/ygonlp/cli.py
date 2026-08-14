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
from .release_counts import ReleaseCountsError, analyze_release_counts, dry_run_lines as release_counts_dry_run_lines
from .similarity import SimilarityError, search_similar
from .vocabulary import VocabularyError, analyze_topics, analyze_vocabulary
from .prices import PriceSnapshotError, snapshot_prices
from .price_analysis import PriceAnalysisError, analyze_prices
from .archetypes import ArchetypeError, analyze_archetypes, dry_run_lines as archetype_dry_run_lines
from .archetype_similarity import ArchetypeSimilarityError, analyze_archetype_similarity, dry_run_lines as archetype_similarity_dry_run_lines


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
    release_counts_parser = subparsers.add_parser(
        "analyze-releases",
        help="TCG初出候補年別のカードrelease countを集計する",
        description="TCG初出候補年別のカードrelease countを集計する",
    )
    release_counts_parser.add_argument("--input-metadata", type=Path, required=True, help="measurement metadata JSON")
    release_counts_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    release_counts_parser.add_argument("--dry-run", action="store_true", help="出力せずにrelease count分析計画を検証")
    release_counts_parser.add_argument("--force", action="store_true", help="有効なrelease count分析出力を無視して再生成")
    archetypes_parser = subparsers.add_parser("analyze-archetypes", help="archetype別カードテキストprofileを集計する")
    archetypes_parser.add_argument("--input-metadata", type=Path, required=True, help="preprocessing metadata JSON")
    archetypes_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    archetypes_parser.add_argument("--dry-run", action="store_true", help="出力せずにarchetype分析計画を検証")
    archetypes_parser.add_argument("--force", action="store_true", help="有効なarchetype分析出力を無視して再生成")
    archetype_similarity_parser = subparsers.add_parser("analyze-archetype-similarity", help="archetype内の効果テキスト類似ペアを分析する")
    archetype_similarity_parser.add_argument("--input-metadata", type=Path, required=True, help="preprocessing metadata JSON")
    archetype_similarity_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    archetype_similarity_parser.add_argument("--top-n", type=int, default=10, help="archetypeごとに返す正の類似ペア数（既定: 10）")
    archetype_similarity_parser.add_argument("--dry-run", action="store_true", help="出力せずにarchetype類似性分析計画を検証")
    archetype_similarity_parser.add_argument("--force", action="store_true", help="有効なarchetype類似性分析出力を無視して再生成")
    similar_parser = subparsers.add_parser("search-similar", help="正規化済み効果テキストを語彙的に検索する")
    similar_parser.add_argument("--input-metadata", type=Path, required=True, help="preprocessing metadata JSON")
    query_group = similar_parser.add_mutually_exclusive_group(required=True)
    query_group.add_argument("--card-id", type=int, help="完全一致する card_id")
    query_group.add_argument("--name", help="完全一致するカード名")
    similar_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    similar_parser.add_argument("--top-n", type=int, default=10, help="返す正の件数（既定: 10）")
    similar_parser.add_argument("--card-type", help="完全一致する card_type で候補を絞る")
    similar_parser.add_argument("--release-status", choices=("released", "missing_date", "future_dated"), help="TCG発売状態で候補を絞る")
    similar_parser.add_argument("--force", action="store_true", help="有効な類似検索出力を無視して再生成")
    vocabulary_parser = subparsers.add_parser("analyze-vocabulary", help="正規化済み効果テキストの語彙・n-gram頻度を分析する")
    vocabulary_parser.add_argument("--input-metadata", type=Path, required=True, help="preprocessing metadata JSON")
    vocabulary_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    vocabulary_parser.add_argument("--ngram", type=int, choices=(1, 2), default=1, help="1=unigram、2=bigram")
    vocabulary_parser.add_argument("--min-df", type=int, default=1, help="正の最小document frequency")
    vocabulary_parser.add_argument("--english-stopwords", action="store_true", help="組み込み英語stopwordを除外")
    vocabulary_parser.add_argument("--force", action="store_true", help="有効な語彙分析出力を無視して再生成")
    topics_parser = subparsers.add_parser("analyze-topics", help="正規化済み効果テキストの探索的LDAトピックを分析する")
    topics_parser.add_argument("--input-metadata", type=Path, required=True, help="preprocessing metadata JSON")
    topics_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    topics_parser.add_argument("--topic-count", type=int, default=5, help="正のtopic数")
    topics_parser.add_argument("--top-terms", type=int, default=10, help="topicごとの正の上位term数")
    topics_parser.add_argument("--representative-cards", type=int, default=10, help="topicごとの正の代表card数")
    topics_parser.add_argument("--random-seed", type=int, default=0, help="LDAの固定random seed")
    topics_parser.add_argument("--max-iter", type=int, default=10, help="LDAの正の最大iteration数")
    topics_parser.add_argument("--min-df", type=int, default=1, help="正の最小document frequency")
    topics_parser.add_argument("--english-stopwords", action="store_true", help="組み込み英語stopwordを除外")
    topics_parser.add_argument("--force", action="store_true", help="有効なtopic分析出力を無視して再生成")
    prices_parser = subparsers.add_parser("snapshot-prices", help="収集済みraw dataからvendor価格snapshotを生成する")
    prices_parser.add_argument("--input-metadata", type=Path, required=True, help="collection metadata JSON")
    prices_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    prices_parser.add_argument("--force", action="store_true", help="有効な価格snapshotを無視して再生成")
    price_analysis_parser = subparsers.add_parser("analyze-prices", help="価格snapshotとテキスト指標を結合分析する")
    price_analysis_parser.add_argument("--price-metadata", type=Path, required=True, help="price snapshot metadata JSON")
    price_analysis_parser.add_argument("--measurement-metadata", type=Path, required=True, help="measurement metadata JSON")
    price_analysis_parser.add_argument("--output", type=Path, required=True, help="保存先ディレクトリ")
    price_analysis_parser.add_argument("--character-buckets", default="100,200,300,500", help="comma区切りの正の昇順character count境界")
    price_analysis_parser.add_argument("--include-zero", action="store_true", help="zero priceを統計・相関へ含める")
    price_analysis_parser.add_argument("--force", action="store_true", help="有効な価格分析出力を無視して再生成")
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
        "analyze-releases",
        "analyze-archetypes",
        "analyze-archetype-similarity",
        "search-similar",
        "analyze-vocabulary",
        "analyze-topics",
        "snapshot-prices",
        "analyze-prices",
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
        elif args.command == "analyze-releases" and args.dry_run:
            print("\n".join(release_counts_dry_run_lines(args.input_metadata, args.output, force=args.force)))
        elif args.command == "analyze-releases":
            result = analyze_release_counts(args.input_metadata, args.output, force=args.force)
            print(f"status: {result['status']}")
            print(f"release counts metadata path: {result['output_metadata_path']}")
        elif args.command == "analyze-archetypes" and args.dry_run:
            print("\n".join(archetype_dry_run_lines(args.input_metadata, args.output, force=args.force)))
        elif args.command == "analyze-archetypes":
            result = analyze_archetypes(args.input_metadata, args.output, force=args.force)
            print(f"status: {result['status']}")
            print(f"archetype metadata path: {result['output_metadata_path']}")
        elif args.command == "analyze-archetype-similarity" and args.dry_run:
            print("\n".join(archetype_similarity_dry_run_lines(args.input_metadata, args.output, top_n=args.top_n, force=args.force)))
        elif args.command == "analyze-archetype-similarity":
            result = analyze_archetype_similarity(args.input_metadata, args.output, top_n=args.top_n, force=args.force)
            print(f"status: {result['status']}")
            for name, path in result["output_paths"].items():
                print(f"{name} output file path: {path}")
            print(f"archetype similarity metadata path: {result['output_metadata_path']}")
        elif args.command == "search-similar":
            result = search_similar(
                args.input_metadata, args.output, card_id=args.card_id, name=args.name, top_n=args.top_n,
                card_type=args.card_type, release_status=args.release_status, force=args.force,
            )
            print(f"status: {result['status']}")
            for name, path in result["output_paths"].items():
                print(f"{name} output file path: {path}")
            print(f"similarity metadata path: {result['output_metadata_path']}")
        elif args.command == "analyze-vocabulary":
            result = analyze_vocabulary(args.input_metadata, args.output, ngram=args.ngram, min_df=args.min_df,
                                        english_stopwords=args.english_stopwords, force=args.force)
            print(f"status: {result['status']}")
            print(f"vocabulary metadata path: {result['output_metadata_path']}")
        elif args.command == "analyze-topics":
            result = analyze_topics(args.input_metadata, args.output, topic_count=args.topic_count,
                                    top_terms=args.top_terms, representative_cards=args.representative_cards,
                                    random_seed=args.random_seed, max_iter=args.max_iter, min_df=args.min_df,
                                    english_stopwords=args.english_stopwords, force=args.force)
            print(f"status: {result['status']}")
            print(f"topics metadata path: {result['output_metadata_path']}")
        elif args.command == "snapshot-prices":
            result = snapshot_prices(args.input_metadata, args.output, force=args.force)
            print(f"status: {result['status']}")
            print(f"price snapshot data path: {result['output_data_path']}")
            print(f"price snapshot metadata path: {result['output_metadata_path']}")
        elif args.command == "analyze-prices":
            result = analyze_prices(args.price_metadata, args.measurement_metadata, args.output,
                                    character_buckets=args.character_buckets, include_zero=args.include_zero, force=args.force)
            print(f"status: {result['status']}")
            for name, path in result["output_paths"].items(): print(f"{name} output file path: {path}")
            print(f"price analysis metadata path: {result['output_metadata_path']}")
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
    except (RuntimeError, PreprocessError, MeasureError, SummarizeError, TimeSeriesError, ReleaseCountsError, ArchetypeError, ArchetypeSimilarityError, SimilarityError, VocabularyError, PriceSnapshotError, PriceAnalysisError) as exc:
        import sys

        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
