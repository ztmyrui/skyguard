from datetime import timedelta
import json

import httpx
import pytest

from skyguard.flight_model import FlightModel, FlightModelConfig
from skyguard.flight_rules import DEMO_START, FlightQuery


@pytest.fixture
def config():
    return FlightModelConfig('https://assistant.example/v1', 'my-model', 'test-key')


@pytest.fixture
def flight():
    return FlightQuery(((114.0579, 22.5431),), 'circle', 75, DEMO_START,
                       DEMO_START + timedelta(minutes=15), 70, '轻型', demo=False)


def _reply(content, **other_fields):
    return httpx.Response(200, json={'choices': [{'message': {
        'content': content, **other_fields,
    }}]})


def test_flight_model_sends_parameters_and_validates_advice(config, flight):
    def exchange(request):
        assert str(request.url) == 'https://assistant.example/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-key'
        assert request.extensions['timeout']['connect'] == 5.0
        assert request.extensions['timeout']['read'] == 25.0
        payload = json.loads(request.content)
        assert set(payload) == {'model', 'messages', 'response_format'}
        assert payload['model'] == 'my-model'
        assert payload['response_format'] == {'type': 'json_object'}
        context = json.loads(payload['messages'][1]['content'])
        assert context['城市'] == '深圳'
        assert context['最高离地米数'] == 70
        assert context['检索到的公开公告'][0]['原文链接'] == 'https://example.test/notice.html'
        assert '拟飞条件' not in context
        assert 'test-key' not in request.content.decode()
        return _reply('{"checks":["核对所选时段的官方空域资料。"]}', reasoning_content='仅供内部推理')

    assert FlightModel(config, httpx.MockTransport(exchange)).assess(
        '深圳', flight, notices=[{'原文链接': 'https://example.test/notice.html'}]
    ) == ('核对所选时段的官方空域资料。',)


@pytest.mark.parametrize(('checks', 'expected'), [
    ([
        {'item': ' 首选事项 ', 'title': '忽略', 'detail': ' 首选说明 ', '说明': '忽略', 'status': '忽略'},
        {'title': '标题', '说明': '说明'},
        {'事项': '中文事项', 'content': '正文'},
    ], ('首选事项：首选说明', '标题：说明', '中文事项：正文')),
    ([
        ' 读取公告 ', {'item': ' ', 'title': '时段', 'detail': None, 'text': '确认起止时间'},
        {'status': '忽略'}, '', '  ', None, 1, {'detail': ' 核对机场范围 '}, '超出的有效事项',
    ], ('读取公告', '时段：确认起止时间', '核对机场范围')),
    (['一', '二', '三', '四'], ('一', '二', '三')),
    (['甲' * 181, '乙' * 180, {'item': '标题', 'detail': '丙' * 190}],
     ('甲' * 179 + '…', '乙' * 180, '标题：' + '丙' * 176 + '…')),
], ids=['objects', 'mixed', 'three-item-limit', 'length-limit'])
def test_flight_model_normalizes_checks(config, flight, checks, expected):
    transport = httpx.MockTransport(lambda _: _reply(json.dumps({'checks': checks})))
    assert FlightModel(config, transport).assess('深圳', flight) == expected


def test_flight_model_keeps_actions_and_drops_disclaimers(config, flight):
    checks = ['当前记录不能视为飞行许可；请到 UOM 核对审批。',
              '公告要求活动期间禁止飞行。', '当前信息并不代表实际空域状态。']
    transport = httpx.MockTransport(lambda _: _reply(json.dumps({'checks': checks})))
    assert FlightModel(config, transport).assess('深圳', flight) == (
        '请到 UOM 核对审批。', '公告要求活动期间禁止飞行。',
    )


@pytest.mark.parametrize('content', [
    '说明中有 {占位符}，结果如下 {"checks":["检查 } 与 { 字符"]} 完毕。{"checks":["忽略"]}',
    '```json\n{"checks":["检查 } 与 { 字符"]}\n```',
    '下面是结果\n```\n{"checks":["检查 } 与 { 字符"]}\n```\n结束',
], ids=['surrounding-prose', 'json-fence', 'plain-fence'])
def test_flight_model_extracts_first_complete_object(config, flight, content):
    transport = httpx.MockTransport(lambda _: _reply(content))
    assert FlightModel(config, transport).assess('深圳', flight) == ('检查 } 与 { 字符',)


