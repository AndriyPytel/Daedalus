"""
The worker image runs headless FreeCAD 1.1.4 and `sdk.Client` on Linux x86_64, built and run on the home server.

The server tests drive podman over ssh and skip when the server is unreachable. Every container, image and remote
directory they create is prefixed `dae-496-`; containers are removed in a `finally`, the image is kept as a layer cache.
"""
import json
import os
import re
import shlex
import subprocess
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER_DIR = os.path.join(ROOT, "deploy", "worker")
HOST = os.environ.get("DAE_SERVER", "andrii@192.168.0.150")
IMAGE = "dae-496-worker"
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


def harness_python():
    candidates = [os.environ.get("CADGENBENCH_VENV"), os.path.join(ROOT, "harness", ".venv", "bin", "python")]
    common = subprocess.run(["git", "-C", ROOT, "rev-parse", "--git-common-dir"], capture_output=True, text=True)
    if common.returncode == 0:
        main = os.path.dirname(os.path.abspath(os.path.join(ROOT, common.stdout.strip())))
        candidates.append(os.path.join(main, "harness", ".venv", "bin", "python"))
    return next((path for path in candidates if path and os.path.exists(path)), None)


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
    tar = subprocess.Popen(["tar", "--exclude=__pycache__", "-cf", "-", "-C", ROOT, "src", "deploy"],
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
    assert "FreeCAD_1.1.4-Linux-x86_64-py311.AppImage" in text
    assert re.search(r"\b[0-9a-f]{64}\b", text)
    assert "sha256sum" in text and "--appimage-extract" in text


def test_building_the_image_on_linux_x86_64_succeeds(image):
    done = ssh("podman image inspect %s --format '{{.Architecture}}'" % image)
    assert done.stdout.strip() == "amd64"


def test_freecadcmd_runs_a_script_and_freecad_imports_with_no_display(image):
    script = "import os,sys,FreeCAD; print('RESULT', 'DISPLAY' in os.environ, FreeCAD.Version()[:3])"
    done = run(image, "freecadcmd -c %s" % shlex.quote(script), "dae-496-cmd")
    assert "RESULT False ['1', '1', '4']" in done.stdout
    done = run(image, "python -c %s" % shlex.quote("import FreeCAD; print('IMPORT', FreeCAD.Version()[:3])"),
               "dae-496-import")
    assert "IMPORT ['1', '1', '4']" in done.stdout


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


def test_one_client_build_in_the_container_exports_a_step_the_validity_check_reads_valid(smoke):
    python = harness_python()
    if python is None:
        pytest.skip("the vendored scorer venv (harness/.venv) is absent")
    step, report = smoke
    assert report["ok"] is True and report["solids"] == 1
    check = ("import sys\nfrom cadgenbench.common.validity import analyze_step\n"
             "if __name__ == '__main__':\n"
             "    r = analyze_step(sys.argv[1])\n"
             "    print(r.validation.is_valid, r.validation.is_watertight, list(r.validation.topology_errors))\n")
    done = subprocess.run([python, "-c", check, step], capture_output=True, text=True, timeout=300)
    assert done.stdout.strip().splitlines()[-1] == "True True []", done.stdout + done.stderr


def test_the_gui_paths_are_disabled_not_served_by_xvfb(smoke, image):
    _, report = smoke
    assert report["gui"] == {"selection": [], "active_view": None, "review_views": []}
    done = ssh("podman run --rm --name dae-496-xvfb %s sh -c 'command -v Xvfb || echo none'" % image)
    assert done.stdout.strip() == "none"
    readme = _read("README.md")
    assert "Xvfb" in readme and "FreeCADGui" in readme and "disabled" in readme
