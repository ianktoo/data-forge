# Installation

## uv (recommended)
```bash
uv tool install llm-web-crawler
dataforge
```

Update:
```bash
uv tool upgrade llm-web-crawler
```

Uninstall:
```bash
uv tool uninstall llm-web-crawler
```

## pip
```bash
pip install llm-web-crawler
dataforge
```

Update:
```bash
pip install --upgrade llm-web-crawler
```

Uninstall:
```bash
pip uninstall llm-web-crawler
```

## From source
```bash
git clone https://github.com/ianktoo/data-forge.git
cd data-forge
uv sync
uv run dataforge
```

## Standalone executables (no Python required)
Download pre-built binaries for your platform from [GitHub Releases](https://github.com/ianktoo/data-forge/releases):

| Platform | File |
|---|---|
| Windows | `dataforge-windows-x64.exe` |
| macOS | `dataforge-macos-x64` |
| Linux | `dataforge-linux-x64` |

## Using Ollama (fully local, no API key)

```bash
ollama serve
ollama pull llama3.2
dataforge config   # choose ollama / llama3.2
dataforge
```

See [CONFIGURATION.md](CONFIGURATION.md) for all provider and environment options.

## Updating and uninstalling from DataForge

`dataforge update` and `dataforge uninstall` (also in the main menu) use the
right installer for how DataForge was installed: uv tool, pip, or a venv made
by uv without pip.

A program should not replace or remove itself while it runs, so by default
both commands **close DataForge first**. A small helper waits for DataForge to
exit, then runs the installer. On Windows its progress opens in a new window;
on macOS and Linux it stays in the same terminal.

**Only a person, in their own terminal, can update or uninstall.** Both
commands refuse to run without an interactive terminal (scripts, AI agents)
or inside an AI agent session such as Claude Code, and no flag skips their
prompts. A new release can change recipes, outputs or commands that you, your
scripts or an agent rely on, so read the
[release notes](https://github.com/ianktoo/data-forge/releases) first and
decide for yourself.

`dataforge update`:
1. Checks PyPI. If you are already on the latest release, it stops there.
   Otherwise it shows the latest version, warns on a major version change,
   and links the release notes.
2. Asks how to update (`--in-place` preselects the second choice):
   - **Close DataForge, then update (recommended).**
   - **Update in place.** The installer runs while DataForge is open. On
     Windows expect an error about a file being in use even when the update
     worked (see below).
3. Either way, run `dataforge update` again afterwards to check the version.

`dataforge uninstall`:
1. Lists this folder's DataForge data (`dataforge.db`, `output/`,
   `.dataforge`) and asks **Keep your data?** (default: yes). Choosing to
   delete asks once more. `--keep-data` / `--delete-data` preselect the first
   answer; the second question is still asked.
2. Warns that DataForge will close, asks you to confirm, then exits and
   uninstalls. Data you chose to delete is removed only after the uninstall
   succeeds.

Only data inside the current folder is ever deleted. A database or output
folder that lives elsewhere is listed for you to remove by hand. `.env` is
never deleted, because it may hold keys other tools use. Other project folders
keep their own data; run `uninstall` there, or delete them by hand.

Running from a source checkout (editable install), neither command touches
anything: update with `git pull` and `uv sync`, remove by deleting the folder.

## Known issues and workarounds

### Windows: "file in use" (os error 32) when updating or uninstalling

Windows locks a running `.exe` and the libraries it has loaded. If the
installer runs while any DataForge process is open, it cannot replace or
remove those files:

```
failed to copy file from ...\Scripts\dataforge.exe to ...\bin\dataforge.exe:
The process cannot access the file because it is being used by another process. (os error 32)
```

When this comes from `dataforge update --in-place`, the update itself usually
worked: uv installs the new version and only fails to refresh the
`dataforge.exe` launcher. DataForge reports the update with a warning. (Up to
2.4.2, `dataforge update` always updated in place and reported "Update
failed" here, and running it a second time reported success.)

Even the close-first mode fails if *another* DataForge process uses the same
install. DataForge warns you before closing when it finds one. The usual
culprit is an MCP client (such as Claude Code) running `dataforge mcp`.

Workaround:
1. Close every running DataForge: interactive sessions, runs, and MCP clients
   that started `dataforge mcp` from this install.
2. In a plain terminal, run the installer yourself:
   ```bash
   uv tool upgrade llm-web-crawler        # or: uv tool uninstall llm-web-crawler
   ```
3. If an in-place update warned that the launcher could not be refreshed, run:
   ```bash
   uv tool install --force llm-web-crawler
   ```

pip installs hit the same lock (`[WinError 32]`). Close DataForge, then run
`pip install --upgrade llm-web-crawler` (or `pip uninstall llm-web-crawler`).

### `dataforge update` does nothing on a pinned install

If you installed an exact version (`uv tool install llm-web-crawler==2.4.1`),
uv keeps that version, and `dataforge update` says it is pinned. To move to
the latest release:

```bash
uv tool install llm-web-crawler@latest
```

### From source: `uv sync` / `uv run` fails with os error 32

If an MCP client runs `dataforge mcp` from the repository's `.venv`, uv
cannot reinstall the project's `dataforge.exe` while it is running. Either
stop the MCP client first, or skip the re-sync:

```bash
uv run --no-sync pytest
```
