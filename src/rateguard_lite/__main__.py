"""Allows `python -m rateguard_lite ...` (same as the `rgl` command; useful when rgl.exe is blocked by application control)."""
from .cli import app

if __name__ == "__main__":
    app()
