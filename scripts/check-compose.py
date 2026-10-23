"""Check the one-command Compose contract (#33) with `docker compose config`.

Needs only the Docker CLI with Compose v2, not a running daemon. Exits non-zero
with a message on the first violation.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def config(profiles):
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        for name in ("docker-compose.yml", "Dockerfile"):
            shutil.copy(ROOT / name, project / name)
        shutil.copy(ROOT / ".env.example", project / ".env")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "SABOR_", "MEALIE_"))}
        env["COMPOSE_PROFILES"] = profiles
        result = subprocess.run(["docker", "compose", "config", "--format", "json"], cwd=project,
                                env=env, capture_output=True, text=True, check=False)
        if result.returncode:
            sys.exit(f"docker compose config failed for profiles={profiles!r}:\n{result.stderr}")
        return json.loads(result.stdout)


def require(condition, message):
    if not condition:
        sys.exit(f"compose contract violated: {message}")


def loopback_only(service):
    return all(port.get("host_ip") == "127.0.0.1" for port in service.get("ports", []))


bundled = config("mealie")["services"]
require(set(bundled) == {"sabor", "mealie"}, "the mealie profile must start sabor and mealie")
mealie, sabor = bundled["mealie"], bundled["sabor"]
require(mealie["image"].startswith("ghcr.io/mealie-recipes/mealie:v")
        and not mealie["image"].endswith(":latest"), "Mealie image must be a pinned version")
require(loopback_only(mealie) and loopback_only(sabor), "ports must bind to 127.0.0.1 only")
require(mealie["environment"].get("ALLOW_SIGNUP") == "false", "Mealie signup must stay closed")
require(any(v["target"] == "/app/data" and v["type"] == "volume" for v in mealie["volumes"]),
        "Mealie data must persist in a named volume")
require(any(v["target"] == "/data" and v["type"] == "volume" for v in sabor["volumes"]),
        "Sabor data must persist in a named volume")
require(sabor["depends_on"]["mealie"]["required"] is False, "mealie dependency must be optional")

standalone = config("")["services"]
require(set(standalone) == {"sabor"}, "without the profile only sabor may start")
require(loopback_only(standalone["sabor"]), "ports must bind to 127.0.0.1 only")
print("compose contract OK (bundled and standalone)")
