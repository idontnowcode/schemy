"""API key storage in the operating system's credential store (plan v1.2 section 14).

Windows: Credential Manager, macOS: Keychain, Linux: Secret Service — through the
``keyring`` package. Keys never go into project files, the SQLite index or logs.
``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``) in the environment is used when set, which
also covers machines without a usable credential store.
"""

from __future__ import annotations

import os

SERVICE = "ai-circuit-studio"
ENV_VARS = {"gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}


class KeyStoreError(Exception):
    pass


class KeyStore:
    def __init__(self, service: str = SERVICE):
        self.service = service

    def _keyring(self):
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return None
        return keyring

    @property
    def available(self) -> bool:
        try:
            return self._keyring() is not None
        except Exception:  # noqa: BLE001 - a broken backend counts as unavailable
            return False

    def get(self, provider: str) -> tuple[str | None, str | None]:
        """(key, source) where source is 'env' or 'keyring'."""
        for var in ENV_VARS.get(provider, ()):
            if os.environ.get(var):
                return os.environ[var], "env"
        try:
            kr = self._keyring()
            if kr is not None:
                value = kr.get_password(self.service, f"{provider}-api-key")
                if value:
                    return value, "keyring"
        except Exception:  # noqa: BLE001
            pass
        return None, None

    def set(self, provider: str, api_key: str) -> None:
        kr = self._keyring()
        if kr is None:
            raise KeyStoreError("이 컴퓨터에서 OS 보안 저장소를 쓸 수 없습니다.")
        kr.set_password(self.service, f"{provider}-api-key", api_key)

    def delete(self, provider: str) -> bool:
        kr = self._keyring()
        if kr is None:
            return False
        try:
            kr.delete_password(self.service, f"{provider}-api-key")
            return True
        except Exception:  # noqa: BLE001 - nothing stored
            return False
