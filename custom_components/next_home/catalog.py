"""Persisted catalog contract. Keep parity with frontend catalog.ts using shared fixtures."""
import math
import re

# 目录允许的放置面；门窗固定 wall，家具固定 floor，设备按安装方式声明。
SURFACES = {
    'door/single': ('wall',), 'door/double': ('wall',), 'door/sliding': ('wall',),
    'window/standard': ('wall',), 'window/full-height': ('wall',), 'window/bay': ('wall',),
    'window/corner': ('wall',), 'window/corner-full-height': ('wall',),
    'furniture/sofa': ('floor',), 'furniture/coffee-table': ('floor',), 'furniture/chair': ('floor',),
    'furniture/dining-set': ('floor',), 'furniture/bed': ('floor',), 'furniture/cabinet': ('floor',),
    'furniture/generic': ('floor',),
    'furniture/wardrobe': ('floor',), 'furniture/bookshelf': ('floor',),
    'furniture/tv-stand': ('floor',), 'furniture/desk': ('floor',), 'furniture/nightstand': ('floor',),
    'furniture/bathtub': ('floor',), 'furniture/basin': ('floor',),
    'device/washer-dryer': ('floor',), 'device/air-conditioner-wall': ('wall',),
    'device/air-conditioner-floor': ('floor',), 'device/water-heater': ('wall',),
    'device/microwave-oven': ('floor', 'wall'), 'device/dishwasher': ('floor',),
    'device/water-purifier': ('wall', 'floor'), 'device/vacuum': ('floor',),
    'device/robot-vacuum': ('floor',), 'device/humidifier-purifier': ('floor',),
    'device/bathroom-heater': ('ceiling',), 'device/tv': ('wall', 'floor'),
    'device/speaker': ('floor', 'wall'), 'device/projector': ('ceiling', 'floor'),
    'device/curtain-motor': ('wall',), 'device/light': ('ceiling', 'wall'),
    'device/sensor': ('wall', 'ceiling'), 'device/camera': ('ceiling', 'wall'),
    'device/door-lock': ('wall',), 'device/smart-speaker': ('floor', 'wall'),
    'device/smart-toilet': ('floor',), 'device/router': ('floor', 'wall'),
    'device/dehumidifier': ('floor',), 'device/radiator': ('wall',),
    'device/coffee-machine': ('floor',), 'device/nas': ('floor',),
    'device/fridge': ('floor',), 'device/range-hood': ('wall',), 'device/oven': ('floor',),
    'device/generic': ('floor', 'wall', 'ceiling'),
}
# 已知目录的参数范围：字符串元组是枚举，数字元组是闭区间。
KNOWN = {
    'door/single': {'swing': ('left', 'right'), 'hinge': ('start', 'end')},
    'door/double': {'swing': ('left', 'right')},
    'door/sliding': {}, 'window/standard': {}, 'window/full-height': {},
    'window/bay': {'projection': (0.1, 1.5), 'side': ('left', 'right')},
    'window/corner': {'corner': ('start', 'end'), 'side': ('left', 'right'), 'leg': (0.1, 20.0)},
    'window/corner-full-height': {'corner': ('start', 'end'), 'side': ('left', 'right'), 'leg': (0.1, 20.0)},
    'furniture/sofa': {'seats': ('2', '3', '4')},
    'furniture/coffee-table': {'shelf': ('auto', 'yes', 'no')}, 'furniture/chair': {},
    'furniture/dining-set': {'seats': ('4', '6')},
    'furniture/bed': {'size': ('1.5', '1.8')},
    'furniture/cabinet': {'doors': ('auto', '1', '2', '3', '4', '5', '6'), 'doorWidth': (0.25, 0.8)},
    'furniture/wardrobe': {'doors': ('auto', '1', '2', '3', '4', '5', '6'), 'doorWidth': (0.25, 0.8)},
    'furniture/tv-stand': {'doors': ('auto', '1', '2', '3', '4', '5', '6'), 'doorWidth': (0.25, 0.8)},
    'furniture/nightstand': {'doors': ('auto', '1', '2', '3', '4', '5', '6'), 'doorWidth': (0.25, 0.8)},
    'furniture/bookshelf': {}, 'furniture/bathtub': {}, 'furniture/basin': {},
    'furniture/desk': {'shelf': ('auto', 'yes', 'no')},
    'device/washer-dryer': {}, 'device/air-conditioner-wall': {},
    'device/air-conditioner-floor': {}, 'device/water-heater': {},
    'device/microwave-oven': {}, 'device/dishwasher': {}, 'device/water-purifier': {},
    'device/vacuum': {}, 'device/robot-vacuum': {}, 'device/humidifier-purifier': {},
    'device/bathroom-heater': {}, 'device/tv': {}, 'device/speaker': {}, 'device/projector': {},
    'device/curtain-motor': {},
    # beam 接受旧文档的 30–360°；渲染钳到 180°，旧 range 作为未知参数保留。
    'device/light': {'kind': ('ceiling', 'pendant'), 'beam': (30, 360, True),
                     'brightness': (0, 100, True), 'color_temp': (2000, 6500, True)},
    'device/sensor': {}, 'device/camera': {}, 'device/door-lock': {}, 'device/smart-speaker': {},
    'device/smart-toilet': {}, 'device/router': {}, 'device/dehumidifier': {},
    'device/radiator': {}, 'device/coffee-machine': {}, 'device/nas': {}, 'device/generic': {},
    'device/fridge': {}, 'device/range-hood': {}, 'device/oven': {},
}
# 目录声明为**可选**的枚举参数：缺省合法（与前端目录的 `optional: true` 一一对应）。
# 数值型可选参数在 KNOWN 里用三元组 `(min, max, True)`；枚举型塞不进第三个 True，就登记在这里。
OPTIONAL = {
    'furniture/coffee-table': ('shelf',),
    'furniture/desk': ('shelf',),
}

def validate_catalog(item, category):
    catalog_id = item['catalogId']
    if not isinstance(catalog_id, str) or not re.fullmatch(rf'{category}/[a-z0-9][a-z0-9-]{{0,63}}', catalog_id):
        raise ValueError('目录身份与元素类型不匹配')
    surface = item['surface']
    if surface not in ('floor', 'wall', 'ceiling'):
        raise ValueError('放置面无效')
    if category in ('door', 'window') and surface != 'wall':
        raise ValueError('门窗必须附墙')
    if catalog_id in SURFACES and surface not in SURFACES[catalog_id]:
        raise ValueError('该目录不支持此放置面')
    params = item['params']
    if not isinstance(params, dict) or len(params) > 32:
        raise ValueError('元素参数必须是最多 32 项的字典')
    for key, value in params.items():
        if (not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,31}', key)
                or key in ('__proto__', 'constructor', 'prototype') or not (
                    type(value) is bool or isinstance(value, str) and len(value) <= 128 or
                    type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1_000_000)):
            raise ValueError('元素参数格式或范围无效')
    for key, bounds in KNOWN.get(catalog_id, {}).items():
        value = params.get(key)
        if value is None and (
            key in OPTIONAL.get(catalog_id, ()) or (len(bounds) == 3 and bounds[2] is True)
        ):
            # 声明为可选的参数：缺省合法（默认值在前端目录 defaults.params 里）。
            continue
        if isinstance(bounds[0], str):
            if value not in bounds: raise ValueError(f'{key}无效')
        elif type(value) not in (int, float) or not math.isfinite(value) or not bounds[0] <= value <= bounds[1]:
            raise ValueError(f'{key}无效')
