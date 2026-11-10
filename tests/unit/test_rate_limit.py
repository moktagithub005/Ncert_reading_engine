from unittest.mock import Mock

from redis.exceptions import RedisError

from app.core import rate_limit


def test_rate_limit_allows_limit_then_rejects_overage(monkeypatch) -> None:
    client = Mock()
    client.incr.side_effect = [1, 2, 3]
    monkeypatch.setattr(rate_limit, "get_redis", lambda: client)

    assert rate_limit.allow_request("bucket", 2)
    assert rate_limit.allow_request("bucket", 2)
    assert not rate_limit.allow_request("bucket", 2)
    client.expire.assert_called_once_with("bucket", 60)


def test_rate_limit_fails_closed_when_redis_is_unavailable(monkeypatch) -> None:
    client = Mock()
    client.incr.side_effect = RedisError("redis unavailable")
    monkeypatch.setattr(rate_limit, "get_redis", lambda: client)

    assert not rate_limit.allow_request("bucket", 2)
