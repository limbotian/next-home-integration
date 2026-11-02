"""Whole-home space style storage, kept in HA private storage outside the plan document.

风格是**按户一份**的纯外观设置：四个分类（墙面 / 地面 / 门窗 / 家具）各带颜色与纹理。
它不进入户型文档，沿用底图的策略：独立存储键、独立 revision、管理员写入。

**当时的理由（2026-10 上旬）**：户型格式是「只接受当前版本、不迁移」的，为一个外观设置
升 schemaVersion 会让用户既有的 V6 户型在下次加载时被拒绝。
**这条限制已于 2026-10-25 解除**（openspec `plan-format-forward-compatible`：新增可选字段
不再需要升版本，未知字段读取接受、保存原样保留）。

**但现在仍然不搬回去**，理由是独立的：

- 它是**按户一份**而不是按楼层，混进户型文档会让「户型 revision」与「风格 revision」互相拖累
  ——改一次颜色要动户型版本号，导入导出也会把风格带走（规范明确要求风格不随户型导出）。
- 它由管理员写入、与户型保存是两条独立命令，合成一条会丢掉「只改风格不碰户型」的能力。

换句话说：以后再有「想往户型里加字段」的功能，**可以直接加进户型了**，不必再开 sidecar。

读取是**宽松**的：分类缺失、颜色非法、纹理标识不认识，都只让**那一项**回落为「未设置」，
不丢整条记录、不报错；只有结构整体损坏（不是 dict、revision 不是整数）才整份丢弃。
"""
import asyncio
import logging
import re
from copy import deepcopy

from .repository import RevisionConflict

_LOGGER = logging.getLogger(__name__)

CATEGORIES = ('wall', 'floor', 'opening', 'furniture')
# 纹理标识白名单：与前端 `styleTextures.ts` 的生成器一一对应，新增一侧必须同时改另一侧。
TEXTURES = ('none', 'wood', 'tile', 'marble', 'concrete', 'fabric', 'wallpaper', 'panel', 'leather')
COLOR_PATTERN = re.compile(r'^#[0-9a-fA-F]{6}$')


def clean_category(value):
    """把一条分类记录收敛成 {'color','texture'}；无法识别时返回 None（=未设置）。"""
    if not isinstance(value, dict):
        return None
    color = value.get('color')
    texture = value.get('texture')
    if not isinstance(color, str) or not COLOR_PATTERN.match(color):
        return None
    if not isinstance(texture, str) or texture not in TEXTURES:
        return None
    return {'color': color.lower(), 'texture': texture}


def clean_style(value):
    """收敛整份风格：逐项回落，未知分类直接丢弃（不是整份拒绝）。"""
    if not isinstance(value, dict):
        return {}
    cleaned = {}
    for category in CATEGORIES:
        item = clean_category(value.get(category))
        if item is not None:
            cleaned[category] = item
    return cleaned


class SpaceStyleRepository:
    """Per-home space style, with the same write discipline as the plan and the underlay."""

    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        self.snapshot = {'revision': 0, 'style': {}}

    async def load(self):
        stored = await self.store.async_load()
        if stored is None:
            return
        if not isinstance(stored, dict) or type(stored.get('revision')) is not int:
            # 与户型 / 底图一致：坏数据只告警，不阻断集成加载。
            _LOGGER.warning('Discarding corrupted space style data')
            return
        self.snapshot = {'revision': stored['revision'], 'style': clean_style(stored.get('style'))}

    def read(self):
        return deepcopy(self.snapshot)

    async def save(self, style, expected_revision):
        # 保存路径严格（写入方是自家前端，非法取值应暴露而不是静默丢），读取路径宽松。
        if not isinstance(style, dict) or set(style) - set(CATEGORIES):
            raise ValueError('空间风格无效')
        cleaned = clean_style(style)
        if len(cleaned) != len(style):
            raise ValueError('空间风格包含无法识别的颜色或纹理')
        async with self.lock:
            if expected_revision != self.snapshot['revision']:
                raise RevisionConflict
            revision = self.snapshot['revision'] + 1
            snapshot = {'revision': revision, 'style': cleaned}
            # 与户型存储一致：先完成原子写盘，再更新内存快照。
            await self.store.async_save(snapshot)
            self.snapshot = snapshot
            return self.read()
