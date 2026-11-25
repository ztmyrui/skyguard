import json
import pytest
from skyguard.application import Service
from skyguard.civic import CivicService, default_staff
from skyguard.domain import demo
from skyguard.flight_rules import DEMO_START, demo_city
from skyguard.infrastructure import SQLiteRepository
from skyguard.workflow import Workflow


@pytest.fixture
def svc(tmp_path):return Service(SQLiteRepository(tmp_path/'business.db'),Workflow(tmp_path/'graph.db'))


def test_persisted_interrupt_resumes_after_restart(svc):
    pending = svc.generate('obs-a', '事件协同')
    assert 1 <= len(pending['plans']) <= 3
    restarted = Service(svc.repo, svc.workflow)
    assert restarted.pending['thread'] == pending['thread']
    adopted = restarted.confirm(0)
    tasks = restarted.scene.tasks[:]
    audit = restarted.repo.history()
    assert len(tasks) == 3 and adopted['confirmed']
    assert restarted.confirm(0) == adopted
    assert restarted.scene.tasks == tasks and restarted.repo.history() == audit
    assert Service(svc.repo, svc.workflow).confirm(0)['confirmed']


def test_confirmation_reports_all_staleness_reasons(svc):
    pending = svc.generate('obs-b', '事件协同')
    svc.event_change('obs-b', 'conflict')
    svc.set_person('p1', False)
    svc.set_activity('area-a', 540, False)
    svc.tick(121)
    reasons = svc.staleness(pending)
    with pytest.raises(ValueError, match='方案已过期') as error:
        svc.confirm(0)
    assert str(error.value).splitlines()[1:] == reasons
    for prefix in ('事件信息已更新', '人员状态变化', '地面活动已调整', '时间安排已过期'):
        assert any(reason.startswith(prefix) for reason in reasons)
    assert not svc.scene.tasks


def test_unrelated_revision_and_old_sixty_second_limit_do_not_invalidate(svc):
    pending = svc.generate('obs-b', '事件协同')
    svc.changed('无关操作')
    # Use a recent, static report whose event fingerprint stays stable.
    svc.scene.observations[1].observed_s = 0
    pending = svc.generate('obs-b', '事件协同')
    svc.tick(61)
    # The credibility change is independent of the age limit for the draft itself.
    assert '时间安排已过期' not in svc.staleness(pending)
    eid = svc.add_observation({'name': '位置未知的报告', 'lat': None, 'lon': None, 'observed_s': -100})
    pending = svc.generate(eid, '事件协同')
    svc.changed('无关操作')
    svc.tick(61)
    assert svc.staleness(pending) == []
    assert svc.confirm(0)['confirmed']


def test_tasks_require_predecessors_feedback_and_review(svc):
    svc.generate('obs-a','事件协同');svc.confirm(0)
    last=svc.scene.tasks[-1]
    with pytest.raises(ValueError,match='前置'):svc.task_transition(last['id'])
    for task in svc.scene.tasks:
        svc.task_transition(task['id'])
        with pytest.raises(ValueError):svc.task_transition(task['id'])
        svc.task_transition(task['id'],'演练核实结果')
        svc.task_transition(task['id'],'已复核')
        assert task['status']=='已完成'
    svc.close_event('obs-a','演练结束')
    assert not any(v['id']=='obs-a' for v in svc.views())


def test_import_is_atomic_and_duplicate_ids_rejected(svc):
    initial=len(svc.scene.observations)
    rows=[{'id':'new','name':'new','lat':None,'lon':None}, {'id':'obs-a','name':'duplicate','lat':None,'lon':None}]
    with pytest.raises(ValueError):svc.import_observations(json.dumps(rows),'json')
    assert len(svc.scene.observations)==initial
    assert svc.import_observations(json.dumps(rows[:1]),'json')==1
    assert svc.views()[-1]['impact'] is None


