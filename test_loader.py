"""Pure and simulated transaction tests; no live API or SQL Server calls."""
import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import Mock, patch

import ingest


def record(identifier='a', deleted=None):
    value = {'id': identifier, 'clinic_id': 'c', 'scheduled_at': '2026-10-04T23:30:00Z',
             'updated_at': '2026-10-06T12:00:00Z', 'status': 'no_show', 'duration_minutes': 20}
    if deleted:
        value['deleted_at'] = deleted
    return value


class LoaderTests(unittest.TestCase):
    def test_local_date_and_soft_delete_preserved(self):
        row = ingest.appointment_row(record(deleted='2026-10-06T11:00:00Z'),
              {'c': {'timezone': 'Europe/Copenhagen'}}, date(2026,10,5), date(2026,10,5))
        self.assertEqual(row[5], '2026-10-05')
        self.assertEqual(row[7], 'no_show')
        self.assertEqual(row[9], '2026-10-06T11:00:00+00:00')

    def test_incremental_query_has_no_date_filter_and_includes_deletions(self):
        api = object.__new__(ingest.VetOne)
        api.get = Mock(return_value={'data': [record()], 'next_cursor': None})
        api.appointments(updated_since=datetime(2026,10,6,tzinfo=timezone.utc))
        params = api.get.call_args.args[1]
        self.assertNotIn('from', params)
        self.assertNotIn('to', params)
        self.assertEqual(params['include_deleted'], 'true')

    def test_pages_deduplicate_and_reject_cursor_loop(self):
        api = object.__new__(ingest.VetOne)
        api.get = Mock(side_effect=[{'data':[record()], 'next_cursor':'x'},
                                   {'data':[record()], 'next_cursor':None}])
        records, pages, duplicates = api.appointments(date(2026,10,5), date(2026,10,5))
        self.assertEqual((len(records), pages, duplicates), (1,2,1))
        api.get = Mock(side_effect=[{'data':[], 'next_cursor':'x'},
                                   {'data':[], 'next_cursor':'x'}])
        with self.assertRaises(ValueError):
            api.appointments(date(2026,10,5), date(2026,10,5))

    def test_incremental_rejects_ignored_filter(self):
        api = object.__new__(ingest.VetOne)
        api.get = Mock(return_value={'data':[record()], 'next_cursor':None})
        with self.assertRaises(ValueError):
            api.appointments(updated_since=datetime(2026,10,7,tzinfo=timezone.utc))

    def run_simulated(self, fail_checkpoint=False):
        connection = Mock()
        cursor = connection.cursor.return_value
        def execute(sql, *args):
            if fail_checkpoint and 'INSERT INTO dbo.sync_state' in sql:
                raise RuntimeError('simulated checkpoint failure')
            return cursor
        cursor.execute.side_effect = execute
        cursor.fetchone.side_effect = [None, (None,), None, None]
        api = Mock()
        api.get.return_value = {'data':[{'id':'c','name':'Clinic','country':'DK',
                                      'timezone':'Europe/Copenhagen','active':True}]}
        api.appointments.side_effect = [([record(deleted='2026-10-06T11:00:00Z')],1,0), ([],1,0)]
        patches = [patch.object(ingest.pyodbc, 'connect', return_value=connection),
                   patch.object(ingest, 'VetOne', return_value=api),
                   patch.dict(os.environ, {'VETONE_CLIENT_ID':'test', 'VETONE_CLIENT_SECRET':'test'}),
                   patch('sys.argv',['ingest.py','--from-date','2026-10-05','--to-date','2026-10-05'])]
        with patches[0], patches[1], patches[2], patches[3]:
            if fail_checkpoint:
                with self.assertRaises(RuntimeError): ingest.main()
            else:
                ingest.main()
        return connection, cursor

    def test_success_commits_data_and_checkpoint(self):
        connection, cursor = self.run_simulated()
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()
        self.assertTrue(any('INSERT INTO dbo.sync_state' in call.args[0]
                            for call in cursor.execute.call_args_list))

    def test_checkpoint_failure_rolls_back(self):
        connection, _ = self.run_simulated(fail_checkpoint=True)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
