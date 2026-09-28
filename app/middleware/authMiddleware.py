from fastapi import (
    Depends,
    HTTPException,
    WebSocket,
    WebSocketException,
    status,
)

from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from app.config.security import (
    get_user_id_from_token,
    get_role_from_token,
)

security = HTTPBearer(auto_error=False)



# HTTP AUTHENTICATION
# ============================================================
async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:

    # Check if the request contains authentication credentials
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Make sure the authentication method is Bearer
    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        # Verify the JWT and get the user ID from the token
        return get_user_id_from_token(credentials.credentials)

    except ValueError as e:
        # Reject the request if the token is invalid or expired
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


# GET CURRENT USER ROLE
# ============================================================
async def get_current_user_role(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:

    # Check if the request contains authentication credentials
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Make sure the authentication method is Bearer
    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        # Verify the JWT and get the user's role from the token
        return get_role_from_token(credentials.credentials)

    except ValueError as e:
        # Reject the request if the token is invalid or expired
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_admin(
    role: str = Depends(get_current_user_role),
) -> str:
    if role.strip().lower() not in {"admin", "super_admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return role



# OPTIONAL USER AUTHENTICATION
# ============================================================
async def get_optional_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str | None:

    # No credentials means the user is not logged in
    # but the request is still allowed to continue
    if credentials is None:
        return None

    # Ignore credentials that do not use Bearer authentication
    if credentials.scheme.lower() != "bearer":
        return None

    try:
        # Verify the JWT and get the user ID
        return get_user_id_from_token(credentials.credentials)

    except ValueError:
        # Invalid or expired token is treated as an anonymous user
        return None


# WEBSOCKET AUTHENTICATION
# ============================================================
async def authenticate_websocket(websocket: WebSocket) -> str:

    # First, look for the JWT in the WebSocket URL
    # Example: /ws?token=YOUR_JWT
    token = websocket.query_params.get("token")

    # If no token was found in the URL,
    # check the Authorization header instead
    if not token:
        authorization = websocket.headers.get("authorization")

        if authorization:
            # Split "Bearer TOKEN" into two parts
            parts = authorization.split(" ", 1)

            # Make sure the format is "Bearer <token>"
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1]

    # Reject the WebSocket connection if no token was provided
    if not token:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Authentication required",
        )

    try:
        # Verify the JWT and get the user ID
        return get_user_id_from_token(token)

    except ValueError as e:
        # Reject the WebSocket if the JWT is invalid or expired
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=str(e),
        )
        

        