def test_incremental_plan_preserves_completed_steps_and_replaces_pending(svc):
    svc.generate('obs-a','事件协同');svc.confirm(0)
    first=svc.scene.tasks[0]
    for note in ('','结果','复核'):svc.task_transition(first['id'],note)
    svc.event_change('obs-a','conflict');svc.generate('obs-a','事件协同')
    assert all(t['name']!=first['name'] for p in svc.pending['plans'] for t in p['tasks'])
    svc.confirm(0)
    assert first['status']=='已完成'
    replaced = [task for task in svc.scene.tasks if task['status'] == '已替代']
    assert len(replaced) == 2 and all(task['history'][-1]['action'] == 'replace' for task in replaced)
    assert sum(t['status']=='待接单' for t in svc.scene.tasks)==2


def test_export_keeps_sources_and_never_claims_external_submission(svc):
    data=svc.export()
    assert '演练数据' in data['label']
    from skyguard import __version__
    assert data['version'] == __version__
    assert data['scene']['observations'][0]['source']=='synthetic'
    assert data['audit'][0]['detail'] == {'version': __version__}


def test_photography_window_respects_preparation_and_deadline(svc):
    svc.set_operation('活动全景',600,1500,300)
    p=svc.generate('obs-a','航拍辅助')['plans']
    assert p
    for plan in p:
        window=plan['operation_window']
        assert window['start_s'] >= max(600, *(t['end_s'] for t in plan['tasks']))
        assert window['end_s'] <= 1500
        assert plan['metrics']['activity_overlap_s'] is not None


def test_too_short_photography_window_has_no_feasible_plan(svc):
    svc.set_operation('活动全景',0,300,120)
    assert not svc.generate('obs-a','航拍辅助')['plans']


def test_legacy_lifecycle_migrates_and_is_saved_once(svc):
    legacy = svc.state()
    legacy['schema'] = 2
    legacy['closed'] = ['obs-b']
    legacy.pop('incidents')
    svc.repo.save(legacy)

    migrated = Service(svc.repo, svc.workflow)
    assert migrated.closed == {'obs-b'}
    assert migrated.incidents['obs-a']['status'] == '待核验'
    assert migrated.incidents['obs-c']['status'] == '待核验'
    history = migrated.incidents['obs-b']['history']
    assert len(history) == 1 and history[0]['note'] == '迁移自 v0.7'
    assert history[0]['at'] and history[0]['to'] == '已关闭'
    assert 'closed' not in svc.repo.load()
    assert Service(svc.repo, svc.workflow).incidents == migrated.incidents
    assert [view['id'] for view in migrated.views()] == ['obs-a', 'obs-c']
    archived = next(v for v in migrated.views(include_closed=True) if v['id'] == 'obs-b')
    assert archived['status'] == '已关闭' and archived['conclusion'] is None
    assert archived['lat'] is not None


