import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apk_delivery import ApkDelivery
from connect_phone import ConnectPhone


class StartupCatalogTest(unittest.TestCase):
    def test_reconnect_recovers_only_requested_waiting_tasks_after_missed_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Mock()
            delivery = ApkDelivery(bridge, Mock(), Path(tmp) / 'jobs.json')
            job = {'taskId': 'drawing', 'state': 'making'}
            bridge.send_job.side_effect = OSError('disconnected')
            delivery.publish(job)
            # Completion was durably stored while the phone was disconnected.
            completed = dict(job, state='downloading', sha256='b' * 64, downloadUrl='http://localhost/apk')
            delivery.jobs['drawing'] = completed
            delivery.jobs['unrelated'] = dict(completed, taskId='unrelated')
            bridge.reset_mock(side_effect=True)
            delivery.send_task_statuses(json.dumps({'taskIds': ['drawing', 'drawing', 'missing']}))
            self.assertEqual(bridge.send_job.call_count, 2)
            self.assertEqual(bridge.send_job.call_args_list[0].args[0], completed)
            self.assertEqual(bridge.send_job.call_args_list[1].args[0],
                             {'kind': 'task_missing', 'taskId': 'missing'})

    def test_invalid_task_queries_never_replay_all_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Mock()
            delivery = ApkDelivery(bridge, Mock(), Path(tmp) / 'jobs.json')
            for value in ('invalid', '[]', '{}', '{"taskIds":[]}', '{"taskIds":[null]}',
                          json.dumps({'taskIds': ['x'] * 51})):
                delivery.send_task_statuses(value)
            bridge.send_job.assert_not_called()

    def test_task_check_is_control_message(self):
        bridge = ConnectPhone()
        bridge.send_to_phone = Mock()
        payload = json.dumps({'taskIds': ['drawing']})
        bridge._handle_line(json.dumps({'type': 'task_check', 'text': payload}).encode(), Mock())
        self.assertEqual(bridge._inbound_queue.get_nowait(), ('task_check', payload))
        bridge.send_to_phone.assert_not_called()

    def test_catalog_only_lists_downloadable_artifacts_and_completes_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Mock()
            delivery = ApkDelivery(bridge, Mock(), Path(tmp) / 'jobs.json')
            request = 'a' * 32
            delivery.send_catalog(request)
            bridge.send_job.assert_called_once_with({'kind': 'catalog_complete', 'requestId': request, 'count': 0})
            bridge.reset_mock()
            ready = {'taskId': 'snake', 'state': 'downloading', 'sha256': 'b' * 64, 'downloadUrl': 'http://localhost/file'}
            delivery.jobs = {'snake': ready, 'unfinished': {'state': 'making'}, 'failed': {'state': 'failed'}}
            delivery.send_catalog(request)
            self.assertEqual(bridge.send_job.call_count, 2)
            self.assertEqual(bridge.send_job.call_args_list[0].args[0]['job'], ready)
            self.assertEqual(bridge.send_job.call_args_list[1].args[0]['count'], 1)
            bridge.reset_mock()
            delivery.send_catalog('invalid')
            bridge.send_job.assert_not_called()

    def test_check_is_a_control_message_without_chat_acknowledgement(self):
        bridge = ConnectPhone()
        bridge.send_to_phone = Mock()
        bridge._handle_line(b'{"type":"catalog_check","text":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}', Mock())
        self.assertEqual(bridge._inbound_queue.get_nowait()[0], 'catalog_check')
        bridge.send_to_phone.assert_not_called()


if __name__ == '__main__':
    unittest.main()
