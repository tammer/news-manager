"""Verify Supabase-issued user JWTs (JWKS / ES256|RS256 or legacy HS256)."""

from __future__ import annotations

import logging
from functools import lru_cache

import jwt
from jwt import PyJWKClient

from news_manager.config import supabase_jwt_secret_optional, supabase_url_base

logger = logging.getLogger(__name__)

_ASYMMETRIC_ALGS = frozenset({"ES256", "RS256"})


@lru_cache(maxsize=4)
def _jwks_client_cached(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url)


def _auth_issuer_and_jwks_url() -> tuple[str, str]:
    base = supabase_url_base()
    if not base:
        raise jwt.InvalidTokenError(
            "Set SUPABASE_URL for JWKS verification (asymmetric JWT signing keys)."
        )
    issuer = f"{base}/auth/v1"
    jwks_url = f"{issuer}/.well-known/jwks.json"
    return issuer, jwks_url


def verify_supabase_jwt(token: str) -> dict:
    """
    Decode and validate a Supabase access token from the browser/client.

    - **ES256 / RS256** (JWT signing keys): fetch public keys from
      ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``.
    - **HS256** (legacy): verify with ``SUPABASE_JWT_SECRET``.

    Expects ``aud`` ``authenticated`` and required ``exp``, ``sub``.
    Raises jwt.PyJWTError on failure.
    """
    token = token.strip()
    if not token:
        raise jwt.DecodeError("empty token")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.DecodeError:
        raise
    alg = (header.get("alg") or "").upper()

    decode_options: dict = {"require": ["exp", "sub"]}

    if alg in _ASYMMETRIC_ALGS:
        issuer, jwks_url = _auth_issuer_and_jwks_url()
        try:
            jwks_client = _jwks_client_cached(jwks_url)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
        except jwt.PyJWTError:
            raise
        except Exception as e:
            logger.debug("JWKS fetch/signing key failed: %s", e)
            raise jwt.InvalidTokenError("Could not load signing key from JWKS.") from e
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
            issuer=issuer,
            options=decode_options,
        )

    if alg == "HS256":
        secret = supabase_jwt_secret_optional()
        if not secret:
            raise jwt.InvalidTokenError(
                "Token uses HS256; set SUPABASE_JWT_SECRET for verification."
            )
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
            options=decode_options,
        )

    raise jwt.InvalidTokenError(f"Unsupported JWT alg: {alg!r}")
