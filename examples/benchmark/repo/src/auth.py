def login(token: str) -> bool:
    return token.startswith("prod_")
