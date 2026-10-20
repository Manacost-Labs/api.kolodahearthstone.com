#!/usr/bin/env python3
"""Offload must copy without the shared sync lock and only wait for the exchange."""
import argparse
import fcntl
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'api-uploads-offload'


def load_module():
    loader = importlib.machinery.SourceFileLoader('api_uploads_offload', str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class OffloadLockTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.source = base / 'uploads'
        self.mount = self.source / '.s3-storage'
        self.mount.mkdir(parents=True)
        (self.source / 'golden').mkdir()
        self.image = self.source / 'golden' / 'card.png'
        self.image.write_bytes(b'png' * 100)
        os.chmod(self.image, 0o644)
        os.utime(self.image, (1, 1))
        self.sync_lock = base / 'sync.lock'
        patches = {
            'SOURCE': self.source,
            'MOUNT': self.mount,
            'STATE': base / 'state',
            'OFFLOAD_LOCK': str(base / 'offload.lock'),
            'SYNC_LOCK': str(self.sync_lock),
        }
        for name, value in patches.items():
            patcher = mock.patch.object(self.module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, value in (('geteuid', 0), ('lchown', None)):
            patcher = mock.patch.object(self.module.os, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(self.module, 'rc', side_effect=self.fake_rc)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sync_lock_free_during_copy = None

    def tearDown(self):
        self.tmp.cleanup()

    def fake_rc(self, method):
        if method == 'vfs/stats':
            return {'diskCache': {'uploadsQueued': 0, 'uploadsInProgress': 0,
                                  'erroredFiles': 0, 'outOfSpace': False}}
        return {}

    def sync_lock_is_free(self):
        fd = os.open(self.sync_lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        except BlockingIOError:
            return False
        finally:
            os.close(fd)

    def fake_run(self, command, *args, **kwargs):
        if command[0] == 'mountpoint':
            return subprocess.CompletedProcess(command, 0)
        if command[1] == 'copy':
            self.sync_lock_free_during_copy = self.sync_lock_is_free()
        if command[1] == 'check':
            listing = Path(command[command.index('--files-from-raw') + 1]).read_bytes()
            combined = Path(command[command.index('--combined') + 1])
            combined.write_bytes(b''.join(b'= ' + line + b'\n' for line in listing.splitlines()))
        return subprocess.CompletedProcess(command, 0)

    def run_offload(self, wait):
        args = argparse.Namespace(min_age_seconds=60, only_file=None, sync_lock_wait_seconds=wait)
        with mock.patch.object(self.module.subprocess, 'run', side_effect=self.fake_run):
            return self.module.execute(args)

    def test_copy_runs_without_the_shared_lock_and_exchange_takes_it(self):
        report = self.run_offload(wait=1)
        self.assertTrue(self.sync_lock_free_during_copy)
        self.assertEqual(report['replaced_files'], 1)
        self.assertTrue(self.image.is_symlink())
        self.assertEqual(os.readlink(self.image), str(self.mount / 'golden' / 'card.png'))

    def test_busy_shared_lock_defers_exchange_without_touching_files(self):
        holder = os.open(self.sync_lock, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            report = self.run_offload(wait=0)
        finally:
            os.close(holder)
        self.assertEqual(report['deferred_exchange'], 'shared sync lock busy')
        self.assertEqual(report['replaced_files'], 0)
        self.assertFalse(self.image.is_symlink())
        self.assertEqual(self.image.read_bytes(), b'png' * 100)
        saved = json.loads(next((self.module.STATE).glob('*.json')).read_text())
        self.assertEqual(saved['deferred_exchange'], 'shared sync lock busy')

    def test_acquire_wait_obtains_lock_once_released(self):
        holder = os.open(self.sync_lock, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(holder, fcntl.LOCK_EX)
        with self.assertRaises(BlockingIOError):
            self.module.acquire_wait(str(self.sync_lock), 0)
        os.close(holder)
        fd = self.module.acquire_wait(str(self.sync_lock), 1)
        self.assertFalse(self.sync_lock_is_free())
        os.close(fd)


if __name__ == '__main__':
    unittest.main()
