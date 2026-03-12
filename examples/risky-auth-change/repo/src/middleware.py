from .auth import verify_token

def auth_middleware(token: str) -> bool:
    return verify_token(token)
