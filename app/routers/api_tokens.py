from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response, status
from pydantic import BaseModel, ConfigDict, Field

from .. import config
from ..api_tokens import (
    ApiTokenError,
    ApiTokenMetadata,
    ApiTokenPrincipal,
    api_token_http_exception,
    authenticate_api_token,
    extract_api_token,
    get_api_token_store,
)

router = APIRouter(tags=["API tokens"])


class IssueApiTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(min_length=1, max_length=3)
    expires_in_days: int = Field(default=90, ge=1, le=365)


class ApiTokenMetadataResponse(BaseModel):
    id: str
    name: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_by: str
    revoked_by: str | None


class IssuedApiTokenResponse(ApiTokenMetadataResponse):
    token: str


class ApiTokenIdentityResponse(BaseModel):
    id: str
    name: str
    scopes: list[str]
    expires_at: datetime | None
    is_legacy: bool


class TokenIssueEnvelope(BaseModel):
    data: IssuedApiTokenResponse
    meta: dict[str, bool]


class TokenListEnvelope(BaseModel):
    data: list[ApiTokenMetadataResponse]
    meta: dict[str, int]


class TokenIdentityEnvelope(BaseModel):
    data: ApiTokenIdentityResponse


def _authenticate_headers(
    authorization: str | None,
    x_api_key: str | None,
    required_scope: str | None,
) -> ApiTokenPrincipal:
    try:
        supplied = extract_api_token(authorization, x_api_key)
        return authenticate_api_token(
            supplied,
            required_scope=required_scope,
            legacy_key=config.api_key(),
        )
    except ApiTokenError as error:
        raise api_token_http_exception(error) from None


def require_any_api_token(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> ApiTokenPrincipal:
    return _authenticate_headers(authorization, x_api_key, required_scope=None)


def require_token_manager(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> ApiTokenPrincipal:
    return _authenticate_headers(
        authorization,
        x_api_key,
        required_scope="tokens:manage",
    )


def _metadata_response(token: ApiTokenMetadata) -> ApiTokenMetadataResponse:
    return ApiTokenMetadataResponse(
        id=token.id,
        name=token.name,
        scopes=list(token.scopes),
        created_at=token.created_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        revoked_at=token.revoked_at,
        created_by=token.created_by,
        revoked_by=token.revoked_by,
    )


@router.get("/v1/auth/token", response_model=TokenIdentityEnvelope)
def token_identity(
    principal: Annotated[ApiTokenPrincipal, Depends(require_any_api_token)],
) -> TokenIdentityEnvelope:
    return TokenIdentityEnvelope(
        data=ApiTokenIdentityResponse(
            id=principal.id,
            name=principal.name,
            scopes=sorted(principal.scopes),
            expires_at=principal.expires_at,
            is_legacy=principal.is_legacy,
        )
    )


@router.post(
    "/admin/api-tokens",
    response_model=TokenIssueEnvelope,
    status_code=status.HTTP_201_CREATED,
)
def issue_api_token(
    payload: IssueApiTokenRequest,
    principal: Annotated[ApiTokenPrincipal, Depends(require_token_manager)],
) -> TokenIssueEnvelope:
    try:
        issued = get_api_token_store().issue(
            name=payload.name,
            scopes=payload.scopes,
            expires_in_days=payload.expires_in_days,
            created_by=f"{principal.id}:{principal.name}",
        )
    except ApiTokenError as error:
        raise api_token_http_exception(error) from None
    return TokenIssueEnvelope(
        data=IssuedApiTokenResponse(
            **_metadata_response(issued).model_dump(),
            token=issued.token,
        ),
        meta={"secret_shown_once": True},
    )


@router.get("/admin/api-tokens", response_model=TokenListEnvelope)
def list_api_tokens(
    _principal: Annotated[ApiTokenPrincipal, Depends(require_token_manager)],
) -> TokenListEnvelope:
    tokens = [
        _metadata_response(token) for token in get_api_token_store().list_tokens()
    ]
    return TokenListEnvelope(data=tokens, meta={"count": len(tokens)})


@router.delete(
    "/admin/api-tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_api_token(
    principal: Annotated[ApiTokenPrincipal, Depends(require_token_manager)],
    token_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{12}$")],
) -> Response:
    revoked = get_api_token_store().revoke(
        token_id,
        revoked_by=f"{principal.id}:{principal.name}",
    )
    if not revoked:
        raise HTTPException(
            status_code=404,
            detail={"code": "TOKEN_NOT_FOUND", "message": "API token was not found"},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
