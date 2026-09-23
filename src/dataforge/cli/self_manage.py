"""Updating and uninstalling DataForge from DataForge itself.

A program cannot safely replace or remove itself while it runs: on Windows the
running dataforge.exe and the environment's DLLs are locked (os error 32), so
`uv tool upgrade` / `uv tool uninstall` fail part way. The safe route is to
hand the job to a small helper process that waits for DataForge to exit, then
runs the installer.

The helper runs on the *base* interpreter with `-I` (isolated: no
site-packages), so it holds no file inside the environment being replaced.
On POSIX, where a running program can be replaced, DataForge simply execs the
helper, so the output stays in the same terminal.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

PACKAGE = "llm-web-crawler"
PYPI_JSON = f"https://pypi.org/pypi/{PACKAGE}/json"

InstallKind = Literal["frozen", "uv-tool", "editable", "env"]


def detect_install() -> InstallKind:
    """How this copy of DataForge was installed."""
    if getattr(sys, "frozen", False):
        return "frozen"
    if (Path(sys.prefix) / "uv-receipt.toml").exists():
        return "uv-tool"
    try:
        from importlib.metadata import distribution
        direct = distribution(PACKAGE).read_text("direct_url.json")
        if direct and json.loads(direct).get("dir_info", {}).get("editable"):
            return "editable"
    except Exception:
        pass
    return "env"


def _env_command(action: Literal["update", "uninstall"]) -> list[str] | None:
    """pip (or uv pip, for venvs created without pip) against this interpreter."""
    import importlib.util
    if importlib.util.find_spec("pip") is not None:
        base = [sys.executable, "-m", "pip"]
        return base + (["install", "--upgrade", PACKAGE] if action == "update"
                       else ["uninstall", "-y", PACKAGE])
    if shutil.which("uv"):
        base = ["uv", "pip"]
        return base + (["install", "--upgrade", "--python", sys.executable, PACKAGE]
                       if action == "update"
                       else ["uninstall", "--python", sys.executable, PACKAGE])
    return None


def installer_command(kind: InstallKind, action: Literal["update", "uninstall"]) -> list[str] | None:
    """The command that updates or removes this install, or None if there isn't one."""
    if kind == "uv-tool":
        return ["uv", "tool", "upgrade" if action == "update" else "uninstall", PACKAGE]
    if kind == "env":
        return _env_command(action)
    return None


def latest_version(timeout: float = 5.0) -> str | None:
    """Latest release on PyPI, or None if PyPI can't be reached."""
    try:
        import httpx
        r = httpx.get(PYPI_JSON, timeout=timeout)
        r.raise_for_status()
        return str(r.json()["info"]["version"])
    except Exception:
        return None


def is_newer(latest: str, current: str) -> bool:
    try:
        from packaging.version import Version
        return Version(latest) > Version(current)
    except Exception:
        return latest != current


def is_major_jump(latest: str, current: str) -> bool:
    """True when *latest* changes the major version (expect breaking changes)."""
    try:
        from packaging.version import Version
        return Version(latest).major != Version(current).major
    except Exception:
        return latest.split(".")[0] != current.split(".")[0]


def other_instances() -> list[tuple[int, str]]:
    """Other running processes that use this install (they keep its files locked)."""
    try:
        import psutil
    except ImportError:
        return []
    me = psutil.Process(os.getpid())
    skip = {me.pid} | {p.pid for p in me.parents()}
    prefix = os.path.normcase(os.path.realpath(sys.prefix))
    found = []
    for p in psutil.process_iter(["pid", "exe", "cmdline"]):
        if p.info["pid"] in skip:
            continue
        exe = os.path.normcase(p.info["exe"] or "")
        cmd = " ".join(p.info["cmdline"] or [])
        if prefix in exe or prefix in os.path.normcase(cmd):
            found.append((p.info["pid"], cmd[:120]))
    return found


def data_paths(cwd: Path, db_path: Path, output_dir: Path) -> tuple[list[Path], list[Path]]:
    """DataForge's data for the project in *cwd*: (deletable, outside_project).

    Only paths inside *cwd* are ever deleted; a database or output folder the
    project file points elsewhere is reported for the user to remove by hand.
    `.env` is never included: it may hold keys other tools use.
    """
    cwd = cwd.resolve()
    candidates = [cwd / ".dataforge", db_path, output_dir]
    inside: list[Path] = []
    outside: list[Path] = []
    for p in candidates:
        p = (cwd / p).resolve() if not p.is_absolute() else p.resolve()
        if not p.exists() or p in inside or p in outside:
            continue
        (inside if p != cwd and p.is_relative_to(cwd) else outside).append(p)
    return inside, outside


# Stdlib only: it runs isolated on the base interpreter, after DataForge exits.
_HELPER = r'''
import json, os, shutil, subprocess, sys, time

spec = json.loads(sys.argv[1])

def say(msg):
    print(msg, flush=True)

def wait_for(pid, timeout=30.0):
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if h:
            k.WaitForSingleObject(h, int(timeout * 1000))
            k.CloseHandle(h)
    else:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.2)

for pid in spec["wait_pids"]:
    wait_for(pid)
time.sleep(0.5)  # give Windows a moment to release file handles

say(spec["start"])
say("> " + " ".join(spec["cmd"]))
try:
    code = subprocess.call(spec["cmd"])
except FileNotFoundError:
    code = 127
    say(spec["cmd"][0] + " not found")

if code == 0:
    for p in spec.get("delete", []):
        try:
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            say("Deleted " + p)
        except OSError as exc:
            say("Could not delete " + p + ": " + str(exc))
    say(spec["ok"])
else:
    say(spec["fail"])
if spec.get("pause"):
    try:
        input("\nPress Enter to close this window.")
    except EOFError:
        pass
sys.exit(code)
'''


def hand_off(cmd: list[str], *, start: str, ok: str, fail: str,
             delete: list[Path] | None = None) -> None:
    """Run *cmd* once this process exits. The caller must exit right after.

    Only ever called for a person at a terminal (agents are refused before
    this). On POSIX this call does not return: the process becomes the helper.
    """
    python = getattr(sys, "_base_executable", None) or sys.executable
    spec: dict[str, Any] = {
        "cmd": cmd, "start": start, "ok": ok, "fail": fail,
        "delete": [str(p) for p in (delete or [])],
        "wait_pids": [], "pause": False,
    }
    if os.name != "nt":
        # A running program can be replaced here: exec keeps the output in
        # this terminal, and nothing waits on a stale process.
        args = [python, "-I", "-c", _HELPER, json.dumps(spec)]
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(python, args)

    # Windows: wait for this process and for the dataforge.exe launcher that
    # started it (it holds the launcher file until the interpreter exits).
    spec["wait_pids"] = [os.getpid()]
    try:
        import psutil
        parent = psutil.Process(os.getpid()).parent()
        if parent and parent.name().lower().startswith("dataforge"):
            spec["wait_pids"].append(parent.pid)
    except Exception:
        pass
    spec["pause"] = True  # the new window would otherwise vanish with the result
    subprocess.Popen(
        [python, "-I", "-c", _HELPER, json.dumps(spec)],
        creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=str(Path.home()), close_fds=True,
    )
