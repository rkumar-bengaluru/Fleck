"""A keyring backend that keeps secrets in a JSON file ($FLECK_TEST_KEYRING), so the e2e run never touches the real keychain."""
import json
import os
from pathlib import Path

from keyring.backend import KeyringBackend


class FileKeyring(KeyringBackend):
    priority = 1

    def _path(self) -> Path:
        return Path(os.environ["FLECK_TEST_KEYRING"])

    def _load(self) -> dict:
        return json.loads(self._path().read_text()) if self._path().exists() else {}

    def get_password(self, service, username):
        return self._load().get(f"{service}/{username}")

    def set_password(self, service, username, password):
        self._path().write_text(json.dumps(self._load() | {f"{service}/{username}": password}))

    def delete_password(self, service, username):
        self._path().write_text(json.dumps({k: v for k, v in self._load().items() if k != f"{service}/{username}"}))
