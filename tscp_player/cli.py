"""Command-line entry point."""

from .menu import InteractiveMenu


def main(argv=None) -> int:
    InteractiveMenu().run()
    return 0
