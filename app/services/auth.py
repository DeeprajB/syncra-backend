from typing import Any, Dict

import httpx
from fastapi import Depends, HTTPException, status

from app.core.config import settings
from app.core.security import Auth0Credentials, get_auth0_credentials
from app.models.auth.user import User
from app.repositories import users as users_repository

def _profile_from_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Auth0 token is missing sub",
        )

    return {
        "sub": sub,
        "email": claims.get("email") or "",
        "given_name": claims.get("given_name") or "",
        "family_name": claims.get("family_name") or "",
        "picture": claims.get("picture") or "",
        "email_verified": bool(claims.get("email_verified", False)),
    }

async def _profile_from_userinfo(access_token: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            settings.auth0_userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code != 200:
        return {}

    return _profile_from_claims(response.json())

async def sync_current_user(
    credentials: Auth0Credentials = Depends(get_auth0_credentials),
) -> User:
    profile = _profile_from_claims(credentials.claims)

    if not profile["email"]:
        userinfo_profile = await _profile_from_userinfo(credentials.token)
        if userinfo_profile:
            profile = userinfo_profile

    return await users_repository.upsert_by_sub(profile)