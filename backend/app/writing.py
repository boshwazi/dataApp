"""How every AI text reads: one house style, added to every AI system prompt (llm.ask_json).

Based on plain-language and web-writing guidance (front-load the answer, one idea per line, scannable
structure, plain words; Nielsen Norman Group, Microsoft Writing Style Guide). People skim these texts between
tasks, so the first sentence carries the answer and the rest is there to check it.
"""

STYLE = (
    "\n\nWriting for people (applies to every text a person reads: replies, answers, summaries, insights, reasons, "
    "descriptions). Lead with the answer or outcome in one short sentence, under 20 words. Put details after it, "
    "one fact per line. Use the exact names of tables, columns, measures and visuals. Round numbers sensibly with "
    "separators (RM 46.4K, 1,152 rows, 48%) and give the comparison when there is one (vs the same period last "
    "year). Plain words; no jargon, no filler (no \"Great question\", \"I checked\", \"Let me\"), and don't repeat "
    "the question. Say plainly when something is unknown. Titles and labels stay short noun phrases. Markdown "
    "(\"- \" bullets, **bold**, `code` for formulas) only in a reply or answer field; every other field is plain "
    "sentences."
)

# Chat replies and Ask answers render Markdown: verdict first, then a short list.
REPLY = (
    " Shape every reply the same way: first a one-sentence verdict (what you found or what you'll do), then, "
    "if there are details, a short Markdown bullet list, one item per line, each under 15 words, with **bold** "
    "for names (\"- **Avg Kilometres per Trip**: still valid, 16.3\"). Keep it under 90 words unless asked for "
    "more. No headings or tables. For example: \"Everything still fits; nothing to fix.\" then bullets for what "
    "you checked."
)
