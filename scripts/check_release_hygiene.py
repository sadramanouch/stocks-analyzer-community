#!/usr/bin/env python3
"""Reject accidental tracked private artifacts; pair with Gitleaks for secrets."""
from pathlib import Path, PurePosixPath
import subprocess
import sys
import unittest


def forbidden_path(name):
    path = PurePosixPath(name)
    lower = path.name.lower()
    if lower.startswith('.env') and lower != '.env.example':
        return True
    if lower.endswith(('.db', '.sqlite', '.sqlite3', '.db-wal', '.db-shm',
                       '.pem', '.key', '.p12', '.pfx', '.jks', '.mobileprovision')):
        return True
    if '.sqlite' in lower and lower.endswith(('-wal', '-shm')):
        return True
    return bool(set(path.parts) & {'.git', 'node_modules', '.venv', '.expo', '.next'})


class GuardTests(unittest.TestCase):
    def test_configuration(self):
        for name in ['.env', 'server/.env.local', '.env.production', '.ENV']:
            with self.subTest(name=name):
                self.assertTrue(forbidden_path(name))
        self.assertFalse(forbidden_path('server/.env.example'))

    def test_database_and_keys(self):
        for name in ['backup/data.sqlite3', 'server/data.db', 'a.db-wal',
                     'a.sqlite-shm', 'certificate.pem', 'signing.p12']:
            with self.subTest(name=name):
                self.assertTrue(forbidden_path(name))

    def test_sources_and_hidden_artifacts(self):
        self.assertFalse(forbidden_path('src/config.ts'))
        self.assertFalse(forbidden_path('tests/fixtures/example.json'))
        self.assertTrue(forbidden_path('frontend/node_modules/a/index.js'))


def main():
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    failures = []
    for name in filter(None, names):
        if forbidden_path(name):
            failures.append(name + ': private or generated artifact path')
        elif (root / name).is_symlink():
            failures.append(name + ': inspect symlinks separately before distributing')
    license_text = (root / 'LICENSE').read_text()
    if not all(s in license_text for s in ['MIT License', 'Copyright (c) 2026 Sadra Khorvash',
                                         'Permission is hereby granted']):
        failures.append('LICENSE: expected MIT attribution missing; request explicit review')
    for failure in failures:
        print(failure)
    if failures:
        return 1
    print('Tracked artifact and license checks passed. This is not a security certification.')
    return 0


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        unittest.main(argv=[sys.argv[0]])
    else:
        sys.exit(main())
