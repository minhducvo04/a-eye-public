"""Fixed input routing and output vocabulary. These words are detectors, never copy."""
import re
import unicodedata

ROLE_WORDS = ('dad', 'daddy', 'father', 'mom', 'mommy', 'mother', 'parent', 'parental')
CLAIMS = ("i'm proud", 'i am proud', 'proud of you', "i'm always here", 'i am always here',
          'always here for you', 'i love you', 'i care about you', 'you mean everything to me',
          'you only need me', "you don't need anyone else", 'you do not need anyone else',
          "they don't matter", 'they do not matter', "doesn't matter", 'does not matter',
          'i believe in you', 'you can always count on me', 'i will always', "i'll always",
          'good job', 'great job', 'well done', 'amazing work')
NEUTRAL = 'That sounds difficult. You can choose whether to talk with someone you trust.'
SERIOUS = ('That sounds serious. Consider a trusted adult or a local helpline; '
           'if you are in immediate danger, contact emergency services.')
FALLBACK = 'You can choose the next step or ask for instructions.'


def normalized(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', text).lower().replace('’', "'")).strip()


def claim_tokens(text):
    # Punctuation separates words, including straight/curly apostrophes.
    return re.findall(r'\w+', normalized(text))


# Up to three filler words between each pair; whole tokens avoid substring hits.
CLAIM_PATTERNS = tuple(re.compile(
    r'\b' + r'(?: \w+){0,3} '.join(map(re.escape, claim_tokens(claim))) + r'\b'
) for claim in CLAIMS)


def allowed(text):
    text = ' '.join(claim_tokens(text))
    return not (re.search(r'\b(?:' + '|'.join(ROLE_WORDS) + r')s?\b', text)
                or any(pattern.search(text) for pattern in CLAIM_PATTERNS))


def check(text):
    return text if allowed(text) else FALLBACK


def vent_reply(text):
    text = normalized(text)
    relation = re.search(r'\b(?:' + '|'.join(ROLE_WORDS) +
                         r'|family|brother|sister|sibling|relative|grandparent|grandma|grandpa)s?\b', text)
    if not relation:
        return None
    serious = re.search(r"\b(hurt\w*|hit\w*|abus\w*|threat\w*|afraid|scared|unsafe|danger|violence|kill\w*)\b", text)
    if serious:
        return SERIOUS
    negative = re.search(r"\b(hate\w*|mean|awful|bad|annoy\w*|angry|yell\w*|shout\w*|fight\w*|upset|"
                         r"unfair|useless|stupid|toxic|neglect\w*|abandon\w*|insult\w*|"
                         r"doesn't (care|listen|love|matter)|don't (care|listen|love)|never (helps|listens))\b", text)
    return NEUTRAL if negative else None
