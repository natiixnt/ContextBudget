def verify_token(token: str) -> bool:
    return token.startswith("prod_")
