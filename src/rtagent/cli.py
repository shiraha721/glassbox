"""Stable console-script entry point for the concrete CLI adapter."""

from rtagent.interfaces.cli import main

__all__ = ["main"]

if __name__ == "__main__":
    main()
