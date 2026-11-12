"""Check synthesis manifests for retired quoted-word placeholders; never modify media."""

import argparse
import json
from pathlib import Path

from app.services.example_content_policy import is_lexical_placeholder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    files = []
    for path in args.manifest:
        files.extend(sorted(path.glob('*.json')) if path.is_dir() else [path])
    hits = []
    checked = 0
    for path in files:
        entries = json.loads(path.read_text())
        for key, value in entries.items():
            checked += 1
            if is_lexical_placeholder(value['text']):
                hits.append({'manifest': str(path), 'id': key, 'text': value['text']})
    report = {'complete': not hits, 'checked': checked, 'rejected': len(hits),
              'entries': hits, 'mediaModified': False}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'entries'}))
    return 1 if hits else 0


if __name__ == '__main__':
    raise SystemExit(main())
