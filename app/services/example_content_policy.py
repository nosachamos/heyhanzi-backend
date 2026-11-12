"""Exclude lexical placeholders without rejecting genuine language-learning scenes."""

import re

_QUOTED = r'[“「『\"]([^”」』\"]+)[”」』\"]'
_PLACEHOLDERS = (
    re.compile(r'(?:我|我们)(?:正在|在)?学(?:习)?' + _QUOTED + r'(?:这个字|这个词|这个单词)?[。.!！]?'),
    re.compile(r'(?:你|你们)(?:会|能)说' + _QUOTED + r'吗[？?]?'),
    re.compile(r'这是' + _QUOTED + r'[。.!！]?'),
)


def is_lexical_placeholder(text):
    """Recognize retired quoted-word templates, not actual target-word usage."""
    compact = re.sub(r'\s+', '', text)
    return any(pattern.fullmatch(compact) for pattern in _PLACEHOLDERS)


def reject_lexical_placeholders(entries):
    rejected = {key: entry['text'] for key, entry in entries.items()
                if is_lexical_placeholder(entry['text'])}
    if rejected:
        examples = '; '.join(f'{key}: {text}' for key, text in list(rejected.items())[:3])
        raise ValueError(f'{len(rejected)} retired lexical-placeholder audio inputs; '
                         f'author contextual examples and rebuild the inventory: {examples}')
