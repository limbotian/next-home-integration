"""Strict V6 polygon validation, independent of Home Assistant."""
from copy import deepcopy
import json
import logging
import math
import re
from .catalog import validate_catalog

_LOGGER = logging.getLogger(__name__)

MAX_BYTES = 256_000
# 顶层已知字段。未知字段**容忍并原样保留**（见 `fields()`），这里只用来打一行 debug 诊断：
# 真正有 bug 写出垃圾键时能查到，而不是让它在文档里静默堆积。
TOP_LEVEL_FIELDS = ('schemaVersion', 'id', 'name', 'revision', 'units', 'floors')
ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
AREA_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
EPS = 1e-8
# 世界坐标为无限网格，仅做有限性/量级校验；编辑钳制以前端画布可见区域为准。
COORD_LIMIT = 1000

def number(value, low, high, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label}必须在 {low}–{high} 之间")

def fields(value, expected, label):
    """必需字段齐全即通过；**多余字段一律容忍并原样保留**。

    为什么不要求「字段集合完全相等」（2026-10-25）：那样任何新增可选字段都要升
    `schemaVersion`，而升版本会让用户既有的 V6 户型作废（见
    openspec `plan-format-forward-compatible`）。只查缺失之后，加字段不需要升版本，
    未知字段随 `validate_plan` 返回的副本原样带回。

    判据没有消失：字段名写错（如 `rotation` → `rotaton`）会走「缺必需字段」这条路报错，
    所以文案必须点名缺了哪些字段。
    """
    if not isinstance(value, dict):
        raise ValueError(f"{label}字段不完整：应为对象")
    missing = [key for key in expected if key not in value]
    if missing:
        raise ValueError(f"{label}字段不完整：缺少 {'、'.join(missing)}")

PLACEMENT_REQUIRED = ('id','name','kind','x','y','width','depth','height','rotation','roomId','catalogId','surface','params','elevation','anchor')

def placement_fields(item):
    """布置字段：基础字段必填；绑定接受 deviceId（新）或 entityId（旧）之一。

    **多余的未知字段容忍并保留**（与 `fields()` 同一策略，理由见那里）。
    """
    if not isinstance(item, dict):
        raise ValueError('布置字段不完整：应为对象')
    keys = set(item)
    missing = [key for key in PLACEMENT_REQUIRED if key not in keys]
    if missing:
        raise ValueError(f"布置字段不完整：缺少 {'、'.join(missing)}")
    if 'deviceId' not in keys and 'entityId' not in keys:
        raise ValueError('布置需要绑定字段')

def polygon_area(points):
    area = 0.0
    for index, a in enumerate(points):
        b = points[(index + 1) % len(points)]
        area += a['x'] * b['y'] - b['x'] * a['y']
    return area / 2

def _cross(o, a, b):
    return (a['x'] - o['x']) * (b['y'] - o['y']) - (a['y'] - o['y']) * (b['x'] - o['x'])

def _on_segment(p, a, b):
    return (abs(_cross(a, b, p)) < 1e-6 and
            min(a['x'], b['x']) - 1e-6 <= p['x'] <= max(a['x'], b['x']) + 1e-6 and
            min(a['y'], b['y']) - 1e-6 <= p['y'] <= max(a['y'], b['y']) + 1e-6)

def _segments_cross(a1, a2, b1, b2):
    d1, d2 = _cross(b1, b2, a1), _cross(b1, b2, a2)
    d3, d4 = _cross(a1, a2, b1), _cross(a1, a2, b2)
    return ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and \
           ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS))

def _point_in_polygon(point, points):
    inside = False
    j = len(points) - 1
    for i, a in enumerate(points):
        b = points[j]
        if _on_segment(point, a, b):
            return 'boundary'
        if (a['y'] > point['y']) != (b['y'] > point['y']):
            x = (b['x'] - a['x']) * (point['y'] - a['y']) / (b['y'] - a['y']) + a['x']
            if point['x'] < x:
                inside = not inside
        j = i
    return 'inside' if inside else 'outside'

def polygon_self_intersects(points):
    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue
            if _segments_cross(points[i], points[(i + 1) % n], points[j], points[(j + 1) % n]):
                return True
    return False

