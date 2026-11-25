import httpx
import pytest
from skyguard.infrastructure import HTTPModel


def test_model_request_is_bounded_and_key_not_in_body(monkeypatch):
    real_client=httpx.Client;requests=[]
    def handler(request):
        requests.append(request)
        assert request.headers['Authorization']=='Bearer test-only'
        assert b'test-only' not in request.content
        return httpx.Response(200,json={'choices':[{'message':{'content':'请人工复核当前信息。'}}]})
    monkeypatch.setattr(httpx,'Client',lambda **kw:real_client(transport=httpx.MockTransport(handler),**kw))
    assert HTTPModel('https://model.example/v1','example','test-only').explain({'source':'synthetic'})=='请人工复核当前信息。'
    assert str(requests[0].url)=='https://model.example/v1/chat/completions'
