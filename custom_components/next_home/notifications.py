"""Cross-device notification read state, kept in HA private storage.

已读集合是家庭共享的：任一设备打开通知面板即把当前通知标记为已读，其它设备随后
读到同一份状态（跨设备同步）。非管理员也可以写入——iPad 上的会话通常不是管理员。
"""
import asyncio
import logging
from copy import deepcopy

_LOGGER = logging.getLogger(__name__)

# 已读集合的容量上限：超出后丢弃最早的记录，避免无界增长（通知 id 是短字符串）。
MAX_READ_IDS = 500
MAX_ID_LENGTH = 128


class NotificationReadRepository:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        self.snapshot = {'read': []}

    async def load(self):
        stored = await self.store.async_load()
        if stored is None:
            return
        read = stored.get('read') if isinstance(stored, dict) else None
        if not isinstance(read, list) or not all(
            isinstance(item, str) and 0 < len(item) <= MAX_ID_LENGTH for item in read
        ):
            # 与户型/底图一致：坏数据只告警，不阻断集成加载。
            _LOGGER.warning('Discarding corrupted notification read state')
            return
        self.snapshot = {'read': read[-MAX_READ_IDS:]}

    def read(self):
        return deepcopy(self.snapshot)

    async def mark_read(self, ids):
        if not isinstance(ids, list) or not ids:
            raise ValueError('请提供要标记的通知')
        if not all(isinstance(item, str) and 0 < len(item) <= MAX_ID_LENGTH for item in ids):
            raise ValueError('通知 id 无效')
        async with self.lock:
            requested = list(dict.fromkeys(ids))
            remaining = [item for item in self.snapshot['read'] if item not in requested]
            snapshot = {'read': (remaining + requested)[-MAX_READ_IDS:]}
            # 与户型存储一致：先完成原子写盘，再更新内存快照。
            await self.store.async_save(snapshot)
            self.snapshot = snapshot
            return self.read()
