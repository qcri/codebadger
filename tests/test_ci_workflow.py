"""CI must exercise the deployed HTTP MCP transport, not only the health route."""

import pathlib

import yaml

WORKFLOW = yaml.safe_load(pathlib.Path(".github/workflows/mcp-contract.yml").read_text())


def test_integration_job_calls_a_tool_over_http_after_the_health_probe():
    steps = [step.get("name", "") + " " + str(step.get("run", "")) for step in WORKFLOW["jobs"]["integration"]["steps"]]
    health = next(i for i, s in enumerate(steps) if "/health" in s)
    smoke = next(i for i, s in enumerate(steps) if "scripts/mcp_smoke.py" in s)
    assert smoke > health
    assert "http://127.0.0.1:4242/mcp" in steps[smoke]
