"""
Dashboard REST API integration tests.

Starts the dashboard server on a test port, uses testuser@test.com,
and exercises all API endpoints including auth, bot listing, bot control,
and data endpoints.

Run:  pytest tests/test_dashboard_api.py -v
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest
import requests
import yaml

# ── Config ───────────────────────────────────────────────────────────

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_PORT = 3099
TEST_ZMQ_PORT = 5599
BASE = f"http://localhost:{TEST_PORT}"
ZMQ_ENDPOINT = f"tcp://localhost:{TEST_ZMQ_PORT}"

TEST_EMAIL = "testuser@test.com"
TEST_USERNAME = "testuser"
TEST_PASSWORD = "testpass123"
USERS_CONFIG = os.path.join(PROJECT_ROOT, "config", "users.yaml")
TEST_USER_DIR = os.path.join(PROJECT_ROOT, "data", "testuser")
BOT_CONFIG_PATH = os.path.join(TEST_USER_DIR, "paper_btc.yaml")


# ── Helpers ──────────────────────────────────────────────────────────

def _wait_for_server(url, timeout=15):
    """Poll until the server is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(f"{url}/api/auth/me", timeout=1)
            return True
        except requests.ConnectionError:
            time.sleep(0.3)
    raise RuntimeError(f"Server did not start within {timeout}s")


def _reset_test_user_password():
    """Set testuser's password_hash to empty so set-password flow works."""
    with open(USERS_CONFIG) as f:
        cfg = yaml.safe_load(f)
    for u in cfg.get("users", []):
        if u["email"] == TEST_EMAIL:
            u["password_hash"] = ""
    with open(USERS_CONFIG, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)


