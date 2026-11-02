"""Serializes writes and detects concurrent edits before persisting."""
import asyncio
from copy import deepcopy
import logging
import re
from .model import validate_plan

_LOGGER = logging.getLogger(__name__)

MAX_UNDERLAY_CHARS = 1_500_000
FLOOR_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

class RevisionConflict(Exception):
    """Another client has saved a newer document."""

class FloorPlanRepository:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        self.snapshot = {'revision': 0, 'plan': None}
        self.load_error = None

    async def load(self):
        stored = await self.store.async_load()
        if stored is not None:
            # Explicit V6 replacement policy: old document geometry is not interpreted.
            if isinstance(stored, dict) and isinstance(stored.get('plan'), dict) and type(stored['plan'].get('schemaVersion')) is int and 1 <= stored['plan']['schemaVersion'] < 6:
                self.snapshot = {'revision': 0, 'plan': None}
                self.load_error = None
                _LOGGER.warning('Legacy floor plan is not loaded; next explicit save replaces it with V6')
                return
            try:
                plan = validate_plan(stored['plan'])
                if stored['revision'] != plan['revision']:
                    raise ValueError('Stored revision does not match plan')
            except (ValueError, KeyError, TypeError):
                self.load_error = '存储户型版本不受支持或内容损坏；请先从备份恢复，原文件未修改'
                _LOGGER.warning('Preserving unsupported or corrupted floor plan document')
                return
            self.load_error = None
            self.snapshot = {'revision': plan['revision'], 'plan': plan}

    def read(self):
        if self.load_error:
            raise ValueError(self.load_error)
        return deepcopy(self.snapshot)

    async def save(self, plan, expected_revision):
        if self.load_error:
            raise ValueError(self.load_error)
        if not isinstance(plan, dict) or plan.get('schemaVersion') != 6:
            raise ValueError('保存需要 v6 户型，请刷新前端后重试')
        validated = validate_plan(plan)
        async with self.lock:
            if expected_revision != self.snapshot['revision']:
                raise RevisionConflict
            revision = self.snapshot['revision'] + 1
            validated['revision'] = revision
            snapshot = {'revision': revision, 'plan': validated}
            # Update memory only after the atomic HA Store write succeeds.
            await self.store.async_save(snapshot)
            self.snapshot = snapshot
            return self.read()

class UnderlayRepository:
    """Per-floor tracing background images, kept outside the 256 KB plan document."""

    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        self.snapshot = {'revision': 0, 'underlays': {}}

    async def load(self):
        stored = await self.store.async_load()
        if stored is not None:
            if type(stored.get('revision')) is not int or not isinstance(stored.get('underlays'), dict):
                # Same policy as the plan document: never break integration setup.
                _LOGGER.warning('Discarding corrupted underlay data')
                return
            self.snapshot = {'revision': stored['revision'], 'underlays': stored['underlays']}

    def read(self):
        return deepcopy(self.snapshot)

    async def save(self, floor, image, width, height, expected_revision, x=0, y=0, rotation=0, calibrated=False):
        if not isinstance(floor, str) or not FLOOR_PATTERN.fullmatch(floor):
            raise ValueError('楼层无效')
        if not isinstance(image, str) or len(image) > MAX_UNDERLAY_CHARS:
            raise ValueError('底图过大或无效')
        if image and not image.startswith('data:image/jpeg;base64,'):
            raise ValueError('底图仅支持 JPEG')
        if image:
            if type(width) not in (int, float) or not 0.1 <= width <= 160:
                raise ValueError('底图宽度无效')
            if type(height) not in (int, float) or not 0.1 <= height <= 120:
                raise ValueError('底图高度无效')
            for value, low, high, label in ((x, -1000, 1000, '底图位置 X'), (y, -1000, 1000, '底图位置 Y'), (rotation, -360, 360, '底图旋转')):
                if type(value) not in (int, float) or not low <= value <= high:
                    raise ValueError(label + '无效')
            if not isinstance(calibrated, bool):
                raise ValueError('底图标定状态无效')
        async with self.lock:
            if expected_revision != self.snapshot['revision']:
                raise RevisionConflict
            underlays = deepcopy(self.snapshot['underlays'])
            if image:
                underlays[floor] = {'image': image, 'width': width, 'height': height,
                                    'x': x, 'y': y, 'rotation': rotation % 360, 'calibrated': calibrated}
            else:
                underlays.pop(floor, None)
            revision = self.snapshot['revision'] + 1
            snapshot = {'revision': revision, 'underlays': underlays}
            # Update memory only after the atomic HA Store write succeeds.
            await self.store.async_save(snapshot)
            self.snapshot = snapshot
            return self.read()
