from src.service import Service


def test_run() -> None:
    assert Service().run() == "ok"
