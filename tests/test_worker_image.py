"""
The worker image runs headless FreeCAD 1.1.4 and `sdk.Client` on Linux x86_64, built and run on the home server.

The server tests drive podman over ssh and skip when the server is unreachable. Every container, image and remote
directory they create is prefixed `dae-496-`; containers are removed in a `finally`, the image is kept as a layer cache.
"""
import ast
import json
import os
import re
import subprocess
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER_DIR = os.path.join(ROOT, "deploy", "worker")
HOST = os.environ.get("DAE_SERVER", "andrii@192.168.0.150")
IMAGE = "dae-496-worker"
PRODUCT = "daedalus-worker"
REMOTE = "dae-496-build"

pytestmark = pytest.mark.timeout(2400)


def _read(name):
    with open(os.path.join(WORKER_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def ssh(command, timeout=2400, check=True):
    done = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", HOST, command],
                          capture_output=True, text=True, timeout=timeout)
    if check:
        assert done.returncode == 0, "%s\n%s%s" % (command, done.stdout[-3000:], done.stderr[-3000:])
    return done


@pytest.fixture(scope="module")
def server():
    done = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", HOST, "podman --version"],
                          capture_output=True, text=True)
    if done.returncode != 0:
        pytest.skip("home server %s unreachable over ssh: %s" % (HOST, done.stderr.strip()[-200:]))
    return HOST


@pytest.fixture(scope="module")
def image(server):
    ssh("rm -rf ~/%s/ctx && mkdir -p ~/%s/ctx" % (REMOTE, REMOTE))
    tar = subprocess.Popen(["tar", "--exclude=__pycache__", "-cf", "-", "-C", ROOT, "src", "config", "deploy"],
                           stdout=subprocess.PIPE)
    sent = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, "tar -xf - -C ~/%s/ctx" % REMOTE],
                          stdin=tar.stdout, capture_output=True)
    tar.stdout.close()
    assert tar.wait() == 0 and sent.returncode == 0, sent.stderr
    ssh("cd ~/%s/ctx && podman build -t %s -f deploy/worker/Containerfile ." % (REMOTE, IMAGE))
    return IMAGE


def run(image, args, name, extra=""):
    ssh("podman rm -f %s >/dev/null 2>&1" % name, check=False)
    try:
        return ssh("podman run --name %s %s %s %s" % (name, extra, image, args))
    finally:
        ssh("podman rm -f %s >/dev/null 2>&1" % name, check=False)


def test_the_containerfile_pins_the_official_appimage_by_version_and_sha256():
    text = _read("Containerfile")
    assert "ARG FREECAD_VERSION=1.1.4" in text and "Linux-x86_64-py311.AppImage" in text
    assert re.search(r"\b[0-9a-f]{64}\b", text)
    assert "sha256sum" in text and "--appimage-extract" in text


@pytest.fixture(scope="module")
def smoke(image):
    name = "dae-496-smoke"
    ssh("podman rm -f %s >/dev/null 2>&1; mkdir -p ~/%s/out" % (name, REMOTE), check=False)
    try:
        done = ssh("podman run --name %s %s python /app/deploy/worker/smoke.py /tmp/smoke.step" % (name, image))
        ssh("podman cp %s:/tmp/smoke.step ~/%s/out/smoke.step" % (name, REMOTE))
    finally:
        ssh("podman rm -f %s >/dev/null 2>&1" % name, check=False)
    step = os.path.join(tempfile.mkdtemp(prefix="dae-496-"), "smoke.step")
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "%s:%s/out/smoke.step" % (HOST, REMOTE), step], check=True)
    report = json.loads(done.stdout.strip().splitlines()[-1])
    return step, report


def test_the_product_image_is_not_named_after_the_ticket():
    readme = _read("README.md")
    assert PRODUCT in readme and "dae-496" not in readme
    assert "dae-496" not in _read("Containerfile")
