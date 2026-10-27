# Regression tests

Run from a normal Pympress checkout with Python 3.9 or newer:

```sh
python -m unittest discover -s tests -v
```

Individual test modules also work through `python -m unittest tests.test_playback`
or direct script execution. To test another checkout, set `PYMPRESS_TEST_SOURCE`
to that checkout's root; it must contain the `pympress` package. No sibling source
directory or installed Pympress distribution is required.

The suite installs null gettext translations, including `ngettext`, before
importing pure models. Localization tests explicitly replace those translators
to verify complete templates, plural forms, interpolation and English fallback.
Set `PYMPRESS_TEST_LOCALE_DIR` to a compiled locale tree to additionally check
available German translations. Missing untranslated messages may use English
fallback; translated messages must retain their format placeholders.

These tests use the standard library. They exercise real model code and selected
UI callback definitions with GTK, Cairo and native document parsing replaced at
their boundaries. They do not establish native GUI rendering, codec safety,
desktop drag-and-drop or display behavior. Native GTK/Poppler/Cairo smoke tests
remain a separate validation step.

The portable CI job runs file-reference and playback models on Windows and
macOS. Windows path tests run on the Windows filesystem, and symlink-specific
tests skip only when the filesystem or account cannot create symlinks. The Linux
jobs run the complete mock regression suite. CI does not install Pympress, change
system packages, require secrets or publish artifacts.
