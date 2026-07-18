from datetime import date

from ygonlp.keyword_trends import build_keyword_trends


KEYWORDS = {
    "graveyard": [r"\bgraveyard\b"],
    "gy": [r"\bGY\b"],
    "banish": [r"\bbanish(?:ed|ing)?\b"],
    "search": [r"\badd\b", r"\bsearch\b"],
}
CUTOFF = date(2022, 12, 31)


def record(card_id, tcg_date, text_normalized):
    return {"card_id": card_id, "tcg_date": tcg_date, "text_normalized": text_normalized}


def trend(result, keyword, year):
    return next(row for row in result["trends"] if row["keyword"] == keyword and row["year"] == year)


def test_keyword_trends_count_regex_matches_case_insensitively_and_per_document():
    result = build_keyword_trends([
        record(1, "2020-01-01", "Banish a card; BANISHED cards stay banished. Search your GY."),
        record(2, "2020-03-01", "Add 1 card from your Graveyard."),
    ], KEYWORDS, CUTOFF)

    assert trend(result, "banish", "2020") == {
        "keyword": "banish", "year": "2020", "occurrence_count": 3,
        "document_count": 1, "document_ratio": 0.5,
    }
    assert trend(result, "gy", "2020")["document_count"] == 1
    assert trend(result, "graveyard", "2020")["occurrence_count"] == 1
    assert trend(result, "search", "2020") == {
        "keyword": "search", "year": "2020", "occurrence_count": 2,
        "document_count": 2, "document_ratio": 1.0,
    }


def test_keyword_trends_group_by_year_and_exclude_missing_and_future_dates():
    result = build_keyword_trends([
        record(1, "2021-01-01", "banish"),
        record(2, "2020-01-01", "banish banish"),
        record(3, None, "banish"),
        record(4, "9999-01-01", "banish"),
    ], {"banish": [r"\bbanish\b"]}, CUTOFF)

    assert result["missing_date_count"] == 1
    assert result["future_date_count"] == 1
    assert result["included_record_count"] == 2
    assert [(row["year"], row["occurrence_count"], row["document_count"], row["document_ratio"])
            for row in result["trends"]] == [
        ("2020", 2, 1, 1.0), ("2021", 1, 1, 1.0),
    ]


def test_keyword_trends_are_deterministically_ordered_and_zero_fill_keywords():
    records = [
        record(2, "2021-01-01", "alpha"),
        record(1, "2020-01-01", "beta"),
    ]
    keywords = {"zeta": [r"zeta"], "alpha": [r"alpha"], "beta": [r"beta"]}

    first = build_keyword_trends(records, keywords, CUTOFF)
    second = build_keyword_trends(reversed(records), dict(reversed(list(keywords.items()))), CUTOFF)

    assert first["trends"] == second["trends"]
    assert [(row["keyword"], row["year"]) for row in first["trends"]] == [
        ("alpha", "2020"), ("alpha", "2021"), ("beta", "2020"),
        ("beta", "2021"), ("zeta", "2020"), ("zeta", "2021"),
    ]
    assert trend(first, "zeta", "2020")["occurrence_count"] == 0
    assert trend(first, "zeta", "2020")["document_ratio"] == 0.0
