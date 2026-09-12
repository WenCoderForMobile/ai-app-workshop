from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adb_reverse import AdbReverseWatchdog


class AdbReverseTest(unittest.TestCase):
    def setUp(self):
        ctx = patch('adb_reverse.shutil.which', return_value='/bin/adb')
        ctx.start()
        self.addCleanup(ctx.stop)
        ctx = patch('builtins.print')
        ctx.start()
        self.addCleanup(ctx.stop)

    def test_only_missing_mapping_is_restored(self):
        guard = AdbReverseWatchdog(17890, 17891)
        with patch.object(guard, '_run', side_effect=[
            'List of devices attached\nphone device\n',
            'UsbFfs tcp:17890 tcp:17890\nUsbFfs tcp:9000 tcp:9001\n', '',
        ]) as run:
            guard.check_once()
        self.assertEqual(guard.serial, 'phone')
        self.assertEqual(run.call_args_list[-1].args,
                         ('-s', 'phone', 'reverse', 'tcp:17891', 'tcp:17891'))
        self.assertEqual(run.call_count, 3)

    def test_healthy_mapping_is_not_rebound(self):
        guard = AdbReverseWatchdog(17890, 17891, 'phone')
        with patch.object(guard, '_run', side_effect=[
            'phone device\n',
            'UsbFfs tcp:17890 tcp:17890\nUsbFfs tcp:17891 tcp:17891\n',
        ]) as run:
            guard.check_once()
        self.assertEqual(run.call_count, 2)

    def test_usb_reconnect_restores_both_ports_without_switching_phone(self):
        guard = AdbReverseWatchdog(17890, 17891, 'phone')
        with patch.object(guard, '_run', side_effect=[
            'other device\nphone offline\n',
            'phone device\nother device\n', '', '', '',
        ]) as run:
            guard.check_once()
            self.assertEqual(run.call_count, 1)
            guard.check_once()
        self.assertEqual([c.args for c in run.call_args_list[-2:]], [
            ('-s', 'phone', 'reverse', 'tcp:17890', 'tcp:17890'),
            ('-s', 'phone', 'reverse', 'tcp:17891', 'tcp:17891'),
        ])

    def test_multiple_or_unauthorized_devices_waits_without_mapping(self):
        for devices in ('a device\nb device\n', 'a unauthorized\n', ''):
            guard = AdbReverseWatchdog(17890, 17891)
            with patch.object(guard, '_run', return_value=devices) as run:
                guard.check_once()
            self.assertIsNone(guard.serial)
            self.assertEqual(run.call_count, 1)

    def test_adb_timeout_does_not_prevent_later_recovery(self):
        guard = AdbReverseWatchdog(17890, 17891)
        with patch.object(guard, '_run', side_effect=[
            subprocess.TimeoutExpired('adb', 3), 'phone device\n', '', '', '',
        ]) as run:
            guard.check_once()
            guard.check_once()
        self.assertEqual(run.call_count, 5)

    def test_wrong_destination_is_repaired_and_custom_ports_supported(self):
        guard = AdbReverseWatchdog(18000, 18001)
        with patch.object(guard, '_run', side_effect=[
            'phone device\n', 'UsbFfs tcp:18000 tcp:9999\nUsbFfs tcp:18001 tcp:18001\n', '',
        ]) as run:
            guard.check_once()
        self.assertEqual(run.call_args_list[-1].args,
                         ('-s', 'phone', 'reverse', 'tcp:18000', 'tcp:18000'))

    def test_context_stops_watchdog_thread_promptly(self):
        import threading
        guard = AdbReverseWatchdog(17890, 17891, interval=60)
        checked = threading.Event()
        with patch.object(guard, 'check_once', side_effect=checked.set):
            with guard:
                self.assertTrue(checked.wait(1))
        self.assertFalse(guard._thread.is_alive())


if __name__ == '__main__':
    unittest.main()