def polygons_overlap(a, b):
    for i in range(len(a)):
        for j in range(len(b)):
            if _segments_cross(a[i], a[(i + 1) % len(a)], b[j], b[(j + 1) % len(b)]):
                return True
    return _point_in_polygon(a[0], b) == 'inside' or _point_in_polygon(b[0], a) == 'inside'

def _validate_room_v3(room, identity, checked):
    fields(room, ('id', 'name', 'areaId', 'points', 'wallHeight', 'wallThickness'), '房间')
    identity(room)
    if not isinstance(room['areaId'], str) or (room['areaId'] and not AREA_ID_PATTERN.fullmatch(room['areaId'])):
        raise ValueError("区域关联无效")
    if not isinstance(room['points'], list) or not 3 <= len(room['points']) <= 64:
        raise ValueError("房间多边形需要 3–64 个顶点")
    points = []
    for point in room['points']:
        fields(point, ('x', 'y'), '顶点')
        number(point['x'], -COORD_LIMIT, COORD_LIMIT, '顶点 X')
        number(point['y'], -COORD_LIMIT, COORD_LIMIT, '顶点 Y')
        points.append({'x': point['x'], 'y': point['y']})
    for index, point in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        if math.hypot(point['x'] - nxt['x'], point['y'] - nxt['y']) < 0.01:
            raise ValueError("房间多边形顶点重复或过近")
    if abs(polygon_area(points)) < 0.25:
        raise ValueError("房间面积不能小于 0.25 平方米")
    if polygon_self_intersects(points):
        raise ValueError("房间多边形不能自交")
    number(room['wallHeight'], 1, 8, 'wallHeight')
    number(room['wallThickness'], 0.05, 1, 'wallThickness')
    if any(polygons_overlap(points, other) for other in checked):
        raise ValueError("房间不能重叠，可以共用边界")
    checked.append(points)