@pytest.mark.parametrize('statuses, expected', [
    (('进行中', '待接单', '待接单'), '核验中'),
    (('已完成', '待接单', '已替代'), '核验中'),
    (('待复核', '已完成', '已替代'), '核验中'),
    (('已完成', '已完成', '已替代'), '待核验'),
])
def test_schema2_migration_restores_incidents_and_confirmed_work(tmp_path, statuses, expected):
    repo = SQLiteRepository(tmp_path / 'business.sqlite3')
    civic_repo = SQLiteRepository(tmp_path / 'civic.sqlite3')
    plan = {
        'id': 'flight-07', 'city': 'changsha', 'title': 'v0.7 保存的计划',
        'query': {'points': [[112.953, 28.1792]], 'form': 'circle', 'radius_m': 75,
                  'start': DEMO_START.isoformat(), 'end': '2026-11-19T15:35:00+08:00',
                  'altitude_agl': 70, 'category': '轻型', 'special': False,
                  'demo': True, 'special_unknown': False},
        'result': '涉及限制或需核对申请', 'cancelled': False,
    }
    civic_state = {'cities': {'changsha': demo_city()}, 'staff': default_staff(),
                   'plans': [plan], 'announcements': []}
    civic_repo.save(civic_state)

    scene = demo().to_dict()
    scene.update(clock=120, revision=4)
    scene['observations'].append({
        'id': 'field-07', 'name': '用户新增目击', 'description': 'v0.7 记录',
        'lat': 28.18, 'lon': 112.95, 'accuracy_m': 300,
        'source': 'field_observation', 'observed_s': 0, 'attachment': '',
    })
    thread = 'legacy-confirmed'
    draft_tasks = [
        dict(name=name, person_id=person_id, person=person, start=start, end=end,
             criterion='提交可追溯核实结果，由值班人员复核', status='草案')
        for name, person_id, person, start, end in (
            ('联系负责人核实', 'p2', '值班人员乙', 0, 3),
            ('补充独立观察', 'p1', '现场人员甲', 0, 5),
            ('复核证据与活动安排', 'p2', '值班人员乙', 5, 8),
        )
    ]
    scene['tasks'] = [
        dict(task, id=f'{thread}-{index}', event_id='obs-a', status=status,
             feedback='', plan_thread=thread)
        for index, (task, status) in enumerate(zip(draft_tasks, statuses))
    ]
    # The explicit legacy closure takes precedence even if a task is still active.
    scene['tasks'].append(dict(scene['tasks'][0], id='archived-task', event_id='obs-c',
                               status='待接单', plan_thread='legacy-closed'))
    repo.save({
        'schema': 2, 'scene': scene, 'closed': ['obs-c'], 'links': {}, 'settings': {},
        'pending': {'thread': thread, 'event_id': 'obs-a', 'revision': 1, 'clock': 0,
                    'confirmed': True, 'selected': 0, 'trace': [],
                    'plans': [{'mode': '事件协同', 'tasks': draft_tasks,
                               'minutes': 8, 'people_count': 2, 'changes': 0}]},
    })
    civic = CivicService(civic_repo)
    migrated = Service(repo, None, civic)

    assert migrated.incidents['obs-a']['status'] == expected
    event_history = migrated.incidents['obs-a']['history']
    if expected == '核验中':
        entry, = event_history
        assert entry['at']
        assert (entry['action'], entry['to'], entry['note']) == (
            'migrate', expected, '迁移自 v0.7，存在活跃任务')
    else:
        assert event_history == []
    assert migrated.closed == {'obs-c'}
    assert migrated.incidents['obs-c']['history'][0]['note'] == '迁移自 v0.7'
    assert migrated.incidents['obs-b']['status'] == '待核验'
    assert migrated.incidents['field-07']['status'] == '待核验'
    observation = next(item for item in migrated.scene.observations if item.id == 'field-07')
    assert (observation.source, observation.description) == ('field_observation', 'v0.7 记录')
    assert migrated.scene.clock == 120
    assert [(task['status'], task['start_s'], task['end_s']) for task in migrated.scene.tasks[:3]] == [
        (statuses[0], 0, 180), (statuses[1], 0, 300), (statuses[2], 300, 480),
    ]
    assert migrated.scene.tasks[2]['depends_on'] == ['contact', 'observe']
    assert migrated.pending['confirmed'] and migrated.pending['selected'] == 0
    assert CivicService(civic_repo).data['plans'] == []
    assert repo.load()['schema'] == 4
    from skyguard import __version__
    assert repo.history()[0]['detail'] == {'version': __version__}

    restarted = Service(repo, None, civic)
    assert restarted.state() == migrated.state()
    assert sum(row['action'] == '迁移筹划任务' for row in repo.history()) == 1


def test_added_and_imported_observations_start_as_new_reports(svc):
    eid = svc.add_observation({'name': '新的现场观察', 'lat': None, 'lon': None})
    rows = [{'id': 'imported-1', 'name': '导入观察', 'lat': None, 'lon': None}]
    assert svc.import_observations(json.dumps(rows), 'json') == 1
    views = {view['id']: view for view in svc.views()}
    assert views[eid]['status'] == views['imported-1']['status'] == '新报告'
    assert views[eid]['quality'] == '信息有限'
    assert svc.repo.history()[0]['detail']['events'] == ['imported-1']


