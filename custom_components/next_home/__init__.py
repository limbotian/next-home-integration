"""Local floor-plan and underlay storage. Cloud accounts and HA devices remain independent."""
import logging
import time

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from .const import (
    DOMAIN,
    EVENT_SHARED,
    PANEL_ELEMENT,
    PANEL_MODULE_URL,
    PANEL_REGISTRY_KEY,
    PANEL_SIDEBAR_ICON,
    PANEL_SIDEBAR_TITLE,
    PANEL_URL_PATH,
)
from .assets import async_sync_panel
from .repository import FloorPlanRepository, UnderlayRepository, RevisionConflict
from .notifications import NotificationReadRepository
from .shared import SharedSessionRepository, SharedSkinRepository
from .space_style import SpaceStyleRepository

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """兼容 `configuration.yaml` 里的 `next_home:`。

    以前这里直接初始化存储；现在真正的初始化搬到了 `async_setup_entry`——界面添加和 YAML
    添加走同一条路。见到 YAML 配置就发起一次 import flow 建配置项，老用户的
    configuration.yaml 一个字都不用改。
    """
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={'source': config_entries.SOURCE_IMPORT},
                data={},
            )
        )
    return True

async def async_setup_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """界面添加 / YAML 导入都会走到这里。"""
    if hass.data.get(DOMAIN):
        # 重复 setup（重载、两个入口同时触发）时直接放过，别把存储建两遍。
        return True

    repository = FloorPlanRepository(Store(hass, 1, 'next_home.floor_plan', private=True))
    underlay = UnderlayRepository(Store(hass, 1, 'next_home.underlay', private=True))
    notifications = NotificationReadRepository(Store(hass, 1, 'next_home.notifications', private=True))
    space_style = SpaceStyleRepository(Store(hass, 1, 'next_home.space_style', private=True))
    shared_skin = SharedSkinRepository(Store(hass, 1, 'next_home.shared_skin', private=True))
    shared_session = SharedSessionRepository(Store(hass, 1, 'next_home.shared_session', private=True))
    await repository.load()
    await underlay.load()
    await notifications.load()
    await space_style.load()
    await shared_skin.load()
    await shared_session.load()
    hass.data[DOMAIN] = {
        'plan': repository,
        'underlay': underlay,
        'notifications': notifications,
        'space_style': space_style,
        'shared_skin': shared_skin,
        'shared_session': shared_session,
    }
    websocket_api.async_register_command(hass, get_plan)
    websocket_api.async_register_command(hass, save_plan)
    websocket_api.async_register_command(hass, get_underlay)
    websocket_api.async_register_command(hass, save_underlay)
    websocket_api.async_register_command(hass, get_space_style)
    websocket_api.async_register_command(hass, save_space_style)
    websocket_api.async_register_command(hass, get_notification_reads)
    websocket_api.async_register_command(hass, mark_notifications_read)
    websocket_api.async_register_command(hass, get_shared)
    websocket_api.async_register_command(hass, save_shared_skin)
    websocket_api.async_register_command(hass, save_shared_session)
    websocket_api.async_register_command(hass, clear_shared_session)
    websocket_api.async_register_command(hass, subscribe_shared)
    # HACS 装的时候只搬集成目录，面板资源得靠这一步补到 /config/www（见 assets.py）
    await async_sync_panel(hass)
    await async_register_panel(hass)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """移除集成时把侧边栏入口摘掉。

    存储对象和 WebSocket 命令留在内存里不回收：命令是进程级注册，摘不干净反倒会让面板
    报 `not_found`；用户删了再加回来时直接复用即可。
    """
    try:
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_missing=False)
    except Exception:  # noqa: BLE001 —— 摘面板失败不影响卸载
        _LOGGER.exception('移除 Next Home 面板失败')
    return True

async def async_register_panel(hass: HomeAssistant) -> bool:
    """注册侧边栏面板；已存在时跳过，失败只记日志（不打断集成初始化）。

    为什么注册在**代码里**而不是 configuration.yaml 的 `panel_custom:`：那一块是外部文件里的
    可丢失数据。用户 2026-10-04 报「每次断电重启后菜单消失」，现场是 configuration.yaml 被一次
    未落盘的写入截断——文件正好是部署产物的前缀，切口落在 `panel_custom:` 那一行之前。HA 每次
    启动都从配置重建侧边栏，配置里没有就**永远不会自愈**。注册进代码后，任何对配置文件的改写、
    截断或还原都不再影响菜单；集成开关 `next_home:` 只需一行仍在（它每次都在）。

    幂等：同名 `url_path` 已在登记表里（用户可能还留着旧的 YAML 块）就直接跳过——HA 的
    `async_register_panel` 对重复路径会抛 `ValueError`。降级：任何异常都吞在这里，户型的读写与
    所有 WebSocket 命令必须照常可用；"面板没上"由部署脚本的 HTTP 探针负责暴露。
    """
    if PANEL_URL_PATH in hass.data.get(PANEL_REGISTRY_KEY, {}):
        return False
    try:
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name=PANEL_ELEMENT,
            sidebar_title=PANEL_SIDEBAR_TITLE,
            sidebar_icon=PANEL_SIDEBAR_ICON,
            module_url=PANEL_MODULE_URL,
            embed_iframe=False,
            trust_external=True,
            require_admin=False,
        )
    except Exception:  # noqa: BLE001 —— 面板是入口，存储是命脉，这里不许把集成拖挂
        _LOGGER.exception('Next Home 面板注册失败；户型存储与 WebSocket 命令不受影响')
        return False
    return True

