"""`python -m backend` as the desktop shell runs it: ready line, token, exit on stdin EOF."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

from .conftest import SYMBOL_DIR


def start(tmp_path, *args, token=None):
    env = {**os.environ, "AICS_SYMBOL_DIRS": str(SYMBOL_DIR), "AICS_KICAD_CLI": "/nonexistent/kicad-cli"}
    env.pop("AICS_TOKEN", None)
    if token:
        env["AICS_TOKEN"] = token
    return subprocess.Popen(
        [sys.executable, "-m", "backend", "--workspace", str(tmp_path), *args],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )


def get(url, token=None):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read())


def test_refuses_to_start_without_token(tmp_path):
    proc = start(tmp_path)
    assert proc.wait(timeout=30) == 2
    assert "AICS_TOKEN is not set" in proc.stderr.read()


def test_ready_line_token_and_stdin_shutdown(tmp_path):
    proc = start(tmp_path, "--exit-on-stdin-eof", token="s3cret")
    try:
        ready = json.loads(proc.stdout.readline())
        assert ready["event"] == "ready"
        assert ready["url"] == f"http://127.0.0.1:{ready['port']}"
        status, body = get(ready["url"] + "/api/system", token="s3cret")
        assert status == 200 and body["workspace"] == str(tmp_path)
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(ready["url"] + "/api/system")
        assert exc.value.code == 401
        status, root = get(ready["url"] + "/")  # UI path needs no token
        assert status == 200
    finally:
        proc.stdin.close()
    assert proc.wait(timeout=30) == 0
