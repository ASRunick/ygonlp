import pytest


@pytest.fixture(autouse=True)
def forbid_real_http(monkeypatch):
    """テストで依存注入を忘れても実ネットワークへ出ないようにする。"""
    import ygonlp.collect as module

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("実ネットワーク通信は禁止されています")

    monkeypatch.setattr(module.httpx, "Client", ForbiddenClient)
