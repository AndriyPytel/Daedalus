"""
Results are stored as blobs and downloaded as artifacts, scoped by owner.

A fake worker (no FreeCAD) sends `blob` lines on the channel and the gateway writes them through the `BlobStore`.
Needs a real Postgres, skipped as in `test_service_postgres.py`. The FCStd open check runs `service.worker.artifacts`
with a scripted LLM in the worker image on the home server over ssh, and skips when the server is unreachable.
"""
import hashlib
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from service import db
from service.app import app
from service.blobstore import LocalBlobStore
from service.owner import current_owner
from service.supervisor import Worker
from test_service_postgres import _admin_url, conn, database_url, repo  # noqa: F401
from test_service_worker_channel import FAKE, ROOT, _ssh, until

STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
FCSTD = b"PK\x03\x04" + bytes(range(256)) * 20


@pytest.fixture
def store(tmp_path):
    return LocalBlobStore(str(tmp_path))


@pytest.fixture
def owners(repo):
    return repo.create_user(), repo.create_user()


@pytest.fixture
def api(database_url, tmp_path, monkeypatch, owners):
    monkeypatch.setenv(db.URL_VARIABLE, database_url)
    monkeypatch.setenv("DAEDALUS_BLOB_ROOT", str(tmp_path / "blobs"))
    monkeypatch.setenv("DAEDALUS_WORKER_ARGV", json.dumps(FAKE))
    app.dependency_overrides[current_owner] = lambda: owners[0]
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def run(database_url, repo, owners, tmp_path):
    session = repo.create_session(owners[0])
    worker = Worker(FAKE, database_url, owners[0], session["id"], blob_store=LocalBlobStore(str(tmp_path / "blobs")))
    worker.start()

    def send(prompt, attachments=()):
        run_id = worker.send(prompt, attachments=attachments)
        until(lambda: repo.get_run(owners[0], run_id)["state"] in ("completed", "failed"))
        return run_id

    send.session = session["id"]
    yield send
    worker.stop(timeout=3)


def row(conn, run_id, kind):
    return conn.execute("SELECT key, sha256, size FROM blobs WHERE run_id = %s AND kind = %s", (run_id, kind)).fetchone()


def test_downloading_a_runs_step_returns_a_file_whose_sha256_and_size_match_the_db_row(api, run, conn):
    run_id = run("make")
    key, sha256, size = row(conn, run_id, "step")
    body = api.get("/v1/runs/%s/step" % run_id)
    assert body.status_code == 200
    assert body.content == STEP
    assert (hashlib.sha256(body.content).hexdigest(), len(body.content)) == (sha256, size)
    assert key.startswith("session/%s/" % run.session)


def test_the_checkpoint_is_one_fcstd_per_run_keyed_session_ckpt_n(api, run, conn):
    first, second = run("make"), run("make")
    assert row(conn, first, "checkpoint")[0] == "session/%s/ckpt/1.FCStd" % run.session
    assert row(conn, second, "checkpoint")[0] == "session/%s/ckpt/2.FCStd" % run.session
    assert api.get("/v1/runs/%s/checkpoint" % first).content == FCSTD
    assert conn.execute("SELECT count(*) FROM blobs WHERE run_id = %s AND kind = 'checkpoint'", (first,)).fetchone()[0] == 1


def test_a_second_checkpoint_for_the_same_run_is_refused_by_the_database(repo, conn, owners):
    session = repo.create_session(owners[0])
    run_id = repo.create_run(owners[0], session["id"])["id"]
    repo.create_blob(owners[0], "session/s/ckpt/1.FCStd", "0" * 64, 1, "checkpoint", session["id"], run_id)
    with pytest.raises(Exception):
        repo.create_blob(owners[0], "session/s/ckpt/2.FCStd", "0" * 64, 1, "checkpoint", session["id"], run_id)


def test_a_run_with_no_step_has_no_step_route(api, run):
    assert api.get("/v1/runs/%s/step" % run("hello")).status_code == 404


def test_an_uploaded_attachment_reads_back_and_a_later_run_receives_it(api, run, conn):
    uploaded = api.post("/v1/blobs", params={"name": "drawing.png"}, content=b"\x89PNG-bytes")
    assert uploaded.status_code == 201
    blob = uploaded.json()
    assert blob["sha256"] == hashlib.sha256(b"\x89PNG-bytes").hexdigest() and blob["size"] == 10
    assert api.get("/v1/blobs/%s" % blob["id"]).content == b"\x89PNG-bytes"
    run_id = run("attachments", [blob["id"]])
    received = conn.execute("SELECT payload FROM events WHERE run_id = %s AND type = 'received'", (run_id,)).fetchone()[0]
    assert received == {"files": [{"name": "drawing.png", "sha256": blob["sha256"]}]}


def test_another_owners_blob_returns_404_on_every_route(api, run, repo, owners, store):
    stranger = api.post("/v1/blobs", params={"name": "a.png"}, content=b"x").json()
    run_id = run("make")
    app.dependency_overrides[current_owner] = lambda: owners[1]
    assert api.get("/v1/blobs/%s" % stranger["id"]).status_code == 404
    assert api.get("/v1/runs/%s/step" % run_id).status_code == 404
    assert api.get("/v1/runs/%s/checkpoint" % run_id).status_code == 404
    assert api.get("/v1/blobs/blob_missing").status_code == 404


