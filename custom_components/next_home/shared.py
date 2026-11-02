"""Family-shared profile: one cloud session and one skin for the whole home.

与空间风格同族，但语义不同：

- **会话**是覆盖写（后登录者胜），不设 revision 冲突——登录表达的是「这个家现在
  在用哪个账号」，不是需要合并的编辑；
- **皮肤**是单选，同样覆盖写，`revision` 只用来让广播与读取能对上号。

两者都存 HA 私有存储、各自独立键，不写户型文档、不写底图、不碰空间风格。
**读写不要求管理员**：面板本身以 `require_admin=False` 注册，用户 2026-10-25 要求
「所有连接此台 HA 的终端」都同步——加管理员限制会直接与需求冲突。

**已知代价**：云账号令牌以明文落在这里。任何能读该文件或能连 HA WebSocket 的已登录
用户都能取到它，这就是「全家共用一个账号」的字面含义（见 openspec 变更
`shared-home-profile` 的设计 D2）。缓解手段只有云侧撤销令牌与任一台终端退出即清空。
"""
import asyncio
import logging
from copy import deepcopy

_LOGGER = logging.getLogger(__name__)

MAX_TOKEN_LENGTH = 512
MAX_TEXT_LENGTH = 254
MAX_SKIN_LENGTH = 64
PLANS = ('free', 'paid')


def clean_skin(value):
    """皮肤标识：非空、长度有界。注册表在客户端，服务端只挡明显的坏值。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_SKIN_LENGTH:
        return None
    return text


def _clean_text(value, limit=MAX_TEXT_LENGTH):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit:
        return None
    return text


def _clean_timestamp(value):
    if value is None:
        return None
    if type(value) not in (int, float) or value < 0:
        return None
    return int(value)


def clean_session(value):
    """把一份共享会话收敛成字段齐全的 dict；任何必需字段不合格都返回 None。

    宁可整份丢弃也不留半条记录：缺邮箱的会话无法归档权益缓存，缺令牌的会话什么都做不了。
    """
    if not isinstance(value, dict):
        return None
    token = _clean_text(value.get('access_token'), MAX_TOKEN_LENGTH)
    email = _clean_text(value.get('email'))
    plan = value.get('plan')
    if token is None or email is None or plan not in PLANS:
        return None
    return {
        'access_token': token,
        'email': email,
        'plan': plan,
        'expires_at': _clean_timestamp(value.get('expires_at')),
        'shared_at': _clean_timestamp(value.get('shared_at')) or 0,
        'shared_by': _clean_text(value.get('shared_by'), 128) or 'unknown',
    }


class SharedSkinRepository:
    """当前皮肤，全家一份。缺省 `None` = 还没有人设过（不是「默认皮肤」）。"""

    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        self.snapshot = {'revision': 0, 'skin': None}

    async def load(self):
        stored = await self.store.async_load()
        if stored is None:
            return
        if not isinstance(stored, dict) or type(stored.get('revision')) is not int:
            _LOGGER.warning('Discarding corrupted shared skin data')
            return
        skin = stored.get('skin')
        self.snapshot = {'revision': stored['revision'], 'skin': clean_skin(skin)}

    def read(self):
        return deepcopy(self.snapshot)

    async def save(self, skin):
        cleaned = clean_skin(skin)
        if cleaned is None:
            raise ValueError('皮肤标识无效')
        async with self.lock:
            revision = self.snapshot['revision'] + 1
            snapshot = {'revision': revision, 'skin': cleaned}
            # 与其它仓储一致：先完成原子写盘，再更新内存快照。
            await self.store.async_save(snapshot)
            self.snapshot = snapshot
            return self.read()


class SharedSessionRepository:
    """全家共享的云账号会话，覆盖写；`clear()` 保留 revision 递增以便广播对号。"""

    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        self.snapshot = {'revision': 0, 'session': None}

    async def load(self):
        stored = await self.store.async_load()
        if stored is None:
            return
        if not isinstance(stored, dict) or type(stored.get('revision')) is not int:
            _LOGGER.warning('Discarding corrupted shared session data')
            return
        self.snapshot = {'revision': stored['revision'], 'session': clean_session(stored.get('session'))}

    def read(self):
        return deepcopy(self.snapshot)

    async def save(self, session):
        cleaned = clean_session(session)
        if cleaned is None:
            raise ValueError('共享会话无效：需要令牌、邮箱与方案')
        async with self.lock:
            snapshot = {'revision': self.snapshot['revision'] + 1, 'session': cleaned}
            await self.store.async_save(snapshot)
            self.snapshot = snapshot
            return self.read()

    async def clear(self):
        async with self.lock:
            snapshot = {'revision': self.snapshot['revision'] + 1, 'session': None}
            await self.store.async_save(snapshot)
            self.snapshot = snapshot
            return self.read()
