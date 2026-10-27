"""Locate the tested checkout and configure predictable English fallback text."""

import gettext
import logging
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('PYMPRESS_TEST_SOURCE', str(ROOT))).resolve()
if not (SOURCE / 'pympress').is_dir():
    raise RuntimeError('PYMPRESS_TEST_SOURCE must name a checkout containing the pympress package.')

# Script execution, package discovery and an alternate checkout use the same
# imports. Installing only the null translator keeps pure model imports free
# from native GTK and catalog-generation requirements.
for directory in (SOURCE, Path(__file__).resolve().parent):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
gettext.NullTranslations().install(names=['ngettext'])
sys.dont_write_bytecode = True
# Expected failure-path logs should not use logging's emergency stderr handler.
# Tests can still capture and assert emitted records through their own handlers.
logging.getLogger().addHandler(logging.NullHandler())


def symlink_or_skip(test, link, target):
    """Require symlink support only for tests whose behavior needs a symlink."""
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as error:
        test.skipTest('The current filesystem/account cannot create symlinks: {}'.format(error))
