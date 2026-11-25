"""Durable local LangGraph workflow. Business writes remain in the application service."""
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .domain import Scenario
from .planning import build_plans


class State(TypedDict, total=False):
    scenario: dict
    analysis: dict
    plans: list
    gaps: list
    trace: list
    chosen: dict
    context: dict
    snapshot: dict


def _stage(state, role, result):
    return state.get('trace', []) + [{'role': role, 'result': result, 'clock': state['snapshot']['clock']}]


def intake(state: State):
    return {'trace': _stage(state, '信息整理', '保留原报告、数据来源和未知字段')}


def risk(state: State):
    view = state['analysis']
    return {'trace': _stage(state, '态势研判', f'{view["quality"]} · {view["priority"]} · 影响为相对指数')}


def planning(state: State):
    scene = Scenario.from_dict(state['scenario'])
    inputs = state['snapshot']
    result = build_plans(scene, state['analysis'], state['context'], inputs['params'], inputs)
    count = len(result['plans'])
    return {**result, 'trace': _stage(state, '协同筹划', f'生成 {count} 个不同安排')}


def review(state: State):
    if not state['plans']:
        return {'chosen': {}, 'trace': _stage(state, '复核归档', '记录具体筹划缺口')}
    answer = interrupt({'message': '请复核候选方案后确认', 'plan_count': len(state['plans'])})
    index = int(answer['plan_index'])
    if not 0 <= index < len(state['plans']):
        raise ValueError('无效方案编号')
    resumed = dict(state, snapshot=dict(state['snapshot'], clock=answer['clock']))
    return {'chosen': state['plans'][index], 'trace': _stage(resumed, '复核归档', '人工已确认')}


class Workflow:
    def __init__(self, path: Path):
        self.path = path

    def _graph(self, saver):
        graph = StateGraph(State)
        nodes = (intake, risk, planning, review)
        for node in nodes:
            graph.add_node(node.__name__, node)
        names = (START, *(node.__name__ for node in nodes), END)
        for current, following in zip(names, names[1:]):
            graph.add_edge(current, following)
        return graph.compile(checkpointer=saver)

    @staticmethod
    def _config(thread_id):
        return {'configurable': {'thread_id': thread_id}}

    def start(self, thread_id, scenario, view, context, snapshot):
        with SqliteSaver.from_conn_string(str(self.path)) as saver:
            return self._graph(saver).invoke(
                {'scenario': scenario, 'analysis': view, 'context': context, 'snapshot': snapshot},
                self._config(thread_id),
            )

    def resume(self, thread_id, index, clock):
        with SqliteSaver.from_conn_string(str(self.path)) as saver:
            graph, config = self._graph(saver), self._config(thread_id)
            saved = graph.get_state(config)
            if saved.values.get('chosen'):
                return saved.values
            return graph.invoke(Command(resume={'plan_index': index, 'clock': clock}), config)
