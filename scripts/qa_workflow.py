"""P11 isolated desktop cases, using recorded and generated exercise data."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox
from qa_desktop import button, capture
from shapely.geometry import Point

from skyguard.aerial import CHINA
from skyguard.flight_route import tracking
from skyguard.flight_rules import FlightQuery
from skyguard.training_tracks import HOME


def p11(window, shots, result):
    from qa_training import map_visible
    service=window.context.training
    service.clock.stop()
    window.resize(1440,900)
    yield lambda:window.map_view.is_ready
    window.switch_role('duty')
    window.situation.open_training()
    training=window.situation.training
    panel=training.workflow
    service.load_example('商圈夜景')
    service.now=40
    service.return_now()
    assert service.selected_object()['attention'].level>=3
    training.objects.setCurrentRow(0)
    with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
        button(panel.card,'安排观察岗核验').click()
    assert any(r['kind']=='dispatch' for r in service.actions)
    result['商圈提示到派人']=3
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield lambda:map_visible(window)
        yield
        capture(window,shots,'处置_人员在路上',theme)
    service.close_event('合规飞行')
    assert [r['kind'] for r in service.actions][-1]=='close'
    original=service.key()
    service.seek(38)
    training.right.setCurrentIndex(2)
    button(panel.review,'从这里重来').click()
    assert original!=service.key() and service.comparison
    assert any(r['kind']=='close' for r in service.data['actions'][original])
    assert not any(r['kind']=='close' for r in service.actions)
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield lambda:map_visible(window)
        yield
        capture(window,shots,'复盘_分支对比',theme)
    result['分支保留原记录']=True

    service.load_example('风漂后修正')
    service.now=65
    service.return_now()
    obj=service.selected_object()
    assert obj['dev'] and obj['dev']['level'] not in ('容差内','等待确认')
    service.perform('call',result='已联系并确认')
    service.now=88
    service.return_now()
    obj=service.selected_object()
    assert obj['dev']['horizontal_m']<30
    service.tick()
    assert any(r['kind']=='returned' for r in service.actions)
    result['联系后回到航线']=True

    service.load_example('合规飞行')
    training.views.setCurrentIndex(2)
    panel.estimate()
    assert service.outlook
    window.resize(1280,800)
    yield
    assert panel.future_list.isVisible() and panel.future_list.width()>200
    window.resize(1440,900)
    yield
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield lambda:map_visible(window)
        yield
        capture(window,shots,'未来_报备与天气',theme)
    hint=service.outlook[0]
    panel.future_list.itemClicked.emit(panel.future_list.item(0))
    assert service.cursor==int((hint.at-service.frames.origin).total_seconds())
    result['未来提示时间定位']=True

    future_start=datetime.now(CHINA)+timedelta(minutes=25)
    future_path=tracking(HOME,90,200,65,start=future_start,category='轻型')
    future_query=FlightQuery((),'route',20,future_start,future_path.end,65,'轻型',demo=False,route=future_path)
    filings=[dict(id=f'test-{i}',state='已报备',snapshot=dict(
        city='changsha',title=f'查询样例 {i}',query=future_query.record())) for i in range(4)]
    official=SimpleNamespace(id='test-notice',name='验收区域',start=None,end=None,source='验收',
                             kind='restriction',verified=True,demo=False)
    book=SimpleNamespace(rules=(official,),shapes=(Point(*HOME).buffer(.003),))
    with patch.object(service.context.civic,'book',return_value=book), \
         patch.object(service,'outlook_filings',return_value=filings), \
         patch.object(service.context.weather_lookup,'configured',return_value=True), \
         patch.object(service.context,'run_aerial_lookup',side_effect=lambda _run,_ok,fail:fail()):
        service.estimate('本机报备')
    hotspot=next(hint for hint in service.outlook if hint.kind=='热点')
    service.publish_note(hotspot)
    assert service.data['public_notes'][-1]['demo'] is False
    assert any('暂无天气预报' in hint.reason for hint in service.outlook)
    result['本机报备天气异常时保留研判与区域提示']=True

    now=datetime.now(CHINA)
    path=tracking(HOME,90,600,65,start=now-timedelta(seconds=min(2,now.second)),category='轻型')
    query=FlightQuery((),'route',20,path.start,path.end,65,'轻型',demo=False,route=path)
    civic=window.context.civic
    pid=civic.save_plan('changsha','飞手端 · 模拟求助',query,'待核对',verdict='check')
    civic.public_filing(pid)
    civic.transition_plan(pid,'start',now)
    window.switch_page('my_plans')
    yield
    card=window.my_plans.cards[0]
    card.lost.click()
    service.sim_speed=1
    service.tick()
    window.switch_role('duty')
    window.situation.open_training()
    service.perform('nudge',result='请核对飞行状态')
    with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
        button(panel.card,'安排演练搜寻').click()
    rescue=next(r for r in service.actions if r['kind']=='search')
    service.finish_action(rescue['id'],'演练设备已找回')
    service.acknowledge()
    window.switch_role('public')
    window.switch_page('my_plans')
    yield
    card=window.my_plans.cards[0]
    assert card.messages.isVisible()
    button(card.messages,'正在处理').click()
    assert any(r['kind']=='reminder_reply' for r in service.actions)
    assert any(r['kind']=='request_ack' for _,r in service.pilot_messages(pid))
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield lambda:map_visible(window)
        yield
        capture(window,shots,'飞手端_提醒和回执',theme)
    result['失联求助回执']=True
    result['双主题截图']=8
    result['passed']=True
