# FitFindr 🛍️

FitFindr is a multi-tool AI agent that helps you thrift. You describe what you
want in plain language ("vintage graphic tee under $30, size M"), and the agent
searches a mock listings dataset, suggests an outfit that styles the find against
your existing wardrobe, and writes a short, shareable "fit card" caption — while
handling the messy cases where a search comes back empty or your wardrobe is bare.

The agent runs a **planning loop** that decides which tool to call next based on
what the previous tool returned, and threads everything through a single `session`
dict so each tool builds on the last without re-asking you for anything.

---

## Setup

```bash
# from the project root
python -m venv .venv
source .venv/bin/activate          # Mac/Linux
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt
```

Create a `.env` file in the project root with your Groq key (same key as Project 1):

```
GROQ_API_KEY=your_key_here
```

`search_listings` works without a key; `suggest_outfit` and `create_fit_card` call
Groq's `llama-3.3-70b-versatile`, so they need it.

## Running it

```bash
python app.py          # launches the Gradio UI (http://localhost:7860)
python agent.py        # runs the happy-path + no-results demo in the terminal
pytest tests/          # runs the tool tests
```

---

## Tool Inventory

All three tools live in [`tools.py`](tools.py) and can be called and tested on their own.

### 1. `search_listings(description: str, size: str | None = None, max_price: float | None = None) -> list[dict]`

**Purpose:** Find secondhand listings that match the user's keywords, size, and price ceiling.
**Inputs:**

- `description` (str) — keywords, e.g. `"vintage graphic tee"`. Matched against each listing's title, description, style_tags, and category.
- `size` (str | None) — size to filter on, e.g. `"M"` or `"8"`. `None` skips the size filter.
- `max_price` (float | None) — inclusive price cap. `None` skips the price filter.

**Output:** a `list[dict]` of full listing dicts (`id`, `title`, `description`, `category`, `style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`), ranked by how many keywords they hit. Returns `[]` when nothing matches — never raises.

### 2. `suggest_outfit(new_item: dict, wardrobe: dict) -> str`

**Purpose:** Suggest a complete outfit that styles the found item using the user's own wardrobe (LLM-backed).
**Inputs:**

- `new_item` (dict) — the selected listing dict from `search_listings`.
- `wardrobe` (dict) — a dict with an `"items"` list (each item has `name`, `category`, `colors`, `style_tags`, `notes`). May be empty.

**Output:** a `str` — a few sentences of styling advice. With a wardrobe it names real pieces; with an empty wardrobe it gives general advice for the item on its own.

### 3. `create_fit_card(outfit: str, new_item: dict) -> str`

**Purpose:** Turn the outfit into a casual, shareable social-media caption (LLM-backed, higher temperature so it varies each run).
**Inputs:**

- `outfit` (str) — the styling text from `suggest_outfit`.
- `new_item` (dict) — the selected listing dict, for the item name / price / platform.

**Output:** a `str` — a 2–4 sentence caption. If `outfit` is empty/whitespace, returns a descriptive guidance message instead of calling the LLM.

---

## How the Planning Loop Works

The loop lives in `run_agent(query, wardrobe)` in [`agent.py`](agent.py). It's
sequential but **conditional** — each step only runs if the previous one produced
something usable, and the agent stops early rather than calling a tool with empty input.

