"""Locating the ``ffmpeg`` and ``ffprobe`` executables.

Release builds ship both binaries inside the bundle, so a downloaded yotoize
works with nothing else installed. Source installs fall back to whatever is on
``PATH``, which is what the project has always required.

Set ``YOTOIZE_FFMPEG`` / ``YOTOIZE_FFPROBE`` to force a specific binary — useful
for pointing a release build at a newer ffmpeg than the one it shipped with.
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Optional


def _bundle_dir() -> Optional[Path]:
    """Directory PyInstaller unpacked our bundled data into, if frozen."""
    if not getattr(sys, "frozen", False):
        return None
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def _locate(name: str, env_var: str) -> str:
    override = os.environ.get(env_var)
    if override:
        return override

    bundle_dir = _bundle_dir()
    if bundle_dir is not None:
        candidate = bundle_dir / (f"{name}.exe" if os.name == "nt" else name)
        if candidate.exists():
            return str(candidate)

    # Bare name as the last resort so the callers' existing FileNotFoundError
    # handling still reports a missing ffmpeg the way it always has.
    return shutil.which(name) or name


def ffmpeg_executable() -> str:
    """Path to the ffmpeg binary to invoke."""
    return _locate("ffmpeg", "YOTOIZE_FFMPEG")


def ffprobe_executable() -> str:
    """Path to the ffprobe binary to invoke."""
    return _locate("ffprobe", "YOTOIZE_FFPROBE")


def is_bundled() -> bool:
    """True when running a release build that carries its own ffmpeg."""
    bundle_dir = _bundle_dir()
    if bundle_dir is None:
        return False
    return (bundle_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")).exists()
