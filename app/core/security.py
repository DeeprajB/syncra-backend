from typing import Any, Dict, NamedTuple, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError, PyJWKClient

from app.core.config import settings

http_bearer = HTTPBearer()
_jwks_client: Optional[PyJWKClient] = None

class Auth0Credentials(NamedTuple):
    token: str
    claims: Dict[str, Any]

def get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(settings.auth0_jwks_url)
    return _jwks_client

def decode_auth0_token(token: str) -> Dict[str, Any]:
    try:
        signing_key = get_jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.auth0_audience,
            issuer=settings.auth0_issuer,
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Auth0 token",
        ) from exc

async def get_auth0_credentials(
    credentials: HTTPAuthorizationCredentials = Depends(http_bearer),
) -> Auth0Credentials:
    return Auth0Credentials(
        token=credentials.credentials,
        claims=decode_auth0_token(credentials.credentials),
    )