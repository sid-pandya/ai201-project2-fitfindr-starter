"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

# Model used for the two LLM-backed tools (suggest_outfit, create_fit_card).
GROQ_MODEL = "llama-3.3-70b-versatile"


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


# ── helpers ─────────────────────────────────────────────────────────────────--

# Words ignored when scoring keyword overlap so common filler doesn't inflate scores.
_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "or", "of", "in", "on", "to",
    "i", "im", "looking", "want", "need", "some", "my", "that", "this",
    "under", "less", "than", "below", "size",
}

# Generic style adjectives that show up across many listings. A listing that
# matches ONLY on these (and nothing specific like "tee" or "boots") isn't a real
# hit — e.g. "vintage medieval armor" shouldn't return a vintage baby tee.
_GENERIC_TERMS = {
    "vintage", "y2k", "retro", "classic", "cute", "aesthetic", "trendy",
    "cool", "nice", "style", "stylish", "fashion", "old", "new",
}


def _tokenize(text: str) -> list[str]:
    """Lowercase a string and split it into alphanumeric word tokens."""
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _size_tokens(size: str) -> set[str]:
    """
    Split a listing's size string into comparable tokens.

    "S/M" -> {"s", "m"}, "W30 L30" -> {"w30", "l30"}, "US 8" -> {"us", "8"},
    "One Size" -> {"one", "size"}. Used for token-based (not substring) size
    matching so "S" does not wrongly match "One Size".
    """
    return set(re.findall(r"[a-z0-9.]+", (size or "").lower()))


def _matches_size(query_size: str, listing_size: str) -> bool:
    """True if the requested size token appears among the listing's size tokens."""
    return query_size.strip().lower() in _size_tokens(listing_size)


def _fmt_price(price) -> str:
    """Format a price without a trailing .0 (18.0 -> '18', 8.5 -> '8.5')."""
    try:
        return f"{float(price):g}"
    except (TypeError, ValueError):
        return str(price)


