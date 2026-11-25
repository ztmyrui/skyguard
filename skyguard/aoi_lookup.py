"""Read an authorised AMap area boundary without storing its Web Service key."""
from dataclasses import dataclass
from math import isfinite
import re

import httpx


@dataclass(frozen=True)
class AmapArea:
    name: str
    classification: str
    code: str
    rings: tuple

    @property
    def kind(self):
        if re.search(r'小学|中学|幼儿园',self.classification):
            return 'school'
        if re.search(r'高校|大学|学院',self.classification):
            return 'university'
        if re.search(r'住宅小区|住宅区',self.classification):
            return 'residential'
        if re.search(r'商圈|购物|商场',self.classification):
            return 'mall'
        if re.search(r'公园|风景|景区',self.classification):
            return 'park'
        return 'open'


def lookup_area(identifier, key, *, transport=None):
    if not re.fullmatch(r'[A-Za-z0-9]{6,32}',identifier):
        raise ValueError('请填写高德地点编号')
    try:
        with httpx.Client(timeout=7,transport=transport) as client:
            response=client.get('https://restapi.amap.com/v5/aoi/polyline',params={'id':identifier,'key':key})
            response.raise_for_status()
            result=response.json()
    except (httpx.HTTPError, ValueError):
        raise ValueError('高德区域查询暂不可用') from None
    areas=result.get('aois')
    if str(result.get('status')) not in ('0','1') or not isinstance(areas,list) or not areas:
        raise ValueError('未取得区域边界，请核对地点编号和 AOI 权限')
    entry=areas[0]
    try:
        rings=tuple(tuple(tuple(map(float,pair.split(','))) for pair in group.split(';') if pair)
                    for group in entry['polyline'].split('_') if group)
        if not rings or sum(map(len,rings))>2000 or any(len(ring)<3 for ring in rings):
            raise ValueError
        if any(len(p)!=2 or not all(isfinite(v) for v in p) for ring in rings for p in ring):
            raise ValueError
        return AmapArea(entry['name'],entry.get('type',''),entry.get('typecode',''),rings)
    except (AttributeError,KeyError,TypeError,ValueError):
        raise ValueError('请核对高德区域轮廓') from None
