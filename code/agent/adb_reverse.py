"""Keep the development phone's two ADB reverse mappings alive across USB resets."""
from __future__ import annotations

import shutil
import subprocess
import threading
from typing import Optional


class AdbReverseWatchdog:
    def __init__(self, port: int, file_port: int, serial: Optional[str] = None,
                 interval: float = 2.0) -> None:
        self.ports = tuple(dict.fromkeys((port, file_port)))
        self.serial = serial or None
        self.interval = interval
        self.adb = shutil.which('adb')
        if not self.adb:
            raise RuntimeError('adb not found; install Android platform-tools')
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._status = ''

    def _run(self, *args: str) -> str:
        result = subprocess.run([self.adb, *args], capture_output=True, text=True,
                                timeout=3, check=True)
        return result.stdout

    def _report(self, message: str) -> None:
        if message != self._status:
            print('adb reverse: ' + message, flush=True)
            self._status = message

    def check_once(self) -> None:
        """Pin a single device; only repair this session's missing/wrong mappings."""
        try:
            devices = [fields[0] for line in self._run('devices').splitlines()
                       if len(fields := line.split()) == 2 and fields[1] == 'device']
            if self.serial is None:
                if len(devices) != 1:
                    self._report('waiting for one authorized phone; with multiple devices set ANDROID_SERIAL')
                    return
                self.serial = devices[0]
            if self.serial not in devices:
                self._report('phone offline; waiting for USB reconnect')
                return
            rows = self._run('-s', self.serial, 'reverse', '--list').splitlines()
            mappings = {fields[-2]: fields[-1] for row in rows
                        if len(fields := row.split()) >= 3}
            repaired = []
            for port in self.ports:
                if self._stop.is_set():
                    return
                endpoint = 'tcp:%s' % port
                if mappings.get(endpoint) != endpoint:
                    self._run('-s', self.serial, 'reverse', endpoint, endpoint)
                    repaired.append(str(port))
            if repaired:
                # Log every actual restoration, including silent mapping loss while USB stays online.
                print('adb reverse: restored ports ' + ', '.join(repaired), flush=True)
            self._report('control and artifact mappings ready')
        except (OSError, subprocess.SubprocessError):
            # ADB can temporarily disappear during a cable reset or daemon restart.
            self._report('ADB unavailable; will retry automatically')

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.check_once()
            self._stop.wait(self.interval)

    def __enter__(self):
        self._thread = threading.Thread(target=self._loop, name='adb-reverse', daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=4)