@websocket_api.websocket_command({vol.Required('type'): 'next_home/floor_plan/get'})
@callback
def get_plan(hass, connection, message):
    try:
        connection.send_result(message['id'], hass.data[DOMAIN]['plan'].read())
    except ValueError as error:
        connection.send_error(message['id'], 'invalid_plan', str(error))

@websocket_api.websocket_command({
    vol.Required('type'): 'next_home/floor_plan/save',
    vol.Required('plan'): dict,
    vol.Required('expected_revision'): vol.All(int, vol.Range(min=0)),
})
@websocket_api.async_response
async def save_plan(hass, connection, message):
    """保存户型文档。

    **不要求管理员**（用户 2026-10-05：「我没要求过需要管理员才能修改」）：任何已登录用户都能
    保存，乐观并发（`expected_revision`）与磁盘失败保护照旧。读取命令本来就不限管理员。
    """
    try:
        result = await hass.data[DOMAIN]['plan'].save(message['plan'], message['expected_revision'])
    except RevisionConflict:
        connection.send_error(message['id'], 'revision_conflict', '其他设备已保存新版本，请先导出本地修改，再重新加载。')
        return
    except ValueError as error:
        connection.send_error(message['id'], 'invalid_plan', str(error))
        return
    connection.send_result(message['id'], result)

@websocket_api.websocket_command({vol.Required('type'): 'next_home/underlay/get'})
@callback
def get_underlay(hass, connection, message):
    connection.send_result(message['id'], hass.data[DOMAIN]['underlay'].read())

@websocket_api.websocket_command({
    vol.Required('type'): 'next_home/underlay/save',
    vol.Required('floor'): str,
    vol.Required('image'): str,
    vol.Required('width'): vol.Any(int, float),
    vol.Required('height'): vol.Any(int, float),
    vol.Optional('x', default=0): vol.Any(int, float),
    vol.Optional('y', default=0): vol.Any(int, float),
    vol.Optional('rotation', default=0): vol.Any(int, float),
    vol.Optional('calibrated', default=False): bool,
    vol.Required('expected_revision'): vol.All(int, vol.Range(min=0)),
})
@websocket_api.async_response
async def save_underlay(hass, connection, message):
    """保存楼层底图。

    **不要求管理员**（同 `save_plan`，用户 2026-10-05 裁定）：任何已登录用户都能写入，
    revision 冲突与「不随户型导入导出」的既有约束不变。
    """
    try:
        result = await hass.data[DOMAIN]['underlay'].save(
            message['floor'], message['image'], message['width'], message['height'],
            message['expected_revision'], x=message['x'], y=message['y'],
            rotation=message['rotation'], calibrated=message['calibrated'])
    except RevisionConflict:
        connection.send_error(message['id'], 'revision_conflict', '其他设备已更新底图，请重新加载。')
        return
    except ValueError as error:
        connection.send_error(message['id'], 'invalid_underlay', str(error))
        return
    connection.send_result(message['id'], result)


@websocket_api.websocket_command({vol.Required('type'): 'next_home/space_style/get'})
@callback
def get_space_style(hass, connection, message):
    connection.send_result(message['id'], hass.data[DOMAIN]['space_style'].read())


@websocket_api.websocket_command({
    vol.Required('type'): 'next_home/space_style/save',
    vol.Required('style'): dict,
    vol.Required('expected_revision'): vol.All(int, vol.Range(min=0)),
})
@websocket_api.async_response
async def save_space_style(hass, connection, message):
    """保存全屋空间风格。

    **不要求管理员**（用户 2026-10-05：「这跟管理员没关系，这个空间风格的自定义是用户自己的行为」）：
    任何已登录用户都能改，跨设备共享与按户一份的语义不变。写入仍在锁内比较 `expected_revision`，
    过期写入返回 `revision_conflict` 且不覆盖当前风格。户型文档与底图的写权限**不随此放宽**。
    """
    try:
        result = await hass.data[DOMAIN]['space_style'].save(message['style'], message['expected_revision'])
    except RevisionConflict:
        connection.send_error(message['id'], 'revision_conflict', '其他设备已更新空间风格，请重新加载。')
        return
    except ValueError as error:
        connection.send_error(message['id'], 'invalid_space_style', str(error))
        return
    connection.send_result(message['id'], result)
    # 其它终端据此重读，不必等刷新（2026-10-25：风格此前要刷新页面才看到）。
    hass.bus.async_fire(EVENT_SHARED, {'changed': 'space_style', 'revision': result['revision']})