1. **Parse (regex).** `parse_query()` pulls `description`, `size`, and `max_price`
   out of the raw query with regex — no extra LLM call. (Price: `under $30`, `$30`,
   `less than 40`, etc. Size: `size M`, `size 8`, or a standalone letter size, using
   word boundaries so "1500s"/"90s" don't get misread as size S.) Stored in
   `session["parsed"]`.
2. **Search.** Calls `search_listings(**parsed)` → `session["search_results"]`.
   - **If the list is empty →** set `session["error"]` to an actionable message and
     `return` immediately. `suggest_outfit` and `create_fit_card` are **not** called,
     so `outfit_suggestion` and `fit_card` stay `None`.
   - **If there are results →** continue.
3. **Select.** `session["selected_item"] = search_results[0]` (the top-ranked match).
4. **Suggest.** Calls `suggest_outfit(selected_item, wardrobe)` → `session["outfit_suggestion"]`.
5. **Fit card.** Calls `create_fit_card(outfit_suggestion, selected_item)` → `session["fit_card"]`.
6. **Return** the session.

Because step 2 branches, the agent's behavior genuinely changes with the input: an
impossible query (e.g. "designer ballgown size XXS under $5") stops at step 2 with
only an error set, while a matchable query runs all three tools.

---

## State Management

Everything for one interaction lives in a single `session` dict, created by
`_new_session()` in `agent.py`. It's the single source of truth — tools read what
they need from it instead of re-prompting the user.

| Key                 | Set by                      | Used by                                    |
| ------------------- | --------------------------- | ------------------------------------------ |
| `query`             | `_new_session`              | the parser                                 |
| `parsed`            | parse step                  | `search_listings(**parsed)`                |
| `search_results`    | `search_listings`           | the select step                            |
| `selected_item`     | select step (`results[0]`)  | `suggest_outfit` **and** `create_fit_card` |
| `wardrobe`          | `_new_session`              | `suggest_outfit`                           |
| `outfit_suggestion` | `suggest_outfit`            | `create_fit_card`                          |
| `fit_card`          | `create_fit_card`           | the UI                                     |
| `error`             | search step (on no results) | the UI (shown in panel 1)                  |

The exact dict that `search_listings` returns flows into `suggest_outfit` as
`new_item` and again into `create_fit_card` — no re-entry, no hardcoded values.
Verified: `session["selected_item"] is session["search_results"][0]` → `True`.
`app.py`'s `handle_query()` reads the finished session and maps `selected_item` /
`outfit_suggestion` / `fit_card` (or `error`) onto the three output panels.

---

## Error Handling (per tool)

Every tool owns its failure mode and degrades gracefully instead of crashing.

**`search_listings` — no matches.** Returns `[]`. The loop catches the empty list,
writes a helpful message into `session["error"]`, and stops before the LLM tools run.

> Triggered with `search_listings('designer ballgown', size='XXS', max_price=5)` → `[]`,
> and the full agent returned:
> _"No listings matched 'designer ballgown', size XXS, under $5. Try removing the size
> filter, raising your max price, or using broader keywords…"_ with `fit_card = None`.

**`suggest_outfit` — empty wardrobe.** Detects an empty `wardrobe["items"]` and
switches to a general-advice prompt; also wrapped in try/except for API errors.

> Triggered with `suggest_outfit(item, get_empty_wardrobe())` → returned a useful
> paragraph of general styling advice ("pairs well with high-waisted jeans or flowy
> skirts in neutral colors… layer a denim jacket…") — no crash, no empty string.

**`create_fit_card` — missing outfit.** Guards against an empty/whitespace `outfit`
and returns a message instead of calling the LLM; also wrapped in try/except.

> Triggered with `create_fit_card('', item)` → returned
> _"Can't write a fit card without an outfit suggestion — run suggest_outfit first…"_

There's also a relevance safeguard: a query that only matches generic style words
(like "vintage medieval armor", which shares only "vintage" with the dataset) returns
`[]` and hits the graceful no-results path instead of styling an unrelated item.

---

## Spec Reflection

**One way the spec helped:** Writing `planning.md` first — especially defining the
`session` dict keys and the explicit no-results branch — meant the planning loop was
basically transcription by the time I coded it. The "if results is empty, set error
and return early" rule was decided on paper, so I never had to untangle a half-built
loop that called `suggest_outfit` with empty input.

**One way the implementation diverged:** The spec described size matching loosely as
"case-insensitive (M matches S/M)" and didn't anticipate two problems that only showed
up in testing. (1) Naive matching treated the trailing "s" in "1500s"/"90s" as size S,
so I switched to whole-word, token-based matching. (2) Pure keyword overlap let a single
common word like "vintage" produce confident-but-irrelevant matches, so I added a rule
that a listing must match at least one _non-generic_ keyword. Both were testing-driven
refinements, not part of the original written spec.

---

## AI Usage

I used **Claude (Claude Code)**,
**1. Implementing the three tools (Milestone 3).** I gave Claude each tool's spec
block from `planning.md` plus `utils/data_loader.py`, and directed it to reuse
`load_listings()`, branch on the empty-wardrobe case in `suggest_outfit`, and guard
the empty-outfit case in `create_fit_card`. **What I changed/overrode:** I added a
token-based size matcher (so "S" doesn't match "One Size") and a `_fmt_price` helper
because the generated captions rendered prices as "$18.0"; I also raised
`create_fit_card`'s temperature to 0.9 after the first version produced near-identical
captions on repeated runs.

**2. Building the planning loop + parser (Milestone 4).** I gave Claude the
architecture diagram, the Planning Loop section, and the `session` dict definition,
and asked for `run_agent()` and the regex `parse_query()`. **What I changed/overrode:**
After testing "vintage medieval armor plate suit 1500s", I caught that the parser read
size "S" from "1500s" and that search returned irrelevant vintage items so I had it
switch to word-boundary size parsing and add the non-generic-keyword relevance rule.
These fixes came from running real edge-case queries, not from the first generated draft.

---

## Project Layout

```
.
├── tools.py            # the 3 tools + helpers + Groq client
├── agent.py            # parse_query() + run_agent() planning loop
├── app.py              # Gradio UI + handle_query()
├── planning.md         # the spec, written before the code
├── tests/test_tools.py # tool tests (7, offline — no LLM calls)
├── data/               # listings.json, wardrobe_schema.json
└── utils/data_loader.py
```

---

### 🎥 Demo Video Walkthorough

**[Watch the Demo Video Here](https://www.youtube.com/watch?v=oTmPDLHNdRM)**