def _format_item(item: dict) -> str:
    """Format a listing dict into a compact one-line description for prompts."""
    return (
        f"{item.get('title', 'item')} "
        f"(category: {item.get('category', 'n/a')}, "
        f"colors: {', '.join(item.get('colors', [])) or 'n/a'}, "
        f"style: {', '.join(item.get('style_tags', [])) or 'n/a'}, "
        f"${_fmt_price(item.get('price', '?'))} on {item.get('platform', 'a resale app')})"
    )


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform

    TODO:
        1. Load all listings with load_listings().
        2. Filter by max_price and size (if provided).
        3. Score each remaining listing by keyword overlap with `description`.
        4. Drop any listings with a score of 0 (no relevant matches).
        5. Sort by score, highest first, and return the listing dicts.

    Before writing code, fill in the Tool 1 section of planning.md.
    """
    listings = load_listings()
    query_tokens = [t for t in _tokenize(description) if t not in _STOPWORDS]

    scored = []
    for item in listings:
        # 1. Hard filters: price ceiling and size (when provided).
        if max_price is not None and item.get("price", 0) > max_price:
            continue
        if size is not None and not _matches_size(size, item.get("size", "")):
            continue

        # 2. Relevance score: count query tokens that appear in the listing's
        #    searchable text (title + description + style_tags + category).
        haystack = " ".join([
            item.get("title", ""),
            item.get("description", ""),
            " ".join(item.get("style_tags", [])),
            item.get("category", ""),
        ])
        haystack_tokens = set(_tokenize(haystack))
        matched = [t for t in query_tokens if t in haystack_tokens]
        score = len(matched)

        # 3. Decide whether this is a real hit.
        #    - No keywords given (blank description): keep everything that passed
        #      the hard filters so size/price-only searches still return results.
        #    - Otherwise require at least one SPECIFIC match — matching only on
        #      generic style words like "vintage" doesn't count.
        if query_tokens:
            specific = [t for t in matched if t not in _GENERIC_TERMS]
            if not specific:
                continue

        scored.append((score, item))

    # 4. Sort by score (highest first); ties keep dataset order (stable sort).
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.

    TODO:
        1. Check whether wardrobe['items'] is empty.
        2. If empty: call the LLM with a prompt for general styling ideas
           (what kinds of items pair well, what vibe it suits, etc.).
        3. If not empty: format the wardrobe items into a prompt and ask
           the LLM to suggest specific outfit combinations using the new item
           and named pieces from the wardrobe.
        4. Return the LLM's response as a string.

    Before writing code, fill in the Tool 2 section of planning.md.
    """
    item_desc = _format_item(new_item)
    items = (wardrobe or {}).get("items", [])

    if items:
        # Wardrobe has pieces: ask for an outfit that names real wardrobe items.
        wardrobe_lines = "\n".join(
            f"- {it.get('name', 'item')} "
            f"({it.get('category', 'n/a')}; {', '.join(it.get('colors', [])) or 'n/a'})"
            for it in items
        )
        prompt = (
            "You are a thoughtful personal stylist. The user is considering buying "
            f"this secondhand piece:\n  {item_desc}\n\n"
            "Their current wardrobe:\n"
            f"{wardrobe_lines}\n\n"
            "Suggest one complete outfit that styles the new piece using SPECIFIC "
            "items named from their wardrobe. Keep it to about 3 sentences, reference "
            "the wardrobe pieces by name, and end with one concrete styling tip "
            "(how to tuck, cuff, layer, etc.)."
        )
    else:
        # Empty-wardrobe branch: no pieces to name, so give general advice.
        prompt = (
            "You are a thoughtful personal stylist. The user is considering buying "
            f"this secondhand piece:\n  {item_desc}\n\n"
            "They haven't entered a wardrobe yet, so give GENERAL styling advice: in "
            "about 3 sentences, say what kinds of pieces (categories/colors) pair well "
            "with it, what vibe or occasion it suits, and one concrete styling tip."
        )

    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=300,
        )
        return response.choices[0].message.content.strip()
    except Exception:  # network / auth / rate-limit — degrade gracefully
        tags = ", ".join(new_item.get("style_tags", [])) or "its style"
        return (
            "(Styling model is unavailable right now — here's a quick idea instead.) "
            f"Lean into {tags}: balance the silhouette (pair fitted with loose), echo "
            "one of its colors elsewhere in the outfit, and keep accessories simple."
        )


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.

    The caption should:
    - Feel casual and authentic (like a real OOTD post, not a product description)
    - Mention the item name, price, and platform naturally (once each)
    - Capture the outfit vibe in specific terms
    - Sound different each time for different inputs (use higher LLM temperature)

    TODO:
        1. Guard against an empty or whitespace-only outfit string.
        2. Build a prompt that gives the LLM the item details and the outfit,
           and asks for a caption matching the style guidelines above.
        3. Call the LLM and return the response.

    Before writing code, fill in the Tool 3 section of planning.md.
    """
    # 1. Guard: no outfit means there's nothing to caption.
    if not outfit or not outfit.strip():
        return (
            "Can't write a fit card without an outfit suggestion — "
            "run suggest_outfit first, then try again."
        )

    title = new_item.get("title", "this piece")
    price = _fmt_price(new_item.get("price", "?"))
    platform = new_item.get("platform", "a resale app")

    prompt = (
        "Write a short, casual social-media caption for an outfit-of-the-day post "
        "about a secondhand find. It should sound like a real person, not a product "
        "description.\n\n"
        f"Item: {title}\n"
        f"Price: ${price}\n"
        f"Platform: {platform}\n"
        f"Outfit: {outfit}\n\n"
        "Rules: 2-4 sentences, mention the item, price, and platform naturally (once "
        "each), capture the outfit's vibe in specific terms, and keep it lowercase and "
        "relaxed with at most one or two emojis. Return only the caption."
    )

    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,  # higher temp so captions vary across runs
            max_tokens=160,
        )
        return response.choices[0].message.content.strip()
    except Exception:  # network / auth / rate-limit — degrade gracefully
        return (
            f"thrifted this {title.lower()} for ${price} on {platform} and i'm obsessed "
            "🫶 styled it up and it's officially in the rotation."
        )
