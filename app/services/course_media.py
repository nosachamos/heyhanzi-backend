"""Trusted media-only compatibility; client-supplied fingerprints grant nothing."""
import copy
import hashlib
import json
import re


def semantic_fingerprint(item):
    """Keep all authored meaning/grading/placement; exclude delivery assets only.

    The compiler proves equality with the previous full catalog before emitting
    aliases. The API independently checks that the alias table still belongs to
    this exact current semantic projection. This does not replace the complete
    fingerprint used for catalog integrity.
    """
    source = copy.deepcopy(item)
    source.pop('fingerprint', None)
    definition = source['definition']
    if definition['id'] == 'ACT-CLOZE':
        definition.pop('audio', None)
    elif definition['id'] in {'ACT-STORY', 'ACT-STORY-COMP'}:
        activity = definition['storyActivity']
        activity.pop('recordings', None)
        for word in activity.get('words', {}).values():
            word.pop('audio', None)
    else:
        raise ValueError('Unsupported media compatibility activity')
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


def validate_compatibility(data, by_id):
    """Only the server-owned exported catalog can name compatible old versions."""
    metadata = data.get('media_compatibility')
    if metadata is None:
        return
    if (not isinstance(metadata, dict) or type(metadata.get('format_version')) is not int
            or metadata['format_version'] != 1 or not isinstance(metadata.get('items'), dict)
            or metadata.get('learnerPolicySha256') != hashlib.sha256(json.dumps(
                data.get('learner_policy'), ensure_ascii=False, sort_keys=True,
                separators=(',', ':')).encode()).hexdigest()):
        raise ValueError('Invalid media compatibility metadata')
    occupied = {item['fingerprint']: identifier for identifier, item in by_id.items()}
    for identifier, row in metadata['items'].items():
        item = by_id.get(identifier)
        if (item is None or not isinstance(row, dict)
                or type(row.get('revision')) is not int or row['revision'] != item['revision']
                or row.get('currentFingerprint') != item['fingerprint']
                or row.get('semanticSha256') != semantic_fingerprint(item)):
            raise ValueError('Media compatibility does not match current authored content')
        previous = row.get('previousFingerprints')
        if (not isinstance(previous, list) or not 1 <= len(previous) <= 64
                or any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value)
                       for value in previous)
                or len(set(previous)) != len(previous)):
            raise ValueError('Invalid media-compatible fingerprint aliases')
        for value in previous:
            if value in occupied:
                raise ValueError('Media-compatible fingerprint alias has another identity')
            occupied[value] = identifier