def test_a_run_cannot_receive_another_owners_attachment(database_url, repo, owners, tmp_path, api):
    foreign = api.post("/v1/blobs", params={"name": "a.png"}, content=b"x").json()
    session = repo.create_session(owners[1])
    worker = Worker(FAKE, database_url, owners[1], session["id"], blob_store=LocalBlobStore(str(tmp_path / "blobs")))
    worker.start()
    try:
        with pytest.raises(LookupError):
            worker.send("attachments", attachments=[foreign["id"]])
    finally:
        worker.stop(timeout=3)


def test_an_uploaded_attachment_reaches_a_run_started_through_post_runs(api, conn):
    blob = api.post("/v1/blobs", params={"name": "drawing.png"}, content=b"\x89PNG-bytes").json()
    posted = api.post("/v1/runs", json={"prompt": "attachments", "attachments": [blob["id"]]})
    assert posted.status_code == 202 and posted.json()["attachments"] == [blob["id"]]
    until(lambda: api.get("/v1/runs/%s" % posted.json()["id"]).json()["state"] == "completed")
    received = conn.execute("SELECT payload FROM events WHERE run_id = %s AND type = 'received'", (posted.json()["id"],)).fetchone()[0]
    assert received == {"files": [{"name": "drawing.png", "sha256": blob["sha256"]}]}


def test_a_run_naming_an_unknown_or_another_owners_blob_is_404_problem_json_at_post_time(api, owners, conn):
    mine = api.post("/v1/blobs", params={"name": "a.png"}, content=b"x").json()
    unknown = api.post("/v1/runs", json={"prompt": "hello", "attachments": ["blob_missing"]})
    assert unknown.status_code == 404 and unknown.headers["content-type"].startswith("application/problem+json")
    app.dependency_overrides[current_owner] = lambda: owners[1]
    foreign = api.post("/v1/runs", json={"prompt": "hello", "attachments": [mine["id"]]})
    assert foreign.status_code == 404
    assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def test_a_missing_blob_for_another_owner_is_problem_json(api, owners):
    blob = api.post("/v1/blobs", params={"name": "a.png"}, content=b"x").json()
    app.dependency_overrides[current_owner] = lambda: owners[1]
    answer = api.get("/v1/blobs/%s" % blob["id"])
    assert answer.status_code == 404
    assert answer.headers["content-type"].startswith("application/problem+json") and answer.json()["status"] == 404


def test_with_no_blob_root_configured_a_blob_route_answers_503_problem_json(api, monkeypatch):
    monkeypatch.delenv("DAEDALUS_BLOB_ROOT")
    answer = api.post("/v1/blobs", params={"name": "a.png"}, content=b"x")
    assert answer.status_code == 503 and answer.headers["content-type"].startswith("application/problem+json")


def test_the_openapi_document_lists_the_blob_routes(api):
    paths = api.get("/openapi.json").json()["paths"]
    assert {"/v1/blobs", "/v1/blobs/{blob_id}", "/v1/runs/{run_id}/step", "/v1/runs/{run_id}/checkpoint"} <= set(paths)


@pytest.fixture(scope="module")
def artifact_image():
    from test_service_worker_channel import HOST, REMOTE
    probe = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", HOST, "podman --version"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("home server %s unreachable over ssh: %s" % (HOST, probe.stderr.strip()[-200:]))
    remote, image = "dae-502-build", "dae-502-worker"
    _ssh("rm -rf ~/%s/ctx && mkdir -p ~/%s/ctx" % (remote, remote))
    tar = subprocess.Popen(["tar", "--exclude=__pycache__", "-cf", "-", "-C", ROOT, "src", "config", "deploy"],
                           stdout=subprocess.PIPE)
    sent = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, "tar -xf - -C ~/%s/ctx" % remote],
                          stdin=tar.stdout, capture_output=True)
    tar.stdout.close()
    assert tar.wait() == 0 and sent.returncode == 0, sent.stderr
    _ssh("cd ~/%s/ctx && podman build -t %s -f deploy/worker/Containerfile ." % (remote, image), timeout=2400)
    return image


def test_the_checkpoint_the_worker_produces_is_an_fcstd_freecad_opens_and_the_step_has_a_solid(artifact_image):
    done = _ssh("podman run --rm --name dae-502-artifacts %s python /app/deploy/worker/artifacts_smoke.py" % artifact_image,
                timeout=600)
    report = json.loads(done.stdout.strip().splitlines()[-1])
    assert report["kinds"] == ["step", "checkpoint"]
    assert report["checkpoint_name"].endswith(".FCStd") and report["step_name"].endswith(".step")
    assert report["reopened_solids"] == 1
    assert report["step_header"] == "ISO-10303-21;"


def test_the_first_upload_by_an_owner_with_no_users_row_is_201(api, conn):
    app.dependency_overrides[current_owner] = lambda: "owner-fresh"
    response = api.post("/v1/blobs", params={"name": "a.txt"}, content=b"x")
    assert response.status_code == 201
    assert conn.execute("SELECT count(*) FROM users WHERE id = 'owner-fresh'").fetchone()[0] == 1
