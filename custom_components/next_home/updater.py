"""设备端自更新：把发布包换成 `custom_components/next_home`，重启后面板一起换新。

**为什么需要**（2026-11-03）：卖出去的整机只有一个死面板。面板或集成要改一版，只能重刷镜像或
人工推文件，客户机器在别人家里，两条都不成立。`assets.py` 已经解决了「集成换新 → 面板自动跟着换」
（它按 `release.json` 把集成自带的 `www/next-home-studio` 同步到 `/config/www`），
所以这里只需要把**新版本安全地送进集成目录**。

**为什么不用 HACS**：HACS 要求用户先「添加自定义仓库」，产品里不该出现这一步。

**这个模块的约束**：文件顶部不许 import homeassistant。
- 开发机上没有 HA 运行时，测试要能把本文件单独加载进来跑纯函数（见 `scripts/tests/test_panel_updater.py`）；
- 所以 HA 相关的东西一律在函数体里延迟导入。

替换策略见 `openspec/changes/panel-self-update/design.md`：暂存 + 两步 rename，绝不原地解压。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import zipfile
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'next_home'
COMPONENT = 'next_home'
# 全部放 /config 下：与 custom_components 同一文件系统，rename 才是原子的
UPDATE_DIR = '.next_home_update'
BUNDLE_INNER = Path('custom_components') / COMPONENT

# 默认发布源。换域名只改这一处；设备端可在集成选项里覆盖。
UPDATE_MANIFEST_URL = 'https://gitee.com/limbotian/next-home-releases/raw/master/panel/release.json'
CONF_MANIFEST_URL = 'manifest_url'
CONF_ALLOW_INSECURE = 'allow_insecure_source'

MAX_BUNDLE_BYTES = 200 * 1024 * 1024
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
CHUNK = 256 * 1024


class UpdateError(Exception):
    """带机读 code 的失败。前端按 code 判断，不去解析文案。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------- 纯函数


def compare_versions(left: str, right: str) -> int:
    """`0.4` 与 `0.4.0` 相等；非数字段按字符串比。返回 -1 / 0 / 1。"""
    def segments(value: str) -> list:
        return [int(part) if part.isdigit() else part for part in str(value).strip().split('.')]

    a, b = segments(left), segments(right)
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else 0
        y = b[i] if i < len(b) else 0
        if isinstance(x, int) and isinstance(y, int):
            if x != y:
                return 1 if x > y else -1
            continue
        sx, sy = str(x), str(y)
        if sx != sy:
            return 1 if sx > sy else -1
    return 0


def check_source(url: str, allow_insecure: bool) -> None:
    """只放 https；内网调试要显式打开允许不安全源。"""
    if not isinstance(url, str) or not url:
        raise UpdateError('bad_manifest', '清单地址为空')
    lowered = url.lower()
    if lowered.startswith('https://'):
        return
    if lowered.startswith('http://') and allow_insecure:
        _LOGGER.warning('自更新使用非 https 源：%s（只应在内网调试时出现）', url)
        return
    raise UpdateError('insecure_source', '发布源必须是 https；内网调试请在集成选项里允许不安全源')


def parse_manifest(payload: str) -> dict:
    """把清单 JSON 收敛成固定字段；任何不合规都抛 UpdateError。"""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError) as err:
        raise UpdateError('bad_manifest', f'清单不是合法 JSON：{err}') from err
    if not isinstance(data, dict):
        raise UpdateError('bad_manifest', '清单顶层必须是对象')

    version = data.get('version')
    if not isinstance(version, str) or not version.strip():
        raise UpdateError('bad_manifest', '清单缺 version')
    url = data.get('url')
    if not isinstance(url, str) or not url.strip():
        raise UpdateError('bad_manifest', '清单缺 url')
    sha = data.get('sha256')
    if not isinstance(sha, str) or not SHA256_RE.match(sha.strip().lower()):
        raise UpdateError('bad_manifest', '清单的 sha256 不是 64 位十六进制')

    notes = data.get('notes')
    min_ha = data.get('min_ha')
    return {
        'version': version.strip(),
        'url': url.strip(),
        'sha256': sha.strip().lower(),
        'notes': notes.strip() if isinstance(notes, str) else '',
        'min_ha': min_ha.strip() if isinstance(min_ha, str) and min_ha.strip() else '',
    }


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def _safe_members(archive: zipfile.ZipFile) -> None:
    """拒绝绝对路径、`..` 与符号链接条目——zip 里的路径不能逃出暂存目录。"""
    for info in archive.infolist():
        name = info.filename
        if name.startswith('/') or name.startswith('\\'):
            raise UpdateError('bad_bundle', f'包里有绝对路径：{name}')
        if '..' in Path(name).parts:
            raise UpdateError('bad_bundle', f'包里有越界路径：{name}')
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise UpdateError('bad_bundle', f'包里有符号链接：{name}')


