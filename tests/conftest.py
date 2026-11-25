"""Allow simulated HTTP transports and local map bridges during tests."""
import httpx
import pytest


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    local = {'127.0.0.1', 'localhost', '::1'}
    sync, asynchronous = httpx.Client.send, httpx.AsyncClient.send

    def permitted(client, request):
        if request.url.host not in local and not isinstance(client._transport_for_url(request.url), httpx.MockTransport):
            raise httpx.ConnectError('测试只使用本机或模拟 HTTP 响应', request=request)

    def send(client, request, *args, **kwargs):
        permitted(client, request)
        return sync(client, request, *args, **kwargs)

    async def send_async(client, request, *args, **kwargs):
        permitted(client, request)
        return await asynchronous(client, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, 'send', send)
    monkeypatch.setattr(httpx.AsyncClient, 'send', send_async)
