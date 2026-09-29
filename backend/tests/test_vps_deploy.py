"""Checks for the tracker.esterotoday.com VPS deploy.

Live checks stay off unless VPS_URL is set, so CI does not depend on the server.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_compose_binds_app_to_localhost_only():
    text = (REPO / "deploy" / "vps" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "127.0.0.1:8080:8080" in text
    assert '"8080:8080"' not in text


def test_actions_workflow_targets_vps_runner():
    text = (REPO / ".github" / "workflows" / "deploy-vps.yml").read_text(encoding="utf-8")
    assert "runs-on: [self-hosted, linux, engage-estero]" in text
    assert "sudo -n update-engage-estero" in text
    assert "ubuntu-latest" not in text


def test_update_script_finds_env_through_symlink(tmp_path):
    """The runner calls /usr/local/bin/update-engage-estero, a symlink into deploy/vps."""
    script = REPO / "deploy" / "vps" / "update.sh"
    link = tmp_path / "bin" / "update-engage-estero"
    link.parent.mkdir()
    link.symlink_to(script)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    id_cmd = fake_bin / "id"
    id_cmd.write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
    id_cmd.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run([str(link)], capture_output=True, text=True, env=env, check=False)

    message = result.stdout + result.stderr
    assert result.returncode != 0
    assert f"Missing {script.parent}/.env" in message


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "engage-estero-vps-test"})
    with urllib.request.urlopen(request, timeout=20) as response:
        assert response.status == 200
        return json.load(response)


@pytest.mark.skipif(not os.getenv("VPS_URL"), reason="set VPS_URL to smoke the deployed server")
def test_live_server_is_ready():
    base = os.environ["VPS_URL"].rstrip("/")
    health = _get_json(f"{base}/health")
    ready = _get_json(f"{base}/ready")
    assert health["status"] == "ok"
    assert health["chain_ready"] is True
    assert health["record_count"] > 0
    assert ready["status"] == "ready"
    assert ready["record_count"] == health["record_count"]