def extract_bundle(zip_path: Path, stage: Path) -> tuple[Path, str]:
    """解压到暂存目录，返回（包内组件目录, 包内版本号）；结构不对直接失败，不碰现有安装。"""
    stage.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            _safe_members(archive)
            archive.extractall(stage)
    except zipfile.BadZipFile as err:
        raise UpdateError('bad_bundle', f'包不是合法 zip：{err}') from err

    component = stage / BUNDLE_INNER
    manifest = component / 'manifest.json'
    if not manifest.is_file():
        raise UpdateError('bad_bundle', '包里没有 custom_components/next_home/manifest.json')
    try:
        meta = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError) as err:
        raise UpdateError('bad_bundle', f'包内 manifest.json 读不了：{err}') from err
    check_panel_pair(component)
    return component, str(meta.get('version', '')).strip()


def check_panel_pair(component: Path) -> None:
    """包里的面板必须自带与 release.json 对得上的入口。

    2026-11-03 真机上白屏过一次：包里只换了 release.json、没带 `app/entry-<release>.html`，
    桥照着 release 去 iframe 一个 404。文件都在、HTTP 全 200，只有浏览器里是白的。
    宁可在替换前拒装，也别把一台机器的面板弄白。
    """
    panel = component / 'www' / 'next-home-studio'
    marker = panel / 'release.json'
    if not marker.is_file():
        return
    try:
        data = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError) as err:
        raise UpdateError('bad_bundle', f'面板 release.json 读不了：{err}') from err
    release = data.get('release') if isinstance(data, dict) else None
    if not isinstance(release, str) or not release.strip():
        raise UpdateError('bad_bundle', '面板 release.json 缺 release 字段')
    entry = panel / 'app' / f'entry-{release.strip()}.html'
    if not entry.is_file():
        raise UpdateError('bad_bundle', f'release.json 指向 entry-{release.strip()}.html，包里没有这个文件')


def commit_bundle(config_dir: Path, component: Path, version: str, from_version: str) -> dict:
    """两步 rename 换目录；第二步失败要把第一步倒回去，别把设备留在半空中。"""
    root = config_dir / UPDATE_DIR
    root.mkdir(parents=True, exist_ok=True)
    previous = root / 'previous'
    if previous.exists():
        shutil.rmtree(previous)
    previous.mkdir(parents=True)

    target = config_dir / 'custom_components' / COMPONENT
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_dir():
        raise UpdateError('not_installed', f'找不到 {target}，这台机器不是集成安装')

    os.replace(str(target), str(previous / COMPONENT))
    try:
        os.replace(str(component), str(target))
    except OSError as err:
        os.replace(str(previous / COMPONENT), str(target))
        raise UpdateError('commit_failed', f'替换失败，已还原上一版：{err}') from err

    (root / 'pending.json').write_text(
        json.dumps({'from': from_version, 'to': version, 'at': _now()}, ensure_ascii=False),
        encoding='utf-8',
    )
    return {'installed': version, 'previous': from_version}


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def reconcile(config_dir: Path, current_version: str) -> dict:
    """启动时收敛：确认替换成功、清掉下载与暂存残留。

    `pending.json` 还在而版本不等于 `to`，说明新版本没起来或被人手动换回去了——标记留着当现场证据。
    """
    root = config_dir / UPDATE_DIR
    if not root.is_dir():
        return {'status': 'clean'}

    for leftover in ('download', 'stage'):
        path = root / leftover
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    marker = root / 'pending.json'
    if not marker.is_file():
        return {'status': 'clean'}
    try:
        data = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        marker.unlink(missing_ok=True)
        return {'status': 'stale'}

    if str(data.get('to', '')) == current_version:
        marker.unlink(missing_ok=True)
        return {'status': 'confirmed', 'version': current_version, 'from': data.get('from')}

    _LOGGER.warning(
        '上次更新未生效：标记载的是 %s，实际运行 %s；上一版在 %s',
        data.get('to'), current_version, root / 'previous',
    )
    return {'status': 'unconfirmed', 'to': data.get('to'), 'from': data.get('from')}


def hacs_owns(config_dir: Path) -> bool:
    """HACS 管着同一个目录时两边会互相覆盖，直接拒绝。"""
    store = config_dir / '.storage' / 'hacs'
    if not store.is_file():
        return False
    try:
        return 'next-home-integration' in store.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return False


def installed_version(config_dir: Path) -> str:
    try:
        meta = json.loads(
            (config_dir / 'custom_components' / COMPONENT / 'manifest.json').read_text('utf-8'))
        return str(meta.get('version', '')).strip()
    except (OSError, ValueError):
        return ''


# ---------------------------------------------------------------- 与 HA 的接线


def _options(hass) -> dict:
    entries = hass.config_entries.async_entries(DOMAIN)
    return dict(entries[0].options) if entries else {}


def _manifest_url(options: dict) -> str:
    return str(options.get(CONF_MANIFEST_URL) or UPDATE_MANIFEST_URL)


def _ha_version() -> str:
    from homeassistant.const import __version__

    return __version__


async def _fetch_manifest(hass, url: str) -> dict:
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    async with session.get(url, timeout=30) as response:
        if response.status != 200:
            raise UpdateError('not_found', f'清单地址返回 {response.status}')
        return parse_manifest(await response.text())