def _ensure_bot_config():
    """Make sure the test bot config exists with the right ZMQ endpoint."""
    os.makedirs(TEST_USER_DIR, exist_ok=True)
    with open(BOT_CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    cfg["zmq"]["endpoint"] = ZMQ_ENDPOINT
    with open(BOT_CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def server():
    """Start the dashboard server for the entire test session."""
    _reset_test_user_password()
    _ensure_bot_config()

    env = {
        **os.environ,
        "DASHBOARD_PORT": str(TEST_PORT),
        "DASHBOARD_ZMQ_PORT": str(TEST_ZMQ_PORT),
    }
    proc = subprocess.Popen(
        ["node", "server/index.js"],
        cwd=os.path.join(PROJECT_ROOT, "dashboard"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    try:
        _wait_for_server(BASE)
    except RuntimeError:
        out = proc.stdout.read().decode() if proc.stdout else ""
        proc.kill()
        pytest.fail(f"Dashboard server failed to start.\nOutput:\n{out}")

    yield {"url": BASE, "zmq_port": TEST_ZMQ_PORT}

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def auth_token(server):
    """
    Set the testuser password (first-login flow) and return a valid token.
    Subsequent tests reuse this token.
    """
    # First login should say password not set
    r = requests.post(f"{BASE}/api/auth/login", json={"email": TEST_EMAIL, "password": ""})
    assert r.status_code == 403
    assert r.json()["error"] == "password_not_set"

    # Set password
    r = requests.post(f"{BASE}/api/auth/set-password", json={
        "email": TEST_EMAIL,
        "password": TEST_PASSWORD,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == TEST_USERNAME
    assert data["email"] == TEST_EMAIL
    assert "token" in data
    return data["token"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


# ── Auth Tests ───────────────────────────────────────────────────────

class TestAuth:
    def test_login_requires_email(self, server):
        r = requests.post(f"{BASE}/api/auth/login", json={})
        assert r.status_code == 400

    def test_login_unknown_user(self, server):
        r = requests.post(f"{BASE}/api/auth/login", json={
            "email": "nobody@example.com",
            "password": "secret",
        })
        assert r.status_code == 401

    def test_set_password_too_short(self, server):
        r = requests.post(f"{BASE}/api/auth/set-password", json={
            "email": TEST_EMAIL,
            "password": "abc",
        })
        assert r.status_code == 400
        assert "6 characters" in r.json()["error"]

    def test_set_password_and_login(self, auth_token):
        """auth_token fixture already tests set-password; verify login works now."""
        r = requests.post(f"{BASE}/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["username"] == TEST_USERNAME
        assert "token" in data

    def test_set_password_already_set(self, auth_token):
        r = requests.post(f"{BASE}/api/auth/set-password", json={
            "email": TEST_EMAIL,
            "password": "newpassword",
        })
        assert r.status_code == 400
        assert "already set" in r.json()["error"]

    def test_login_wrong_password(self, auth_token):
        r = requests.post(f"{BASE}/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": "wrongpassword",
        })
        assert r.status_code == 401

    def test_me_valid_token(self, auth_token):
        r = requests.get(f"{BASE}/api/auth/me", headers=_headers(auth_token))
        assert r.status_code == 200
        data = r.json()
        assert data["username"] == TEST_USERNAME
        assert data["email"] == TEST_EMAIL

    def test_me_no_token(self, server):
        r = requests.get(f"{BASE}/api/auth/me")
        assert r.status_code == 401

    def test_me_bad_token(self, server):
        r = requests.get(f"{BASE}/api/auth/me", headers=_headers("invalid_token"))
        assert r.status_code == 401

    def test_protected_api_requires_auth(self, server):
        r = requests.get(f"{BASE}/api/bots")
        assert r.status_code == 401

    def test_logout(self, server):
        # Get a fresh token
        r = requests.post(f"{BASE}/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        })
        token = r.json()["token"]

        # Verify it works
        r = requests.get(f"{BASE}/api/auth/me", headers=_headers(token))
        assert r.status_code == 200

        # Logout
        r = requests.post(f"{BASE}/api/auth/logout", headers=_headers(token))
        assert r.status_code == 200

        # Token should be revoked
        r = requests.get(f"{BASE}/api/auth/me", headers=_headers(token))
        assert r.status_code == 401


# ── Bot List Tests ───────────────────────────────────────────────────

class TestBotList:
    def test_list_bots(self, auth_token):
        r = requests.get(f"{BASE}/api/bots", headers=_headers(auth_token))
        assert r.status_code == 200
        bots = r.json()
        assert isinstance(bots, list)
        assert len(bots) >= 1
        names = [b["displayName"] or b["name"] for b in bots]
        assert any("Test BTC Paper" in n for n in names)

    def test_list_bots_only_own(self, server):
        """Bots from another user should not appear."""
        # Login as testuser
        r = requests.post(f"{BASE}/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        })
        token = r.json()["token"]

        r = requests.get(f"{BASE}/api/bots", headers=_headers(token))
        bots = r.json()
        for b in bots:
            assert b["owner"] == TEST_USERNAME, f"Bot {b['name']} owned by {b['owner']}, expected {TEST_USERNAME}"

    def test_get_single_bot(self, auth_token):
        # Find our bot's internal name first
        r = requests.get(f"{BASE}/api/bots", headers=_headers(auth_token))
        bots = r.json()
        assert len(bots) >= 1
        bot_name = bots[0]["name"]

        r = requests.get(
            f"{BASE}/api/bots/{requests.utils.quote(bot_name, safe='')}",
            headers=_headers(auth_token),
        )
        assert r.status_code == 200
        assert r.json()["name"] == bot_name

    def test_get_bot_not_found(self, auth_token):
        r = requests.get(
            f"{BASE}/api/bots/{requests.utils.quote('nonexistent:bot', safe='')}",
            headers=_headers(auth_token),
        )
        assert r.status_code == 404


# ── Bot Control Tests (with mock bot) ────────────────────────────────

class TestBotControl:
    @pytest.fixture(autouse=True)
    def _bot_name(self, auth_token):
        """Discover the test bot's internal name."""
        r = requests.get(f"{BASE}/api/bots", headers=_headers(auth_token))
        bots = r.json()
        self.bot_name = bots[0]["name"]
        self.encoded_name = requests.utils.quote(self.bot_name, safe="")
        self.token = auth_token

    def _bot_url(self, action=""):
        suffix = f"/{action}" if action else ""
        return f"{BASE}/api/bots/{self.encoded_name}{suffix}"

    def _start_mock_bot(self):
        """Start the mock bot process that connects to the test ZMQ port."""
        proc = subprocess.Popen(
            [sys.executable, os.path.join(PROJECT_ROOT, "tests", "mock_bot.py"),
             self.bot_name, ZMQ_ENDPOINT],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        time.sleep(1.5)
        return proc

    def _stop_mock_bot(self, proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_start_bot_via_api(self):
        """Start a bot through the API (spawns real swing_bot.py).
        We stop it immediately — just verify the API accepts the request."""
        r = requests.post(self._bot_url("start"), headers=_headers(self.token))
        # May succeed or fail depending on exchange availability; either is OK
        # as long as the API responds correctly
        assert r.status_code in (200, 400)

        # If it started, stop it
        if r.status_code == 200:
            time.sleep(1)
            requests.post(self._bot_url("stop"), headers=_headers(self.token))
            time.sleep(2)

    def test_mock_bot_connects_and_shows_running(self):
        """A mock bot connecting via ZMQ should make the dashboard show it as running."""
        proc = self._start_mock_bot()
        try:
            r = requests.get(self._bot_url(), headers=_headers(self.token))
            assert r.status_code == 200
            assert r.json()["status"] == "running"
        finally:
            self._stop_mock_bot(proc)
            time.sleep(1)

    def test_pause_and_resume(self):
        proc = self._start_mock_bot()
        try:
            # Pause
            r = requests.post(self._bot_url("pause"), headers=_headers(self.token))
            assert r.status_code == 200
            time.sleep(0.5)

            r = requests.get(self._bot_url(), headers=_headers(self.token))
            assert r.json()["paused"] is True

            # Resume
            r = requests.post(self._bot_url("resume"), headers=_headers(self.token))
            assert r.status_code == 200
            time.sleep(0.5)

            r = requests.get(self._bot_url(), headers=_headers(self.token))
            assert r.json()["paused"] is False
        finally:
            self._stop_mock_bot(proc)
            time.sleep(1)

    def test_exit_trade_command(self):
        proc = self._start_mock_bot()
        try:
            r = requests.post(self._bot_url("exit-trade"), headers=_headers(self.token))
            assert r.status_code == 200
            assert r.json()["ok"] is True
        finally:
            # Stop via API so the dashboard marks it stopped cleanly
            requests.post(self._bot_url("stop"), headers=_headers(self.token))
            self._stop_mock_bot(proc)
            time.sleep(1)
            self._wait_for_stopped()

    def _wait_for_stopped(self, timeout=10):
        """Poll until the bot status is stopped or crashed."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = requests.get(self._bot_url(), headers=_headers(self.token))
            status = r.json().get("status")
            if status in ("stopped", "crashed"):
                return status
            time.sleep(0.5)
        return None

    def test_pause_not_running(self):
        """Pause on a stopped bot should fail."""
        r = requests.post(self._bot_url("pause"), headers=_headers(self.token))
        assert r.status_code == 400

    def test_stop_not_running(self):
        r = requests.post(self._bot_url("stop"), headers=_headers(self.token))
        assert r.status_code == 400


# ── Data Endpoint Tests ──────────────────────────────────────────────

class TestDataEndpoints:
    @pytest.fixture(autouse=True)
    def _setup(self, auth_token):
        r = requests.get(f"{BASE}/api/bots", headers=_headers(auth_token))
        bots = r.json()
        self.bot_name = bots[0]["name"]
        self.encoded_name = requests.utils.quote(self.bot_name, safe="")
        self.token = auth_token

    def test_trades_empty(self):
        r = requests.get(
            f"{BASE}/api/bots/{self.encoded_name}/trades",
            headers=_headers(self.token),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_ohlcv_empty(self):
        r = requests.get(
            f"{BASE}/api/bots/{self.encoded_name}/ohlcv?range=1W",
            headers=_headers(self.token),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_supertrend_empty(self):
        r = requests.get(
            f"{BASE}/api/bots/{self.encoded_name}/supertrend?range=1W",
            headers=_headers(self.token),
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_logs(self):
        r = requests.get(
            f"{BASE}/api/bots/{self.encoded_name}/logs",
            headers=_headers(self.token),
        )
        assert r.status_code == 200
        data = r.json()
        assert "process" in data
        assert "python" in data

    def test_logs_download_not_found(self):
        r = requests.get(
            f"{BASE}/api/bots/{self.encoded_name}/logs/download?source=python",
            headers=_headers(self.token),
        )
        # Log file may not exist for test bot
        assert r.status_code in (200, 404)

    def test_cross_user_access_blocked(self, server):
        """Testuser should not be able to access yasmas's bots."""
        r = requests.post(f"{BASE}/api/auth/login", json={
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
        })
        token = r.json()["token"]

        # Try to access a yasmas bot by guessing the namespaced name
        r = requests.get(
            f"{BASE}/api/bots/{requests.utils.quote('yasmas:Swing Bot BTC (Paper 1h)', safe='')}",
            headers=_headers(token),
        )
        assert r.status_code == 404, "Should not be able to access another user's bot"
