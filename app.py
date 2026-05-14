#!/usr/bin/env python3

from __future__ import annotations

import os
import signal

from one_man_band.web import create_app, create_socketio, shutdown


def _handle_shutdown(_signum, _frame) -> None:
    shutdown()
    raise SystemExit(0)


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)
    app = create_app()
    socketio = create_socketio(app)
    try:
        socketio.run(
            app,
            host=os.environ.get("OMB_HOST", "0.0.0.0"),
            port=int(os.environ.get("OMB_PORT", "5000")),
            debug=os.environ.get("OMB_DEBUG", "0") == "1",
            allow_unsafe_werkzeug=os.environ.get("OMB_ALLOW_UNSAFE_WERKZEUG", "1") == "1",
        )
    finally:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
