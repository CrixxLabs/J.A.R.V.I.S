# seed_me.py — Run this once to seed your profile manually
# Usage: python seed_me.py

from user_profile import seed_profile_from_text, confirm_and_save_profile

MY_CONTEXT = """
USER PROFILE SEED — ARJUN ("ARJU")
Source: combined manual seed (ChatGPT-sourced + Claude-observed)
Confidence: manual entries below are user-confirmed; mark accordingly in user_profile.json

=== IDENTITY ===
Name: Arjun, goes by Arju
Student — BCA 4th semester, Yenepoya Institute (Yenepoya University), Bengaluru
Specialization: Computer Science, AI/ML
Current courses: AI/ML, ADA, ANN, Data Mining, Robotics, LMS
Age: 20
Date-Of-Birth: 10/06/2006
Place-Of-Brth: Kerala, India
Mother tongue: Malayalam
Building Jarvis on: Acer laptop, RTX 3050 6GB VRAM, i5 12th Gen H, 16GB DDR4 RAM

=== CORE TRAITS (confirmed via direct interaction pattern) ===

Builder mindset — prefers creating over consuming. Default question is
"how can this be improved" not "how does this work."

Action-oriented — wants working solutions over long explanations. Dislikes
filler, corporate tone, unnecessary formality.

Phase/checklist thinker — naturally organizes work into structured phases
and todo lists. Repeatedly requests visual/structured breakdowns rather
than prose when planning multi-step work.

Heavy shorthand communicator — types in compressed, informal style
(abbreviations, dropped vowels, casual grammar). This is a default style,
not just "casual tone" — should inform how Jarvis parses input, not just
how it responds.

Delegates execution deliberately — uses AI-to-AI handoff as primary build
method: defines architecture/rules with one AI, hands implementation to
another, enforces strict non-destructive editing rules across both.
This is an intentional workflow, not laziness — values control over
process even while delegating execution.

Compounding-systems preference — doesn't just like hard problems, prefers
systems that build on each other in phases rather than one-off solutions.
Gets restless mid-phase and wants to plan ahead, but circles back to
finish foundations when reminded — tendency to plan Phase 4 before Phase 2
is fully wired, self-corrects when this gets flagged.

Persistent under frustration — tolerates repeated failure/debugging far
longer than average when the goal matters (evidenced by iterative
multi-month Jarvis development through repeated bug cycles).

Independent/unconventional approach — questions default solutions,
evaluates tooling personally rather than accepting standard recommendations
(e.g. choosing to build a fully custom modular assistant rather than using
off-the-shelf assistant frameworks).

=== STRENGTHS ===
- High determination, doesn't abandon technical problems quickly
- Fast learner in tech/AI/software domains specifically
- Resourceful — builds with constrained hardware (6GB VRAM) rather than
  assuming bigger is required
- Adaptable across software/hardware/AI tooling
- Strong curiosity — consistently asks "what else can this do"

=== GROWTH AREAS ===
- Impatience with inefficiency or repeated failure
- Risk of overcommitting — wants multiple features built simultaneously
- Optimization-before-stabilization tendency — plans advanced features
  before current phase is fully tested (self-aware of this when flagged)
- High expectations for pace of progress

=== COMMUNICATION PREFERENCES ===
- Casual, direct, no corporate tone
- Prefers structured/visual breakdowns (lists, phases, checklists) over
  prose explanations
- Short sentences, rapid topic switches, frequent follow-ups
- Wants concrete next steps, not theory

=== AI ASSISTANT DESIGN PHILOSOPHY (Arju's stated goals for Jarvis) ===
Jarvis should be: practical, modular, expandable without breaking existing
code, reliable, context-aware, voice-driven, able to learn from real usage
data over time. Explicitly wants Jarvis to move from reactive (responds to
commands) to proactive (tracks obligations, reasons independently, speaks
up unprompted) — this is the active development direction as of the
"Daddy's Home Jarvis" build phase.

=== UNVERIFIED / EXTERNAL SOURCE (ChatGPT-reported, not independently
confirmed by Claude — mark as lower confidence in user_profile.json) ===
- Location: Bahrain
- Motorcycle interest: Bajaj Dominar discussed; brother owns a modified
  Royal Enfield Himalayan scrambler
- Financial behavior: value-focused, weighs cost vs usefulness/resale
  before purchases
- Vehicle interest also includes KTM Duke 390 and BMW S1000RR (referenced
  in other context)

=== NOTES FOR JARVIS'S OWN OBSERVATION (Part B behavioral layer) ===
This seed is a starting point only. Jarvis should weight its own
observed data (from observer.py, memory.py usage patterns, obligations.py
completion timing) more heavily than this manual seed once enough
behavioral data accumulates. If observed behavior contradicts an entry
here, flag the contradiction rather than silently overriding either source.
"""
# ──────────────────────────────────────────────────────────────────────────────

print("\n[Seeder] Sending your context to LLM for extraction...")
print("This may take a few seconds.\n")

proposed = seed_profile_from_text(MY_CONTEXT)

if not proposed:
    print("[Seeder] Nothing was extracted. Check your OpenRouter API key or try rephrasing.")
else:
    print(f"\n[Seeder] Extracted {len(proposed)} entries:\n")
    print("-" * 60)
    for i, entry in enumerate(proposed, 1):
        verified_tag = "✓ verified" if entry.get("verified") else "⚠ unverified"
        conf = int(entry.get("confidence", 1.0) * 100)
        print(f"{i:2}. [{entry['category']}] {entry['value']}")
        print(f"     {verified_tag} | confidence: {conf}%")
    print("-" * 60)

    print("\nDo you want to save all of these? (yes / no / or type numbers to skip e.g. '2 5 7')")
    user_input = input("> ").strip().lower()

    if user_input == "no":
        print("[Seeder] Nothing saved.")

    elif user_input == "yes":
        result = confirm_and_save_profile(proposed)
        print(f"[Seeder] {result}")

    else:
        # User typed numbers to SKIP
        try:
            skip_indices = {int(x) - 1 for x in user_input.split()}
            filtered = [e for i, e in enumerate(proposed) if i not in skip_indices]
            if filtered:
                result = confirm_and_save_profile(filtered)
                print(f"[Seeder] {result}")
            else:
                print("[Seeder] Nothing saved after filtering.")
        except ValueError:
            print("[Seeder] Couldn't parse that. Nothing saved.")