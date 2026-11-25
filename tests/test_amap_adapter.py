import json
import httpx
import pytest
from pathlib import Path
from skyguard.amap_adapter import MapServer,MapCredentials,CredentialStore

@pytest.fixture
def adapter():
    requests=[]
    def upstream(request):requests.append(request);return httpx.Response(200,json={'status':'1'})
    server=MapServer(Path(__file__).parents[1]/'skyguard'/'assets',MapCredentials('a'*32,'b'*32),b'channel',httpx.MockTransport(upstream))
    yield server,requests
    server.close()

def test_config_never_exposes_security_code(adapter):
    server,_=adapter
    with httpx.Client(trust_env=False) as c:
        result=c.get(server.origin+'/'+server.token+'/config.json')
        assert result.json()['configured'];assert 'b'*32 not in result.text
        assert result.headers['Cache-Control']=='no-store'
        assert c.get(server.origin+'/config.json').status_code==404
        assert c.get(server.origin+'/'+server.token+'/amap_adapter.py').status_code==404

def test_proxy_restricts_origin_referer_and_injects_only_own_secrets(adapter):
    server,requests=adapter
    with httpx.Client(trust_env=False) as c:
        url=server.origin+'/_AMapService/v3/assistant/coordinate/convert?key=wrong&jscode=wrong'
        assert c.get(url).status_code==403
        assert c.get(url,headers={'Referer':server.url,'Origin':'https://other.test'}).status_code==403
        response=c.get(url,headers={'Referer':server.url})
        assert response.status_code==200
        assert requests[-1].url.host=='restapi.amap.com'
        assert requests[-1].url.params['key']=='a'*32
        assert requests[-1].url.params['jscode']=='b'*32

def test_proxy_cannot_be_used_as_arbitrary_url_fetcher(adapter):
    server,requests=adapter
    with httpx.Client(trust_env=False) as c:
        assert c.get(server.origin+'/_AMapService/https://other.test/',headers={'Referer':server.url}).status_code==404
    assert requests==[]

def test_encrypted_credentials_roundtrip_and_session_only_clears_file(tmp_path,monkeypatch):
    monkeypatch.delenv('SKYGUARD_AMAP_KEY',raising=False);monkeypatch.delenv('SKYGUARD_AMAP_SECURITY_CODE',raising=False)
    store=CredentialStore(tmp_path);value=MapCredentials('a'*32,'b'*32)
    store.save(value,True)
    assert b'a'*32 not in store.path.read_bytes() and b'b'*32 not in store.path.read_bytes()
    assert store.load().code==value.code
    store.save(value,False);assert not store.path.exists()

def test_invalid_configuration_does_not_echo_input():
    with pytest.raises(ValueError) as error:MapCredentials('private user text','').validate()
    assert 'private user text' not in str(error.value)

def test_map_page_never_receives_attachment_or_statement():
    from skyguard.amap_adapter import display_payload
    result=display_payload({'observations':[{'id':'a','lat':28.1,'lon':112.9,'description':'private statement','attachment':'private file'}],
            'areas':[{'id':'b','description':'private activity'}],'grid':[]})
    assert 'private' not in json.dumps(result)