@pytest.mark.parametrize('already_pending', [True, False])
def test_confirm_starts_verification_for_new_or_pending_incident(svc, already_pending):
    if already_pending:
        eid = 'obs-b'
    else:
        eid = svc.add_observation({'name': '补充观察', 'lat': 28.18, 'lon': 112.95})
    svc.generate(eid, '事件协同')
    svc.confirm(0)

    restarted = Service(svc.repo, svc.workflow)
    assert restarted.incidents[eid]['status'] == '核验中'
    assert all(task['event_id'] == eid for task in restarted.scene.tasks)
    auto = next(row for row in svc.repo.history() if row['action'] == '事件状态')
    assert auto['detail']['note'] == '确认方案后自动开始核验'
    assert auto['detail']['id'] == eid and auto['detail']['action'] == 'start'


def test_action_audit_close_compatibility_and_reopen(svc):
    conclusion = svc.incident_action('obs-b', 'conclude', '位置与报告一致', '已澄清')
    assert conclusion['status'] == '待复核'
    audit = svc.repo.history()[0]
    assert audit['action'] == '事件状态'
    assert audit['detail'] == {'id': 'obs-b', **conclusion['history'][-1], 'conclusion': '已澄清'}
    svc.close_event('obs-b', '复核记录齐全')
    svc.close_event('obs-a', '重复报告')
    assert svc.closed == {'obs-a', 'obs-b'}
    assert svc.incidents['obs-b']['history'][-1]['action'] == 'approve'
    assert svc.incidents['obs-a']['history'][-1]['action'] == 'force_close'
    assert next(v for v in svc.views(include_closed=True) if v['id'] == 'obs-b')['conclusion'] == '已澄清'
    svc.incident_action('obs-b', 'reopen', '收到补充观察')
    assert svc.closed == {'obs-a'}
    assert next(v for v in svc.views() if v['id'] == 'obs-b')['status'] == '待核验'


def test_open_tasks_reject_direct_close_and_approval_without_changing_audit(svc):
    svc.generate('obs-b', '事件协同')
    svc.confirm(0)
    before = svc.repo.history()
    with pytest.raises(ValueError, match='仍有未完成任务'):
        svc.close_event('obs-b', '演练结束')
    assert svc.repo.history() == before
    svc.incident_action('obs-b', 'conclude', '已联络', '已协同')
    before = svc.repo.history()
    with pytest.raises(ValueError, match='仍有未完成任务'):
        svc.incident_action('obs-b', 'approve')
    assert svc.repo.history() == before
    assert svc.incidents['obs-b']['status'] == '待复核'


def test_failed_plan_commit_preserves_pending_incident_and_can_retry(svc, monkeypatch):
    svc.generate('obs-b', '事件协同')
    revision = svc.scene.revision
    before = svc.repo.history()
    with monkeypatch.context() as patches:
        def fail(*_):
            raise OSError('暂时无法写入')

        patches.setattr(svc.repo, 'commit_many', fail)
        with pytest.raises(OSError, match='暂时无法写入'):
            svc.confirm(0)
    assert svc.scene.revision == revision
    assert not svc.scene.tasks and not svc.pending['confirmed']
    assert svc.incidents['obs-b']['status'] == '待核验'
    assert svc.repo.history() == before
    with pytest.raises(ValueError, match='复核记录与所选方案不一致'):
        svc.confirm(1)
    svc.confirm(0)
    assert len(svc.scene.tasks) == 3 and svc.incidents['obs-b']['status'] == '核验中'


