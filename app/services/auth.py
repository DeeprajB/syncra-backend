from typing import Any, Dict, Tuple

import httpx
from fastapi import Depends, HTTPException, status

from app.core.config import settings
from app.core.security import Auth0Credentials, get_auth0_credentials
from app.models.auth.user import User
from app.repositories import users as users_repository

def _split_name(claims: Dict[str, Any]) -> Tuple[str, str]:
    given_name = str(claims.get("given_name") or "").strip()
    family_name = str(claims.get("family_name") or "").strip()
    if given_name or family_name:
        return given_name, family_name

    full_name = str(claims.get("name") or "").strip()
    if not full_name or "@" in full_name:
        return "", ""

    given_name, _, family_name = full_name.partition(" ")
    return given_name.strip(), family_name.strip()


def _profile_from_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Auth0 token is missing sub",
        )

    given_name, family_name = _split_name(claims)
    return {
        "sub": sub,
        "email": str(claims.get("email") or "").strip(),
        "given_name": given_name,
        "family_name": family_name,
        "picture": str(claims.get("picture") or "").strip(),
        "email_verified": bool(claims.get("email_verified", False)),
    }


def _merge_profiles(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key in ("email", "given_name", "family_name", "picture"):
        if not merged.get(key) and extra.get(key):
            merged[key] = extra[key]
    if extra.get("email_verified"):
        merged["email_verified"] = True
    return merged

async def _profile_from_userinfo(access_token: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            settings.auth0_userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code != 200:
        return {}

    body = response.json()
    if not isinstance(body, dict) or not body.get("sub"):
        return {}

    return _profile_from_claims(body)

async def sync_current_user(
    credentials: Auth0Credentials = Depends(get_auth0_credentials),
) -> User:
    profile = _profile_from_claims(credentials.claims)
    profile_is_incomplete = (
        not profile["email"]
        or not (profile["given_name"] or profile["family_name"])
        or not profile["picture"]
    )

    if profile_is_incomplete:
        userinfo_profile = await _profile_from_userinfo(credentials.token)
        if userinfo_profile:
            profile = _merge_profiles(profile, userinfo_profile)

    return await users_repository.upsert_by_sub(profile)