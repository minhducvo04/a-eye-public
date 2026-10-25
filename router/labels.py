"""The three routes, described once. Data generation and the Haiku baseline use these same words."""

LABELS = ('now', 'past', 'expert')

DESCRIPTIONS = {
    'now': (
        'Answer from the live camera scene and the conversation so far: what is this, where does it go, is it '
        'right, what is the next step, how do I do X, small talk, "say that again". Also simple one-step '
        'questions about what the user did or said a moment ago that the conversation already shows.'),
    'past': (
        'Search the video memory of earlier in this call (Rewind): where did an object go or get left, when did '
        'the user do something, what happened before or after an event, did the user already do a step, how '
        'many times something happened, show or go back to an earlier moment. Needs footage from the past, not '
        'the current frame.'),
    'expert': (
        'Escalate to a stronger reasoning model: multi-step diagnosis (it still does not work, symptoms with '
        'several possible causes), safety-critical steps (mains electricity, gas, fire, burning smell, food '
        'safety, allergies, child safety), the user doubting the last answer ("are you sure", "double check", '
        '"think hard"), scene and user evidence that conflict, an object nobody can identify, or two or more '
        'failed tries at the same step.'),
}

MODES = ('general', 'cooking', 'assembly', 'pc_build', 'bike_repair', 'home_repair', 'gardening', 'crafts',
         'car_maintenance', 'electronics', 'makeup', 'cleaning')
