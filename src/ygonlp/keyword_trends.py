"""TCG初出候補年別のmechanic keyword出現傾向を集計する。"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

KEYWORD_TRENDS_IDENTIFIER = "mechanic_keyword_tcg_release_yearly_trends_v1"
DATE_DEFINITION = "tcg_date_adopted_tcg_set_source_first_release_candidate_date_v1"
CURRENT_DATE_CUTOFF_POLICY = "tcg_date_after_utc_current_date_excluded_v1"
OUTPUT_ORDER = "keyword_ascending_then_tcg_year_ascending_v1"


class KeywordTrendsError(RuntimeError):
    """keyword trend分析入力のエラー。"""


def _compile_keywords(keywords: Mapping[str, Sequence[str]]) -> dict[str, tuple[re.Pattern[str], ...]]:
    if not isinstance(keywords, Mapping):
        raise KeywordTrendsError("keywordsはkeyword名からregex pattern列へのmappingである必要があります")
    compiled: dict[str, tuple[re.Pattern[str], ...]] = {}
    for name, patterns in keywords.items():
        if not isinstance(name, str) or not name:
            raise KeywordTrendsError("keyword名は空でないstringである必要があります")
        if isinstance(patterns, str) or not isinstance(patterns, Sequence):
            raise KeywordTrendsError("keyword patternはstringの列である必要があります")
        try:
            compiled[name] = tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)
        except (TypeError, re.error) as exc:
            raise KeywordTrendsError(f"keyword {name}のregex patternが不正です") from exc
    return compiled


def build_keyword_trends(
    records: Iterable[dict[str, Any]], keywords: Mapping[str, Sequence[str]], cutoff: date,
) -> dict[str, Any]:
    """released recordのkeyword出現数と出現カード比率をTCG年別に集計する。"""
    compiled = _compile_keywords(keywords)
    yearly_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    missing = future = 0
    for record in records:
        value = record["tcg_date"]
        if value is None:
            missing += 1
            continue
        if value > cutoff.isoformat():
            future += 1
            continue
        yearly_records[value[:4]].append(record)

    trends: list[dict[str, Any]] = []
    for name in sorted(compiled):
        for year in sorted(yearly_records):
            occurrence_count = document_count = 0
            for record in yearly_records[year]:
                text = record["text_normalized"]
                matches = sum(len(pattern.findall(text)) for pattern in compiled[name])
                occurrence_count += matches
                document_count += matches > 0
            trends.append({
                "keyword": name,
                "year": year,
                "occurrence_count": occurrence_count,
                "document_count": document_count,
                "document_ratio": document_count / len(yearly_records[year]),
            })
    return {
        "analysis_identifier": KEYWORD_TRENDS_IDENTIFIER,
        "date_definition": DATE_DEFINITION,
        "current_date_cutoff": cutoff.isoformat(),
        "current_date_cutoff_policy": CURRENT_DATE_CUTOFF_POLICY,
        "missing_date_count": missing,
        "future_date_count": future,
        "included_record_count": sum(len(group) for group in yearly_records.values()),
        "output_ordering_identifier": OUTPUT_ORDER,
        "trends": trends,
    }
