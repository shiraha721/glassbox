"""Stable ASGI factory and module launch entry point."""

from rtagent.interfaces.http.app import create_app, serve

__all__ = ["create_app", "serve"]

if __name__ == "__main__":
    serve()
