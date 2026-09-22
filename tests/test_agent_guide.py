"""The agent guide ships with the package and only names commands that exist."""
import re

from typer.testing import CliRunner

from dataforge.cli.app import app

runner = CliRunner()


def test_agent_guide_prints():
    result = runner.invoke(app, ["agent-guide"])
    assert result.exit_code == 0
    assert "guide for AI agents" in result.output


def test_agent_guide_commands_exist():
    guide = runner.invoke(app, ["agent-guide"]).output
    named = set(re.findall(r"`dataforge (?:--json |--quiet )*([a-z][a-z-]+)", guide))
    registered = {c.name or c.callback.__name__.replace("_", "-") for c in app.registered_commands}
    assert named, "guide names no commands"
    assert named <= registered, f"guide names unknown commands: {named - registered}"
