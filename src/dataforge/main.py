"""Entry point — loaded by the 'dataforge' console script."""
import sys

from dataforge.cli.app import app

if __name__ == "__main__":
    try:
        app()
    except SystemExit as exc:
        sys.exit(exc.code)
    except Exception as exc:
        msg = str(exc)
        if "No such command" in msg or "no such option" in msg.lower():
            from dataforge.cli.app import _typer_error_handler
            _typer_error_handler(exc)  # prints the "did you mean" panel, exits 2
        # Anything else is a crash: say so. This used to exit 2 silently, which
        # hid the standalone binaries' missing data files for several releases.
        import traceback
        traceback.print_exc()
        sys.exit(1)