@pytest.mark.parametrize('content', [
    '', ' \n ', None, {'checks': ['非字符串']}, '{"checks": [}', '{"result":[]}',
    '{"result":[]} {"checks":["忽略"]}', '{"checks":"单条"}', '{"checks":[]}',
    '{"checks":[" ", {"status":"忽略"}]}',
], ids=['empty', 'whitespace', 'null', 'not-string', 'invalid-json', 'missing-checks',
        'first-object', 'not-list', 'empty-list', 'no-usable-checks'])
def test_flight_model_rejects_invalid_content(config, flight, content):
    transport = httpx.MockTransport(lambda _: _reply(content, reasoning_content='{"checks":["不可使用"]}'))
    with pytest.raises(ValueError, match='^模型回复格式无效。$'):
        FlightModel(config, transport).assess('深圳', flight)


@pytest.mark.parametrize('response', [
    httpx.Response(200, text='not JSON'),
    httpx.Response(200, json={'choices': []}),
    httpx.Response(200, json={'choices': [{'message': {'reasoning_content': '不可使用'}}]}),
], ids=['invalid-envelope', 'empty-choices', 'missing-content'])
def test_flight_model_rejects_invalid_response_envelope(config, flight, response):
    transport = httpx.MockTransport(lambda _: response)
    with pytest.raises(ValueError, match='^模型回复格式无效。$'):
        FlightModel(config, transport).assess('深圳', flight)


@pytest.mark.parametrize('second_status', [200, 400])
def test_flight_model_retries_json_mode_rejection_only_once(config, flight, second_status):
    requests = []

    def exchange(request):
        assert request.headers['authorization'] == 'Bearer test-key'
        requests.append(json.loads(request.content))
        if len(requests) > 1 and second_status == 200:
            return _reply('{"checks":["核对公告"]}')
        return httpx.Response(400, text='Unsupported parameter: response_format')

    provider = FlightModel(config, httpx.MockTransport(exchange))
    if second_status == 200:
        assert provider.assess('深圳', flight) == ('核对公告',)
    else:
        with pytest.raises(httpx.HTTPStatusError, match='HTTP 400'):
            provider.assess('深圳', flight)
    first, second = requests
    assert first.pop('response_format') == {'type': 'json_object'}
    assert first == second


@pytest.mark.parametrize(('status', 'body'), [(400, 'different error'), (401, 'response_format'), (302, '')])
def test_flight_model_does_not_retry_other_errors_or_follow_redirects(config, flight, status, body):
    requests = []

    def exchange(request):
        requests.append(request)
        return httpx.Response(status, headers={'Location': f'https://other.example/{config.key}'},
                              text=f'{body} {config.key}')

    with pytest.raises(httpx.HTTPStatusError, match=f'HTTP {status}') as error:
        FlightModel(config, httpx.MockTransport(exchange)).assess('深圳', flight)
    assert len(requests) == 1
    assert config.key not in str(error.value)


def test_flight_model_passes_custom_read_timeout_to_httpx(config, flight):
    def exchange(request):
        timeout = request.extensions['timeout']
        assert timeout['connect'] == 5.0
        assert timeout['read'] == 12.5
        return _reply('{"checks":["核对公告"]}')

    assert FlightModel(config, httpx.MockTransport(exchange), timeout=12.5).assess(
        '深圳', flight,
    ) == ('核对公告',)


@pytest.mark.parametrize('error_type', [httpx.ReadTimeout, httpx.ConnectError])
def test_flight_model_does_not_expose_key_in_request_errors(config, flight, error_type):
    def exchange(request):
        raise error_type(f'服务错误包含 {config.key}', request=request)

    with pytest.raises(error_type) as error:
        FlightModel(config, httpx.MockTransport(exchange)).assess('深圳', flight)
    assert config.key not in str(error.value)
    assert error.value.__suppress_context__