def validate_plan(value):
    # 校验与归一（补绑定字段默认值）都在副本上进行，调用方传入的对象不被改写。
    value = deepcopy(value)
    try:
        size = len(json.dumps(value, allow_nan=False, ensure_ascii=False).encode())
    except (ValueError, TypeError) as error:
        raise ValueError("户型必须是有效 JSON") from error
    if size > MAX_BYTES:
        raise ValueError("户型文件超过 256 KB")
    fields(value, TOP_LEVEL_FIELDS, '户型')
    unknown = sorted(set(value) - set(TOP_LEVEL_FIELDS))
    if unknown:
        _LOGGER.debug("户型含当前版本不认识的顶层字段（原样保留）：%s", unknown)
    if type(value['schemaVersion']) is not int or value['schemaVersion'] != 6 or value['units'] != 'm':
        raise ValueError("不支持的户型版本或单位")
    number(value['revision'], 0, 2_147_483_647, '版本号')
    if type(value['revision']) is not int:
        raise ValueError("版本号必须是整数")
    ids = set()
    def identity(item):
        key = item['id']
        if not isinstance(key, str) or not ID_PATTERN.fullmatch(key) or key in ids:
            raise ValueError("ID 无效或重复")
        ids.add(key)
        if not isinstance(item['name'], str) or not 1 <= len(item['name'].strip()) <= 80:
            raise ValueError("名称需要 1–80 个字符")
    identity(value)
    if not isinstance(value['floors'], list) or not 1 <= len(value['floors']) <= 4:
        raise ValueError("户型需要 1–4 个楼层")
    for floor in value['floors']:
        fields(floor, ('id','name','elevation','height','rooms','walls','openings','placements'), '楼层')
        identity(floor)
        number(floor['elevation'], -10, 30, '楼层标高')
        number(floor['height'], 1, 8, '楼层高度')
        if not isinstance(floor['rooms'], list) or len(floor['rooms']) > 100:
            raise ValueError("每层最多 100 个房间")
        checked = []
        for room in floor['rooms']:
            _validate_room_v3(room, identity, checked)
        for key in ('walls','openings','placements'):
            if not isinstance(floor[key],list) or len(floor[key])>500: raise ValueError('墙体、门窗或布置数量超限')
        walls={}
        for wall in floor['walls']:
            fields(wall,('id','name','x1','y1','x2','y2','height','thickness','kind'),'墙体'); identity(wall)
            for key in ('x1','x2'): number(wall[key],-COORD_LIMIT,COORD_LIMIT,key)
            for key in ('y1','y2'): number(wall[key],-COORD_LIMIT,COORD_LIMIT,key)
            number(wall['height'],1,8,'墙高'); number(wall['thickness'],0.05,1,'墙厚')
            if wall['kind'] not in ('solid','virtual'): raise ValueError('墙体类型无效')
            length=math.hypot(wall['x2']-wall['x1'],wall['y2']-wall['y1'])
            if length<0.1: raise ValueError('墙长不足 0.1 米')
            # 墙体允许交叉与共线重叠：完全共线的墙在提交前由前端合并，这里不再校验两两不重叠。
            walls[wall['id']] = (wall,length)
        openings=[]
        for item in floor['openings']:
            fields(item,('id','name','wallId','kind','offset','width','height','sill','catalogId','surface','params'),'门窗'); identity(item)
            if not isinstance(item['wallId'],str) or item['wallId'] not in walls or item['kind'] not in ('door','window'): raise ValueError('门窗墙体无效')
            wall,length=walls[item['wallId']]
            if wall['kind'] != 'solid': raise ValueError('门窗只能添加到实体墙')
            validate_catalog(item, item['kind'])
            for key,low,high in [('offset',0,100),('width',0.1,20),('height',0.1,8),('sill',0,8)]: number(item[key],low,high,key)
            if item['offset']+item['width']>length+1e-8 or item['height']+item['sill']>wall['height']+1e-8: raise ValueError('门窗超出墙体')
            if any(o['wallId']==item['wallId'] and o['offset']<item['offset']+item['width']-1e-8 and item['offset']<o['offset']+o['width']-1e-8 for o in openings): raise ValueError('门窗重叠')
            openings.append(item)
        for item in floor['placements']:
            placement_fields(item); identity(item)
            if item['kind'] not in ('furniture','device'): raise ValueError('布置类型无效')
            validate_catalog(item, item['kind'])
            number(item['elevation'], 0, 8, '离地高度')
            if item['surface'] == 'floor' and (item['elevation'] != 0 or item['anchor'] is not None):
                raise ValueError('地面布置必须落地且没有墙锚点')
            anchor = item['anchor']
            anchor_wall = None
            if anchor is not None:
                fields(anchor, ('wallId', 'offset'), '布置墙锚点')
                if not isinstance(anchor['wallId'], str) or anchor['wallId'] not in walls or item['surface'] != 'wall':
                    raise ValueError('布置墙锚点无效')
                anchor_wall, anchor_length = walls[anchor['wallId']]
                if anchor_wall['kind'] != 'solid': raise ValueError('布置墙锚点无效')
                number(anchor['offset'], 0, anchor_length, '沿墙偏移')
            if item['surface'] == 'wall' and anchor is None: raise ValueError('墙面布置需要锚点')
            for key,low,high in [('x',-COORD_LIMIT,COORD_LIMIT),('y',-COORD_LIMIT,COORD_LIMIT),('width',0.1,20),('depth',0.1,20),('height',0.1,20),('rotation',0,360)]: number(item[key],low,high,key)
            if item['surface'] == 'wall':
                if item['elevation'] + item['height'] > anchor_wall['height'] + 1e-8:
                    raise ValueError('墙面布置超出墙高')
                if item['rotation'] not in (0, 180):
                    raise ValueError('墙面布置旋转只能是 0 或 180 度')
            if 'deviceId' in item and (not isinstance(item['deviceId'], str) or (item['deviceId'] and not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', item['deviceId']))): raise ValueError('设备 ID 无效')
            if 'entityId' in item and (not isinstance(item['entityId'], str) or (item['entityId'] and not re.fullmatch(r'[a-z0-9_]+\.[a-z0-9_]+', item['entityId']))): raise ValueError('实体 ID 无效')
            if not isinstance(item['roomId'],str) or (item['roomId'] and item['roomId'] not in {r['id'] for r in floor['rooms']}): raise ValueError('房间关联无效')
            # 解析阶段补空默认值：旧文档只带 entityId 也能正常加载与保存。
            item.setdefault('deviceId', '')
            item.setdefault('entityId', '')
    return deepcopy(value)
