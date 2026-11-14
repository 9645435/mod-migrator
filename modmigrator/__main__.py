from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .util import APP_DISPLAY_NAME, APP_VERSION, log_dir


def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        handler = RotatingFileHandler(
            log_dir() / "modmigrator.log",
            maxBytes=2_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except OSError:
        pass
    if sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)


def main() -> None:
    _setup_logging()
    logging.getLogger("modmigrator").info("%s %s starting", APP_DISPLAY_NAME, APP_VERSION)
    args = sys.argv[1:]
    if args and args[0] != "--gui":
        from .cli import main as cli_main

        raise SystemExit(cli_main())
    from .ui.app import main as ui_main

    ui_main()


if __name__ == "__main__":
    main()