@websocket_api.websocket_command({vol.Required('type'): 'next_home/notifications/get'})
@callback
def get_notification_reads(hass, connection, message):
    """任何已登录用户可读（非管理员也能看到自己的家庭已读状态）。"""
    connection.send_result(message['id'], hass.data[DOMAIN]['notifications'].read())


@websocket_api.websocket_command({
    vol.Required('type'): 'next_home/notifications/read',
    vol.Required('ids'): [str],
})
@websocket_api.async_response
async def mark_notifications_read(hass, connection, message):
    """标记已读：不加 require_admin，iPad 上的非管理员会话也能写入。"""
    try:
        result = await hass.data[DOMAIN]['notifications'].mark_read(message['ids'])
    except ValueError as error:
        connection.send_error(message['id'], 'invalid_notifications', str(error))
        return
    connection.send_result(message['id'], result)


def _shared_by(connection):
    """发起本次写入的 HA 用户名，仅用于排查「谁把账号带进了这个家庭」。"""
    user = getattr(connection, 'user', None)
    name = getattr(user, 'name', None)
    return name if isinstance(name, str) and name else 'unknown'


@websocket_api.websocket_command({vol.Required('type'): 'next_home/shared/get'})
@callback
def get_shared(hass, connection, message):
    """家庭共享档案：会话与皮肤各一份，空值是正常状态（新装的家庭还没人登录过）。

    **不加 require_admin**：面板以 `require_admin=False` 注册，iPad 这类家庭账号也要能读。
    代价是令牌明文落在 HA 私有存储、任何已登录用户都能取到（见 `shared.py` 的模块说明）。
    """
    connection.send_result(message['id'], {
        'skin': hass.data[DOMAIN]['shared_skin'].read(),
        'session': hass.data[DOMAIN]['shared_session'].read(),
    })


@websocket_api.websocket_command({
    vol.Required('type'): 'next_home/shared/skin',
    vol.Required('skin'): str,
})
@websocket_api.async_response
async def save_shared_skin(hass, connection, message):
    """保存当前皮肤。覆盖写，不做 revision 冲突（换皮肤是单选，最后动手的说了算）。"""
    try:
        snapshot = await hass.data[DOMAIN]['shared_skin'].save(message['skin'])
    except ValueError as error:
        connection.send_error(message['id'], 'invalid_skin', str(error))
        return
    connection.send_result(message['id'], snapshot)
    hass.bus.async_fire(EVENT_SHARED, {'changed': 'skin', 'skin': snapshot})


@websocket_api.websocket_command({
    vol.Required('type'): 'next_home/shared/session',
    vol.Required('session'): dict,
})
@websocket_api.async_response
async def save_shared_session(hass, connection, message):
    """上报登录态：后登录者胜。`shared_at` 与 `shared_by` 由服务端盖章，不信客户端时钟。"""
    payload = dict(message['session'])
    payload['shared_at'] = int(time.time())
    payload['shared_by'] = _shared_by(connection)
    try:
        snapshot = await hass.data[DOMAIN]['shared_session'].save(payload)
    except ValueError as error:
        connection.send_error(message['id'], 'invalid_session', str(error))
        return
    connection.send_result(message['id'], snapshot)
    # 广播里带上令牌：其它终端因此不必再发一次请求就能采用。
    hass.bus.async_fire(EVENT_SHARED, {'changed': 'session', 'session': snapshot})


@websocket_api.websocket_command({vol.Required('type'): 'next_home/shared/session/clear'})
@websocket_api.async_response
async def clear_shared_session(hass, connection, message):
    """退出登录：清空共享并广播，其它终端据此一起回落游客。"""
    snapshot = await hass.data[DOMAIN]['shared_session'].clear()
    connection.send_result(message['id'], snapshot)
    hass.bus.async_fire(EVENT_SHARED, {'changed': 'session', 'session': snapshot})


@websocket_api.websocket_command({vol.Required('type'): 'next_home/shared/subscribe'})
@callback
def subscribe_shared(hass, connection, message):
    """订阅家庭共享档案的变更。监听器挂在 `connection.subscriptions` 上，断连由 HA 释放。"""
    connection.subscriptions[message['id']] = hass.bus.async_listen(
        EVENT_SHARED,
        lambda event: connection.send_message(
            websocket_api.event_message(message['id'], event.data)),
    )
    connection.send_result(message['id'])
