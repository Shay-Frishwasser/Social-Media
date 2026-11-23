from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from threading import Lock

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from brandai.db.base import utcnow
from brandai.db.brand_models import SocialConnection
from brandai.errors import GenerationBlockedError, NotFoundError
from brandai.store import BrandAIStore

from .models import ConnectionResponse, ConnectionWrite, OAuthComplete, OAuthState, TokenInput


TokenRefresher = Callable[[TokenInput], Awaitable[TokenInput]]


class TokenVault:
    def __init__(self, key: str):
        self.cipher = Fernet(key.encode())

    def seal(self, brand_id: str, connection_id: str, tokens: TokenInput) -> bytes:
        content = [brand_id, connection_id, tokens.access_token.get_secret_value(),
                   tokens.refresh_token.get_secret_value() if tokens.refresh_token else None,
                   tokens.expires_at.isoformat() if tokens.expires_at else None]
        return self.cipher.encrypt(json.dumps(content).encode())

    def open(self, brand_id: str, connection_id: str, ciphertext: bytes) -> TokenInput:
        try:
            brand, identifier, access, refresh, expiry = json.loads(self.cipher.decrypt(ciphertext))
            if (brand, identifier) != (brand_id, connection_id):
                raise InvalidToken
            return TokenInput(access_token=access, refresh_token=refresh, expires_at=expiry)
        except (InvalidToken, ValueError, TypeError) as exc:
            raise GenerationBlockedError("connection credentials could not be decrypted") from exc


class ConnectionService:
    def __init__(self, store: BrandAIStore, vault: TokenVault | None,
                 refreshers: dict[str, TokenRefresher] | None = None):
        self.store, self.vault = store, vault
        self.refreshers = refreshers or {}
        self._refresh_lock = Lock()

    @staticmethod
    def _row(session, identifier: str) -> SocialConnection:
        row = session.get(SocialConnection, identifier)
        if row is None:
            raise NotFoundError("social connection not found in this brand")
        return row

    @staticmethod
    def _view(row: SocialConnection) -> ConnectionResponse:
        return ConnectionResponse(id=row.id, provider=row.provider, platform=row.platform,
                                  account_id=row.account_id, label=row.label, has_token=row.ciphertext is not None,
                                  expires_at=row.expires_at.replace(tzinfo=UTC) if row.expires_at else None)

    def _save(self, brand_id: str, row: SocialConnection, tokens: TokenInput) -> None:
        if self.vault is None:
            raise GenerationBlockedError("configure the local OAuth encryption key before storing credentials")
        row.ciphertext = self.vault.seal(brand_id, row.id, tokens)
        row.expires_at = tokens.expires_at.astimezone(UTC) if tokens.expires_at else None

    def list(self, brand_id: str) -> list[ConnectionResponse]:
        with self.store.brand_session(brand_id) as session:
            rows = session.scalars(select(SocialConnection).order_by(SocialConnection.created_at, SocialConnection.id))
            return [self._view(row) for row in rows]

    def upsert(self, brand_id: str, request: ConnectionWrite) -> ConnectionResponse:
        with self.store.brand_session(brand_id, immediate=True) as session:
            row = session.scalar(select(SocialConnection).where(
                SocialConnection.provider == request.provider, SocialConnection.platform == request.platform,
                SocialConnection.account_id == request.account_id,
            ))
            if row is None:
                row = SocialConnection(provider=request.provider, platform=request.platform,
                                       account_id=request.account_id, label=request.label)
                session.add(row)
                session.flush()
            row.label = request.label
            if request.tokens:
                self._save(brand_id, row, request.tokens)
                row.oauth_state_hash = row.oauth_state_expires_at = None
            return self._view(row)

    def issue_state(self, brand_id: str, identifier: str) -> OAuthState:
        state = token_urlsafe(32)
        expires = utcnow() + timedelta(minutes=10)
        with self.store.brand_session(brand_id, immediate=True) as session:
            row = self._row(session, identifier)
            row.oauth_state_hash = sha256(state.encode()).hexdigest()
            row.oauth_state_expires_at = expires
        return OAuthState(state=state, expires_at=expires)

    def complete(self, brand_id: str, identifier: str, request: OAuthComplete) -> ConnectionResponse:
        with self.store.brand_session(brand_id, immediate=True) as session:
            row = self._row(session, identifier)
            submitted = sha256(request.state.get_secret_value().encode()).hexdigest()
            if (not row.oauth_state_hash or not row.oauth_state_expires_at
                    or row.oauth_state_expires_at.replace(tzinfo=UTC) <= utcnow()
                    or not hmac.compare_digest(submitted, row.oauth_state_hash)):
                raise GenerationBlockedError("OAuth state is invalid, expired or already used")
            self._save(brand_id, row, request)
            row.oauth_state_hash = row.oauth_state_expires_at = None
            return self._view(row)

    def credentials(self, brand_id: str, identifier: str) -> tuple[ConnectionResponse, TokenInput]:
        # Refresh serially in the single delivery worker process. The ciphertext check
        # prevents a concurrent owner update from being overwritten by an older refresh.
        with self._refresh_lock:
            with self.store.brand_session(brand_id) as session:
                row = self._row(session, identifier)
                connection, ciphertext = self._view(row), row.ciphertext
                if ciphertext is None or self.vault is None:
                    raise GenerationBlockedError("add OAuth credentials before dispatch")
                tokens = self.vault.open(brand_id, identifier, ciphertext)
            if tokens.expires_at is None or tokens.expires_at > utcnow() + timedelta(seconds=30):
                return connection, tokens
            refresh = self.refreshers.get(connection.provider)
            if refresh is None or tokens.refresh_token is None:
                raise GenerationBlockedError("OAuth credentials need renewal before dispatch")
            updated = asyncio.run(refresh(tokens))
            if updated.expires_at and updated.expires_at <= utcnow():
                raise GenerationBlockedError("the refreshed OAuth credential is already expired")
            if updated.refresh_token is None:
                updated = updated.model_copy(update={"refresh_token": tokens.refresh_token})
            with self.store.brand_session(brand_id, immediate=True) as session:
                row = self._row(session, identifier)
                if row.ciphertext != ciphertext:
                    raise GenerationBlockedError("OAuth credentials changed during refresh; retry with the current connection")
                self._save(brand_id, row, updated)
                return self._view(row), updated
