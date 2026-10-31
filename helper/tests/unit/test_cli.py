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


def test_claude_key_from_dotenv_goes_to_the_keychain_unprinted(kr, tmp_path, capsys, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FLECK_SECRET_KEY=x\nANTHROPIC_API_KEY='sk-ant-test-123'\n")
    cli.main(["set-claude-key", "--from-env", str(env)])
    assert kr.store[("ai.fleck.helper", "anthropic_api_key")] == "sk-ant-test-123"
    out = capsys.readouterr()
    assert "sk-ant" not in out.out + out.err and "Saved" in out.out
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.load_claude_key() is True
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test-123"


def test_environment_key_wins_and_missing_key_is_reported(kr, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    kr.store[("ai.fleck.helper", "anthropic_api_key")] = "from-keychain"
    assert cli.load_claude_key() is True
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "from-env"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    kr.store.clear()
    assert cli.load_claude_key() is False


def test_pair_never_stores_the_claude_key(kr, monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-pair\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.httpx, "post", lambda url, json, timeout: Resp(200, {"device_token": "secret"}))
    cli.pair("ABCD-1234")
    assert ("ai.fleck.helper", "anthropic_api_key") not in kr.store  # only set-claude-key stores it


def test_dotenv_parsing_matches_dotenv(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=old\n# ANTHROPIC_API_KEY=commented\nexport ANTHROPIC_API_KEY=sk-new  # rotated\n")
    assert cli._dotenv_key(env) == "sk-new"
    env.write_text('ANTHROPIC_API_KEY="sk-a #b" # quoted\n')
    assert cli._dotenv_key(env) == "sk-a #b"
    env.write_text("OTHER=1\n")
    assert cli._dotenv_key(env) == ""


def test_a_key_saved_after_launch_is_picked_up(kr, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.load_claude_key() is False  # launched without a key: the menu warns
    kr.store[("ai.fleck.helper", "anthropic_api_key")] = "sk-later"  # fleck-helper set-claude-key meanwhile
    assert cli.load_claude_key() is True  # the next menu refresh or recording start clears the warning


def test_replay_uploads_its_audio_in_10_s_chunks(tmp_path):
    import wave
    from fleck_helper.audio import AudioChunkWriter, ReplaySource
    with wave.open(str(tmp_path / "a.wav"), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x01\x00" * 2 * 16000 * 25)
    sent = []
    src = cli.UploadingSource(ReplaySource(tmp_path / "a.wav", realtime=False), AudioChunkWriter(tmp_path / "c"), lambda seq, p: sent.append((seq, p.exists())))
    assert len(list(src.frames())) == 1250
    assert sent == [(0, True), (1, True), (2, True)]  # two full chunks and the 5 s tail
