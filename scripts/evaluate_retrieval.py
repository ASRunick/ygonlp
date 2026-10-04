"""Run research retrieval comparisons exclusively from verified local caches."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from ygonlp.artifacts import write_bytes_atomic
from ygonlp.retrieval_evaluation import evaluate_retrieval, load_cached_corpus, load_cached_queries, validate_cases
from ygonlp.semantic import _digest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preprocessing-metadata", type=Path, required=True)
    parser.add_argument("--embedding-metadata", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--query-cache", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--rrf-constant", type=int, default=60)
    args = parser.parse_args(argv)
    try:
        cases_raw = args.cases.read_bytes()
        cases = json.loads(cases_raw)
        validate_cases(cases)
        cards, vectors, provenance = load_cached_corpus(args.preprocessing_metadata, args.embedding_metadata)
        queries, query_provenance = load_cached_queries(cases, args.query_cache)
        result = evaluate_retrieval(cards, vectors, cases, queries, k=args.k, rrf_constant=args.rrf_constant)
        root = Path(__file__).resolve().parents[1]
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True).stdout)
        result["provenance"] = {**provenance, "query_embeddings": query_provenance,
                                "cases_sha256": _digest(cases_raw), "producer_revision": revision,
                                "producer_dirty": dirty,
                                "producer_source_sha256": _digest(Path(__file__).read_bytes()),
                                "evaluation_source_sha256": _digest((root / "src/ygonlp/retrieval_evaluation.py").read_bytes()),
                                "invocation": list(argv if argv is not None else sys.argv[1:])}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(args.output, (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, re.error, subprocess.CalledProcessError) as exc:
        print(f"offline evaluation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result["groups"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
