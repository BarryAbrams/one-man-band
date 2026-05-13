#!/usr/bin/env python3

from __future__ import annotations

import os

from one_man_band.web import create_app, create_socketio


def main() -> int:
    app = create_app()
    socketio = create_socketio(app)
    socketio.run(
        app,
        host=os.environ.get("OMB_HOST", "0.0.0.0"),
        port=int(os.environ.get("OMB_PORT", "5000")),
        debug=os.environ.get("OMB_DEBUG", "0") == "1",
        allow_unsafe_werkzeug=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
