import keyring
import pytest
from keyring.backend import KeyringBackend

from fleck_helper import cli


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


@pytest.fixture
def kr():
    old = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())  # never the real or System keychain
    yield keyring.get_keyring()
    keyring.set_keyring(old)


class Resp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    def json(self):
        return self._b


def test_pair_stores_the_token_in_the_keychain(kr, monkeypatch):
    monkeypatch.setattr(cli.httpx, "post", lambda url, json, timeout: Resp(200, {"device_token": "secret"}))
    cli.pair("ABCD-1234")
    assert kr.store == {("ai.fleck.helper", "device_token"): "secret"} and cli.load_token() == "secret"


def test_pair_with_a_bad_code_stores_nothing(kr, monkeypatch):
    monkeypatch.setattr(cli.httpx, "post", lambda url, json, timeout: Resp(400, {"detail": "wrong or expired"}))
    with pytest.raises(SystemExit):
        cli.pair("ABCD-0000")
    assert kr.store == {}
