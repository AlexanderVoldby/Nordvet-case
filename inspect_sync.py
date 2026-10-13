"""Inspect deletion responses before deciding how to synchronise them."""
import argparse
import json
import os
from collections import Counter
from datetime import date

from ingest import VetOne


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-date', type=date.fromisoformat, default=date(2026, 10, 5))
    parser.add_argument('--to-date', type=date.fromisoformat, default=date(2026, 10, 11))
    args = parser.parse_args()
    if args.from_date > args.to_date:
        parser.error('from-date must not exceed to-date')
    client_id = os.environ.get('VETONE_CLIENT_ID') or input('Client ID: ').strip()
    secret = os.environ.get('VETONE_CLIENT_SECRET') or input('Client secret (visible): ').strip()
    api = VetOne(client_id, secret)
    params = {'from': args.from_date.isoformat(), 'to': args.to_date.isoformat(),
              'limit': 100, 'include_deleted': 'true'}
    statuses, fields, deletion_values = Counter(), set(), {}
    examples, cursors = {}, set()
    count, pages = 0, 0
    try:
        api.authenticate()
        while True:
            payload = api.get('/v1/appointments', params)
            pages += 1
            for record in payload['data']:
                count += 1
                statuses[str(record.get('status', '<missing>'))] += 1
                fields.update(record)
                markers = {key: value for key, value in record.items()
                           if any(term in key.lower() for term in ('delet', 'remov', 'archiv'))}
                for key, value in markers.items():
                    category = ('null' if value is None else 'false' if value is False
                                else 'true' if value is True else 'non-null value')
                    deletion_values.setdefault(key, Counter())[category] += 1
                # Keep samples for distinct shapes/marker categories and deletion-like statuses.
                signature = (tuple(sorted(record)),
                             tuple((k, v is not None and v is not False) for k, v in sorted(markers.items())),
                             record.get('status') if record.get('status') in ('deleted', 'removed', 'archived') else 'normal')
                if signature not in examples and len(examples) < 8:
                    examples[signature] = {k: v for k, v in record.items() if k != 'patient_ref'}
            print(f'Fetched page {pages}: {count} records')
            cursor = payload.get('next_cursor')
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in cursors:
                raise ValueError('Invalid or repeated pagination cursor')
            cursors.add(cursor)
            params = {**params, 'cursor': cursor}
    finally:
        api.session.close()
    print('\nSUMMARY (include_deleted=true)')
    print(json.dumps({'record_count': count, 'statuses': dict(statuses),
                      'fields': sorted(fields),
                      'possible_deletion_fields': {k: dict(v) for k, v in deletion_values.items()},
                      'examples': list(examples.values())}, indent=2, ensure_ascii=False))
    print('\nRead-only inspection: nothing was written to SQL Server.')


if __name__ == '__main__':
    main()
