"""Entry point for the PyInstaller bundle.

This lives outside the ``yotoize`` package on purpose: PyInstaller puts the
entry script's directory on ``sys.path``, and pointing it at a file inside the
package would make ``config``, ``logger``, ``utils`` and friends importable as
top-level modules.
"""

import multiprocessing

from yotoize.cli import cli

if __name__ == "__main__":
    # tqdm allocates a multiprocessing lock, which starts a resource tracker by
    # re-running sys.executable with interpreter flags. In a frozen build
    # sys.executable is this binary, so click sees "-B" and errors out unless
    # multiprocessing gets its hooks in first.
    multiprocessing.freeze_support()

    # Without prog_name, click derives it from argv[0] and Windows help text
    # would read "yotoize.exe".
    cli(prog_name="yotoize")
