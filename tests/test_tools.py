"""
tests/test_tools.py

Unit tests for the three FitFindr tools, with at least one test per failure mode.

These tests deliberately avoid calling the LLM (Groq) so the suite runs fast and
works offline: we test search_listings (no LLM) thoroughly, and for create_fit_card
we test the empty-outfit guard, which returns before any LLM call.

Run from the project root:
    pytest tests/
"""

from tools import search_listings, create_fit_card


# ── search_listings: happy path ───────────────────────────────────────────────

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0
    # Every result is a full listing dict with the expected fields.
    assert all("title" in item and "price" in item for item in results)


def test_search_sorted_by_relevance():
    # The most on-topic listing for "graphic tee" should rank first.
    results = search_listings("graphic tee", size=None, max_price=None)
    assert len(results) > 0
    top = results[0]
    haystack = (top["title"] + " " + " ".join(top["style_tags"])).lower()
    assert "tee" in haystack or "graphic" in haystack


# ── search_listings: failure mode — no results ────────────────────────────────

def test_search_empty_results():
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []  # empty list, NOT an exception


# ── search_listings: price filter ─────────────────────────────────────────────

def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=10)
    assert all(item["price"] <= 10 for item in results)


# ── search_listings: size filter (token match, not substring) ─────────────────

def test_search_size_token_match():
    # "M" should match listings sized "M", "S/M", "M/L" — but never "One Size".
    results = search_listings("", size="M", max_price=None)
    for item in results:
        tokens = item["size"].lower().replace("/", " ").split()
        assert "m" in tokens
        assert item["size"].lower() != "one size"


# ── create_fit_card: failure mode — missing/empty outfit ──────────────────────

def test_create_fit_card_empty_outfit():
    item = {"title": "Faded Band Tee", "price": 22.0, "platform": "depop"}
    result = create_fit_card("", item)
    assert isinstance(result, str)
    assert result.strip() != ""
    # Returns a descriptive guidance message rather than raising.
    assert "suggest_outfit" in result


def test_create_fit_card_whitespace_outfit():
    item = {"title": "Faded Band Tee", "price": 22.0, "platform": "depop"}
    result = create_fit_card("   \n  ", item)
    assert isinstance(result, str)
    assert "suggest_outfit" in result
