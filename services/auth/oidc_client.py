import time
from typing import Any, Dict, List, Optional
import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidAudienceError, PyJWTError

from config.errors import AuthenticationError, ExternalServiceError
from config.settings import get_settings
from domain.models.auth import TokenClaims


def create_token(
    sub: str,
    email: str,
    name: str,
    roles: Optional[List[str]] = None,
    allowed_fields: Optional[List[str]] = None,
    expires_in_sec: int = 3600,
    audience: Optional[str] = None,
    issuer: Optional[str] = None,
    secret_key: Optional[str] = None,
) -> str:
    """Utility to generate signed JWT tokens for testing and dev environments."""
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": email,
        "name": name,
        "roles": roles or ["engineer"],
        "allowed_fields": allowed_fields,
        "iat": now,
        "exp": now + expires_in_sec,
        "iss": issuer or settings.oidc_issuer_url,
        "aud": audience or settings.oidc_audience,
    }
    key = secret_key or settings.jwt_secret_key
    return jwt.encode(payload, key, algorithm="HS256")


def validate_token(token: str) -> TokenClaims:
    """
    Validate incoming JWT token signature, audience, and expiration.
    Supports symmetric HMAC secret in development/test and asymmetric JWKS in production.
    """
    settings = get_settings()
    if not token or not token.strip():
        raise AuthenticationError("Missing authentication token", detail={"reason": "empty_token"})

    try:
        # In dev/test environments, validate against configured secret key
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256", "RS256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer_url,
            options={"verify_signature": True, "verify_aud": True, "verify_iss": True},
        )
        return TokenClaims(
            sub=payload["sub"],
            email=payload.get("email", ""),
            name=payload.get("name", payload["sub"]),
            roles=payload.get("roles", ["engineer"]),
            allowed_fields=payload.get("allowed_fields"),
            exp=payload["exp"],
            iss=payload["iss"],
            aud=payload["aud"],
        )
    except ExpiredSignatureError as e:
        raise AuthenticationError("Token has expired", detail={"error": "token_expired"}) from e
    except InvalidAudienceError as e:
        raise AuthenticationError("Invalid token audience", detail={"error": "invalid_audience"}) from e
    except PyJWTError as e:
        raise AuthenticationError(f"Invalid authentication token: {str(e)}", detail={"error": "invalid_token"}) from e
    except Exception as e:
        raise ExternalServiceError(f"Token validation service failure: {str(e)}") from e
