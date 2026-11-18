import sys
from pathlib import Path

VERSION = "1.0.1"

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if "--backend" in sys.argv:
    sys.argv.remove("--backend")
    from stodo_launcher.backend import cli

    raise SystemExit(cli(show_errors=False))

from stodo_launcher.gui import run


if __name__ == "__main__":
    run(VERSION)
