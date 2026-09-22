# skills/example_skill.py
# Template for creating new Jarvis skills

SKILL_NAME = "example_skill"
ENABLED = False  # Template only; never hijack production routing
TRIGGERS = [
    "example",
    "demo skill",
    "test skill",
    "hello skill"
]
PRIORITY = 60   # Higher = runs before other skills (default is 50)


def can_handle(user_input):
    """
    Optional intelligent check. Return True if this skill should handle the input.
    """
    text = user_input.lower()
    return any(word in text for word in TRIGGERS)


def handle(user_input, context=None):
    """
    Main handler.
    'context' contains recent conversation summary (Iron Man feature).
    """
    if context:
        print("[ExampleSkill] Received context:", context[:80] + "...")
    
    return "This is an improved example skill. I now have access to conversation context!"


# Optional: You can add more functions if needed
def get_info():
    return f"I am {SKILL_NAME} with priority {PRIORITY}"