async def async_check(hass) -> dict:
    """查版本。清单坏了不抛异常——面板要能显示「为什么不能更新」。"""
    config_dir = Path(hass.config.path())
    options = _options(hass)
    # 读 manifest 是文件 I/O，别在事件循环里做（HA 会记 blocking call 警告）
    current = await hass.async_add_executor_job(installed_version, config_dir)
    result = {
        'current': current,
        'latest': '',
        'available': False,
        'notes': '',
        'reason': '',
        'code': '',
    }
    try:
        url = _manifest_url(options)
        check_source(url, bool(options.get(CONF_ALLOW_INSECURE)))
        manifest = await _fetch_manifest(hass, url)
        result['latest'] = manifest['version']
        result['notes'] = manifest['notes']
        if manifest['min_ha'] and compare_versions(_ha_version(), manifest['min_ha']) < 0:
            result.update(code='ha_too_old', reason=f'需要 Home Assistant {manifest["min_ha"]} 或更高')
            return result
        if compare_versions(manifest['version'], current) > 0:
            result.update(available=True)
        else:
            result.update(code='up_to_date', reason='已是最新')
    except UpdateError as err:
        result.update(code=err.code, reason=err.message)
    except Exception as err:  # noqa: BLE001 —— 网络异常不该把命令打成 500
        _LOGGER.warning('检查更新失败：%s', err)
        result.update(code='network', reason=f'连不上发布源：{err}')
    return result


async def async_apply(hass) -> dict:
    """下载 → 校验 → 暂存 → 替换；成功后异步重启。失败时设备保持原样。"""
    config_dir = Path(hass.config.path())
    if await hass.async_add_executor_job(hacs_owns, config_dir):
        raise UpdateError('hacs', '这个集成由 HACS 管理，请在 HACS 里更新')

    options = _options(hass)
    allow_insecure = bool(options.get(CONF_ALLOW_INSECURE))
    url = _manifest_url(options)
    check_source(url, allow_insecure)

    manifest = await _fetch_manifest(hass, url)
    current = await hass.async_add_executor_job(installed_version, config_dir)
    if compare_versions(manifest['version'], current) <= 0:
        raise UpdateError('up_to_date', f'已是最新（{current}）')
    check_source(manifest['url'], allow_insecure)

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    root = config_dir / UPDATE_DIR
    downloads = root / 'download'
    downloads.mkdir(parents=True, exist_ok=True)
    zip_path = downloads / f'{manifest["version"]}.zip'
    stage = root / 'stage' / manifest['version']
    try:
        await _download(hass, async_get_clientsession(hass), manifest['url'], zip_path,
                        manifest['sha256'])
        if stage.exists():
            shutil.rmtree(stage)
        component, packed = await hass.async_add_executor_job(extract_bundle, zip_path, stage)
        if compare_versions(packed, manifest['version']) != 0:
            raise UpdateError(
                'version_mismatch', f'包内版本 {packed or "空"} 与清单 {manifest["version"]} 不一致')
        result = await hass.async_add_executor_job(
            commit_bundle, config_dir, component, manifest['version'], current or 'unknown')
    finally:
        # 删目录也是文件 I/O，别在事件循环里做（HA 会记 blocking call 警告）
        await hass.async_add_executor_job(shutil.rmtree, downloads, True)
        await hass.async_add_executor_job(shutil.rmtree, root / 'stage', True)

    result['restart'] = True
    _schedule_restart(hass)
    _LOGGER.info('已替换到 %s（上一版 %s），准备重启', result['installed'], result['previous'])
    return result


async def _download(hass, session, url: str, dest: Path, expected: str) -> None:
    """边下边算 sha256。写盘与删除都扔进 executor——在事件循环里 open/write 会被 HA 记警告。"""
    digest = hashlib.sha256()
    total = 0
    handle = await hass.async_add_executor_job(open, dest, 'wb')
    try:
        async with session.get(url, timeout=120) as response:
            if response.status != 200:
                raise UpdateError('not_found', f'包地址返回 {response.status}')
            async for chunk in response.content.iter_chunked(CHUNK):
                total += len(chunk)
                if total > MAX_BUNDLE_BYTES:
                    raise UpdateError('too_large', '包超过大小上限')
                digest.update(chunk)
                await hass.async_add_executor_job(handle.write, chunk)
    except UpdateError:
        await hass.async_add_executor_job(handle.close)
        await hass.async_add_executor_job(dest.unlink, True)
        raise
    except Exception as err:  # noqa: BLE001
        await hass.async_add_executor_job(handle.close)
        await hass.async_add_executor_job(dest.unlink, True)
        raise UpdateError('network', f'下载失败：{err}') from err
    await hass.async_add_executor_job(handle.close)

    if digest.hexdigest() != expected:
        dest.unlink(missing_ok=True)
        raise UpdateError('checksum', 'sha256 校验不符，包可能被截断或源上换了文件')


def _schedule_restart(hass) -> None:
    """先让 WebSocket 回包，再重启——否则前端只能看到连接被掐断。"""
    import asyncio

    async def _later():
        await asyncio.sleep(1.5)
        await hass.services.async_call('homeassistant', 'restart', blocking=False)

    hass.async_create_task(_later())
