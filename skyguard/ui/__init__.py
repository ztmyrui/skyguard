"""Public entry point for the desktop application."""


def main():
    """Start the desktop application."""
    from .app import main as launch

    return launch()


__all__ = ["main"]
