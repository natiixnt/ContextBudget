from .auth import login


def auth_middleware(token: str) -> bool:
    return login(token)
