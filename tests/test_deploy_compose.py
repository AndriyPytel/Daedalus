"""
`deploy/compose.yaml` is the home-server stack: Postgres, the gateway and a worker-host, wired so that only the gateway is reachable and only from this machine and Tailscale.

The assertions read the file and the tree: the roles the server defaults must cover come from `core.role_defaults`, and the modules the commands name are checked against `src/service/`.
"""
import json
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from core.role_defaults import ROLES  # noqa: E402
from core.model_catalog import CLAUDE  # noqa: E402

with open(os.path.join(ROOT, "deploy", "compose.yaml"), encoding="utf-8") as handle:
    COMPOSE = yaml.safe_load(handle)
SERVICES = COMPOSE["services"]


def text(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def module_exists(name):
    return os.path.isfile(os.path.join(ROOT, "src", *name.split(".")) + ".py")


def test_there_are_three_services_and_each_has_a_healthcheck():
    assert set(SERVICES) == {"postgres", "gateway", "worker-host"}
    for name, service in SERVICES.items():
        assert service["healthcheck"]["test"], name


def test_only_the_gateway_publishes_a_port_and_only_on_loopback_and_tailscale():
    assert [name for name, service in SERVICES.items() if service.get("ports")] == ["gateway"]
    hosts = {port.split(":")[0] for port in SERVICES["gateway"]["ports"]}
    assert hosts == {"127.0.0.1", "100.90.228.80"}
    assert all(port.endswith(":8000") for port in SERVICES["gateway"]["ports"])


def test_the_gateway_reaches_its_workers_through_the_relay_on_the_worker_host_port():
    argv = json.loads(SERVICES["gateway"]["environment"]["DAEDALUS_WORKER_ARGV"])
    assert argv[:3] == ["python", "-m", "service.relay"] and module_exists("service.relay")
    assert argv[3] == "worker-host:%s" % SERVICES["worker-host"]["environment"]["DAEDALUS_HOST_PORT"]
    assert SERVICES["worker-host"]["command"] == ["python", "-m", "service.workerhost"] and module_exists("service.workerhost")


def test_the_two_secrets_are_podman_secrets_that_become_environment_variables():
    assert all(secret == {"external": True} for secret in COMPOSE["secrets"].values())
    wanted = {"gateway": ("daedalus-token", "DAEDALUS_TOKEN"),
              "worker-host": ("daedalus-claude-token", "CLAUDE_CODE_OAUTH_TOKEN")}
    for name, (source, target) in wanted.items():
        assert SERVICES[name]["secrets"] == [{"source": source, "target": target, "type": "env"}]
    assert "ANTHROPIC_API_KEY" not in text("deploy", "compose.yaml")


def test_the_server_defaults_put_every_role_on_the_claude_subscription():
    defaults = json.loads(SERVICES["gateway"]["environment"]["DAEDALUS_DEFAULTS"])
    assert {row["role"] for row in defaults["role_table"]} == set(ROLES)
    assert {row["provider"] for row in defaults["role_table"]} == {CLAUDE}
    assert defaults["default_model"]["provider"] == defaults["naming_model"]["provider"] == CLAUDE


def test_state_lives_in_named_volumes():
    assert set(COMPOSE["volumes"]) == {"pgdata", "blobs"}
    mounted = {volume.split(":")[0] for service in SERVICES.values() for volume in service.get("volumes", ())}
    assert mounted == set(COMPOSE["volumes"])


def test_the_worker_image_pins_the_claude_cli_by_version_and_sha256():
    containerfile = text("deploy", "worker", "Containerfile")
    assert re.search(r"ARG CLAUDE_VERSION=\d+\.\d+\.\d+", containerfile) and "linux-x64/claude" in containerfile
    assert re.search(r"ARG CLAUDE_SHA256=[0-9a-f]{64}", containerfile) and "sha256sum -c" in containerfile


def test_the_gateway_image_carries_the_service_package_and_no_freecad():
    containerfile = text("deploy", "gateway", "Containerfile")
    assert "COPY src/service " in containerfile and "uvicorn" in containerfile
    assert not re.search(r"freecad|appimage|COPY src /", containerfile, re.IGNORECASE)


def test_the_build_context_is_limited_to_what_the_images_copy():
    allowed = {line[1:] for line in text(".containerignore").split() if line.startswith("!")}
    copied = {m.group(1).split("/")[0] for name in ("worker", "gateway")
              for m in re.finditer(r"^COPY (?!--from)(\S+)", text("deploy", name, "Containerfile"), re.MULTILINE)}
    assert copied <= allowed
