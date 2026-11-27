import asyncio
from apps.api.body_limit import BodyLimitMiddleware


def test_actual_asgi_chunks_are_limited_before_parser_or_storage():
    called = []
    messages = iter([{"type": "http.request", "body": b"a" * 8, "more_body": True},
                     {"type": "http.request", "body": b"b" * 8, "more_body": False}])
    sent = []

    async def downstream(scope, receive, send):
        called.append(True)

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    asyncio.run(BodyLimitMiddleware(downstream, limit=10)(
        {"type": "http", "headers": []}, receive, send))
    assert not called
    assert sent[0]["status"] == 413
