from __future__ import annotations


def create_app(*args, **kwargs):
    from .web import create_app as _create_app

    return _create_app(*args, **kwargs)


def create_socketio(*args, **kwargs):
    from .web import create_socketio as _create_socketio

    return _create_socketio(*args, **kwargs)

__all__ = ["create_app", "create_socketio"]
