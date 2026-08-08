# portal_scan.py — Read-Only Portal Screen Scanner (Layer 1)
# Reads the CURRENTLY VISIBLE screen using vision.py (no login, no credentials).
# Parses visible deadline/assignment text and PROPOSES obligations for confirmation.
# The user confirms via voice — nothing is auto-added silently.
#
# SAFETY: This is OCR/vision reading of what's already on screen.
# No authentication, no credential storage, no portal scraping.

import re
import datetime
from vision import describe_screen

# ── Heuristic patterns for deadline/assignment detection ─────────────────────
# These match common Coursera / college portal / LMS text patterns.
# All matching is done on plain text returned by vision.describe_screen().

_DEADLINE_PATTERNS = [
    # "due Monday, July 7" / "due July 7, 2025" / "due 7 July"
    re.compile(
        r"due\s+(?:on\s+)?(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+)?"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}"
        r"(?:,?\s+\d{4})?",
        re.IGNORECASE,
    ),
    # "due 7 July" / "due 07/07/2025"
    re.compile(
        r"due\s+(?:on\s+)?\d{1,2}[\s/\-]"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:[\s/\-]\d{2,4})?",
        re.IGNORECASE,
    ),
    # "deadline: July 7" / "deadline — 7 July 2025"
    re.compile(
        r"deadline\s*[:\-–]\s*"
        r"(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+)?"
        r"(?:\d{1,2}\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*\d{0,2}(?:,?\s*\d{4})?",
        re.IGNORECASE,
    ),
    # "submit by July 7" / "submit by 11:59 PM"
    re.compile(
        r"submit\s+by\s+[\w\s,]+?\d{1,2}(?::\d{2})?\s*(?:am|pm)?",
        re.IGNORECASE,
    ),
    # "Quiz · Due Jul 7, 11:59 PM" (Coursera format)
    re.compile(
        r"(?:Quiz|Assignment|Exam|Worksheet|Graded|Peer\s+Review|Programming\s+Assignment)"
        r"\s*[·|—\-]?\s*Due\s+[\w\s,]+?\d{1,2}(?::\d{2})?\s*(?:am|pm)?",
        re.IGNORECASE,
    ),
]

# Labels that indicate what TYPE of obligation it is
_TYPE_KEYWORDS = {
    "exam":       ("exam", "test", "midterm", "final"),
    "assignment": ("assignment", "homework", "project", "programming assignment", "graded"),
    "worksheet":  ("worksheet", "exercise", "practice"),
    "coursera":   ("quiz", "peer review", "coursera", "video lecture", "week"),
}


def _infer_type(text: str) -> str:
    lowered = text.lower()
    for ob_type, keywords in _TYPE_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return ob_type
    return "other"


def _extract_title_near_match(full_text: str, match_start: int, match_end: int) -> str:
    """
    Try to extract a meaningful title from the text surrounding a deadline match.
    Looks at the 120 characters BEFORE the match for a descriptive phrase.
    Falls back to the matched text itself trimmed to 60 chars.
    """
    window_start = max(0, match_start - 120)
    before       = full_text[window_start:match_start].strip()

    # Split on common separators and take the last non-empty chunk
    chunks = re.split(r"[\n\r|·—\-]+", before)
    chunks = [c.strip() for c in chunks if c.strip()]

    if chunks:
        candidate = chunks[-1]
        # Trim to something reasonable
        candidate = candidate[:70].strip()
        if len(candidate) >= 5:
            return candidate

    # Fallback: use the matched text itself
    matched = full_text[match_start:match_end].strip()
    return matched[:60]


def scan_portal_screen() -> dict:
    """
    Capture current screen, describe it via vision.py, parse for deadlines.

    Returns:
        {
            "proposed": [
                {
                    "title":    str,
                    "type":     str,   # one of VALID_TYPES
                    "due_date": str,   # raw string as seen on screen
                    "raw_text": str,   # the matched snippet
                },
                ...
            ],
            "screen_summary": str,   # first 300 chars of vision output, for context
            "error":          str,   # non-empty only if something failed
        }
    """
    print("[portal_scan] Capturing screen for portal scan...")

    try:
        screen_text = describe_screen(
            "You are reading an academic portal or LMS (like Coursera or a college portal). "
            "Please describe all visible deadlines, assignments, quizzes, exams, due dates, "
            "and submission dates shown on the screen. Include exact dates and assignment names."
        )
    except Exception as e:
        print(f"[portal_scan] vision call failed: {e}")
        return {"proposed": [], "screen_summary": "", "error": f"Screen capture failed: {e}"}

    if not screen_text or not screen_text.strip():
        return {"proposed": [], "screen_summary": "", "error": "Screen appears empty or unreadable."}

    screen_summary = screen_text[:300].strip()
    proposed       = []
    seen_titles    = set()  # deduplicate

    for pattern in _DEADLINE_PATTERNS:
        for match in pattern.finditer(screen_text):
            raw_match = match.group(0).strip()
            title     = _extract_title_near_match(screen_text, match.start(), match.end())
            ob_type   = _infer_type(screen_text[max(0, match.start()-120):match.end()])

            # Skip duplicates (same title)
            title_key = title.lower().strip()
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)

            proposed.append({
                "title":    title,
                "type":     ob_type,
                "due_date": raw_match,   # raw; executor will pass this to add_obligation
                "raw_text": raw_match,
            })

    print(f"[portal_scan] Found {len(proposed)} proposed obligation(s).")
    return {
        "proposed":       proposed,
        "screen_summary": screen_summary,
        "error":          "",
    }


def format_scan_proposal(scan_result: dict) -> str:
    """
    Format scan result into a spoken confirmation prompt for the user.
    e.g. "I can see 2 deadlines on screen. 1. ANN quiz due Jul 7. 2. Worksheet due Jul 9.
          Want me to add them?"
    """
    proposed = scan_result.get("proposed", [])
    error    = scan_result.get("error", "")

    if error:
        return f"I had trouble reading the screen. {error}"

    if not proposed:
        return "I couldn't spot any deadlines or assignments on the current screen."

    count = len(proposed)
    noun  = "deadline" if count == 1 else "deadlines"
    lines = [f"{i+1}. {p['title']} — {p['due_date']}" for i, p in enumerate(proposed)]
    items = ". ".join(lines)
    return f"I can see {count} {noun} on screen. {items}. Want me to add them?"