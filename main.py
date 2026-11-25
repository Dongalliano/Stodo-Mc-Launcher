import sys
from pathlib import Path

VERSION = "1.0.4"

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if len(sys.argv) > 1 and sys.argv[1] == "--install-copy":
    from stodo_launcher.shortcuts import install_copy_cli

    raise SystemExit(install_copy_cli(sys.argv[2:]))

if "--backend" in sys.argv:
    sys.argv.remove("--backend")
    from stodo_launcher.backend import cli

    raise SystemExit(cli(show_errors=False))

from stodo_launcher.single_instance import acquire_single_instance

_single_instance_handle = acquire_single_instance()
if _single_instance_handle is None:
    raise SystemExit(0)

from stodo_launcher.gui import run


if __name__ == "__main__":
    run(VERSION)