def test_only_completed_steps_in_same_thread_satisfy_dependencies(svc):
    svc.generate('obs-b', '事件协同')
    svc.confirm(0)
    first, second, review = svc.scene.tasks
    first['status'] = '已完成'
    second['status'] = '已替代'
    svc.scene.tasks.append(dict(second, id='other', plan_thread='other', status='已完成'))
    with pytest.raises(ValueError, match='前置'):
        svc.task_transition(review['id'])
    second['status'] = '已完成'
    svc.task_transition(review['id'])
    assert review['status'] == '进行中'


def test_generation_preserves_specific_gaps_through_workflow(svc):
    pending = svc.generate('obs-a', '事件协同', selected_people=[])
    assert pending['plans'] == [] and '现场人员甲：未勾选' in pending['gaps'][0]
    assert pending['trace'][-1]['role'] == '复核归档'
    assert Service(svc.repo, svc.workflow).pending == pending


@pytest.mark.parametrize('remaining', ['待接单', '进行中', '待复核', None])
def test_unable_blocks_incident_only_when_no_other_active_task(svc, remaining):
    svc.generate('obs-b', '事件协同')
    svc.confirm(0)
    task, other, former = svc.scene.tasks
    other['status'] = remaining or '已完成'
    former['status'] = '已替代'
    before = len(svc.repo.history())
    result = svc.task_action(task['id'], 'unable', ' 无法到岗 ')
    assert result['status'] == '无法完成'
    audit = svc.repo.history()
    task_entry = next(entry for entry in audit if entry['action'] == '任务状态更新')
    assert task_entry['detail'] == {
        'id': task['id'], 'event': 'obs-b', 'status': '无法完成', **task['history'][-1],
    }
    incident = svc.incidents['obs-b']
    assert not svc.event_tasks_resolved('obs-b')
    if remaining is None:
        assert incident['status'] == '待核验' and len(audit) == before + 2
        assert incident['history'][-1]['note'] == '任务无法完成'
        assert audit[0]['detail']['action'] == 'block'
    else:
        assert incident['status'] == '核验中' and len(audit) == before + 1


def test_task_transition_keeps_old_review_note_and_task_action_allows_blank_approval(svc):
    svc.generate('obs-b', '事件协同')
    svc.confirm(0)
    first, second, last = svc.scene.tasks
    svc.task_transition(first['id'])
    svc.task_transition(first['id'], '观察反馈')
    with pytest.raises(ValueError, match='复核说明'):
        svc.task_transition(first['id'], ' ')
    svc.task_transition(first['id'], '通过')
    for task in (second, last):
        svc.task_action(task['id'], 'accept')
        svc.task_action(task['id'], 'submit', '记录已提交', '不适用')
        svc.task_action(task['id'], 'approve')
    assert svc.event_tasks_resolved('obs-b')
    assert svc.incidents['obs-b']['status'] == '核验中'
    assert svc.repo.history()[0]['detail']['action'] == 'approve'
    restarted = Service(svc.repo, svc.workflow)
    assert restarted.scene.tasks == svc.scene.tasks and restarted.event_tasks_resolved('obs-b')


def test_task_and_incident_roll_back_together_if_audit_cannot_commit(svc, monkeypatch):
    svc.generate('obs-b', '事件协同')
    svc.confirm(0)
    task, *other = svc.scene.tasks
    for item in other:
        item['status'] = '已完成'
    before = svc.repo.history()
    revision = svc.scene.revision

    def fail(*args):
        raise OSError('写入故障')

    with monkeypatch.context() as patches:
        patches.setattr(svc.repo, 'commit_many', fail)
        with pytest.raises(OSError, match='写入故障'):
            svc.task_action(task['id'], 'unable', '无法到岗')
    assert task['status'] == '待接单' and task['history'] == []
    assert svc.incidents['obs-b']['status'] == '核验中'
    assert svc.scene.revision == revision and svc.repo.history() == before
    svc.task_action(task['id'], 'unable', '无法到岗')
    assert svc.incidents['obs-b']['status'] == '待核验'
