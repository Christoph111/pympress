#!/usr/bin/env python3
"""Keep source-checkout startup independent of an executable Git installation."""

try:
    from . import _support
except ImportError:
    import _support


import errno
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from pympress import util


SOURCE = _support.SOURCE


class VersionMetadataTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='pympress-version-tests-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / '.git').mkdir()
        metadata = SimpleNamespace(__version__='1.2.3', __author__='Example contributors',
                                   __file__=str(self.root / 'pympress' / '__init__.py'))
        patch = mock.patch.object(util.importlib, 'import_module', return_value=metadata)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(util.sys, 'frozen', False, create=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.static = {'version': '1.2.3', 'contributors': 'Example contributors'}

    def test_nonexistent_git_executable_preserves_static_metadata(self):
        check_output = subprocess.check_output
        missing = self.root / 'nonexistent-git.exe'

        def missing_executable(command, **options):
            self.assertEqual(command[0], 'git')
            return check_output([str(missing)] + command[1:], **options)

        with mock.patch.object(util.subprocess, 'check_output', side_effect=missing_executable) as command:
            self.assertEqual(util.get_pympress_meta(), self.static)
        command.assert_called_once()
        self.assertFalse(missing.exists())

    def test_os_errors_and_failed_git_commands_preserve_static_metadata(self):
        errors = [OSError(errno.EACCES, 'Git execution denied'),
                  subprocess.CalledProcessError(128, ['git', 'describe'])]
        for error in errors:
            with self.subTest(error=error):
                with mock.patch.object(util.subprocess, 'check_output', side_effect=error):
                    self.assertEqual(util.get_pympress_meta(), self.static)

    def test_installed_git_keeps_clean_tag_and_development_version_behavior(self):
        for output, expected in [(b'v1.2.3-0-gabcdef\n', '1.2.3'),
                                 (b'v1.2.3-7-gabcdef\n', '1.2.3+7@abcdef\n'),
                                 (b'v1.2.3-0-gabcdef-dirty\n', '1.2.3+0@abcdef')]:
            with self.subTest(output=output):
                with mock.patch.object(util.subprocess, 'check_output', return_value=output):
                    self.assertEqual(util.get_pympress_meta(), dict(self.static, version=expected))

    def test_install_without_git_metadata_does_not_invoke_git(self):
        (self.root / '.git').rmdir()
        with mock.patch.object(util.subprocess, 'check_output', side_effect=AssertionError('must not run')):
            self.assertEqual(util.get_pympress_meta(), self.static)


if __name__ == '__main__':
    unittest.main(verbosity=2)
