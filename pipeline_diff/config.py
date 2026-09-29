"""Settings from the environment, optionally loaded from a .env file."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

PREFIX = "PIPELINE_DIFF_"


def load_env(path=".env"):
    """Fill missing PIPELINE_DIFF_ settings from a .env file. Variables already set win, and nothing else is read, so a
    .env in a folder you did not write cannot set a proxy, a certificate bundle, or PYTHONPATH."""
    file = Path(path)
    if not file.is_file():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key.strip().startswith(PREFIX):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def setting(name, default=""):
    return os.environ.get(PREFIX + name, "").strip() or default


def data_dir(override=None):
    return Path(override or setting("DATA_DIR", "data"))


def timezone():
    name = setting("TIMEZONE", "UTC")
    try:
        return ZoneInfo(name)
    except Exception as exc:
        raise SystemExit(f"{PREFIX}TIMEZONE={name!r} is not a time zone name") from exc


def redact(text):
    """The text with every configured key replaced by ***."""
    text = str(text)
    for key, value in os.environ.items():
        if key.startswith(PREFIX) and key.endswith(("TOKEN", "KEY", "SECRET")) and value.strip():
            text = text.replace(value.strip(), "***")
    return text
