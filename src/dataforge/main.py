"""Entry point — loaded by the 'dataforge' console script."""
import sys
from dataforge.cli.app import app

if __name__ == "__main__":
    try:
        app()
    except SystemExit as exc:
        sys.exit(exc.code)
    except Exception as exc:
        from dataforge.cli.app import _typer_error_handler
        _typer_error_handler(exc)
        sys.exit(2)
