"""把集成自带的静态面板同步到 `/config/www`。

为什么需要：用 HACS 装的时候，HACS 只搬 `custom_components/next_home/` 这一个目录，
`/config/www/next-home-studio/` 这种目录外的静态资源它管不到。面板模块路径
`/local/next-home-studio/next-home-panel.js` 是 Next.js 导出时就写死的（basePath），
改路径要重编前端，所以这里走"集成自带一份、启动时补到 www"的路子。

出厂镜像不受影响：payload 已经把面板放进 `/config/www` 了，两边 release.json 一样就直接跳过。
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

FOLDER = 'next-home-studio'


def _release_of(folder: Path) -> str | None:
    """release.json 里的版本标识。

    新格式是 JSON（`{"release": "<时间戳>"}`，`pack-payload.sh` 生成），老格式可能是纯文本，
    两种都认——这里只负责「两份是不是同一版」，不负责判断它指向的入口存不存在。
    """
    marker = folder / 'release.json'
    if not marker.is_file():
        return None
    try:
        raw = marker.read_text(encoding='utf-8').strip()
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return raw or None
    if isinstance(data, dict):
        value = data.get('release')
        if isinstance(value, str) and value.strip():
            return value.strip()
    return raw or None


def _ensure_entry(folder: Path) -> str | None:
    """`release.json` 必须指向真实存在的 `app/entry-<release>.html`。

    2026-11-03 踩过：发布包里只换了 release.json、没带对应入口，桥就 iframe 到一个 404——
    文件都在、HTTP 全 200，只有浏览器里是白的，非常难查。这里兜底改成最新的那个入口。
    """
    app = folder / 'app'
    if not app.is_dir():
        return None
    ids = sorted(path.name[len('entry-'):-len('.html')] for path in app.glob('entry-*.html'))
    if not ids:
        return None
    current = _release_of(folder)
    if current in ids:
        return None
    pick = ids[-1]
    (folder / 'release.json').write_text(
        json.dumps({'release': pick}, ensure_ascii=False) + "\n", encoding='utf-8')
    _LOGGER.warning('面板 release.json 指向 %s，包里只有 %s；已改指 %s', current, ids, pick)
    return pick


def _sync(src: Path, dst: Path) -> str:
    """真正搬文件。返回一句话结果，供日志用。"""
    if not src.is_dir():
        return '集成里没有自带面板资源，跳过'
    if _release_of(src) == _release_of(dst):
        return '面板已是最新，跳过'
    dst.mkdir(parents=True, exist_ok=True)
    # 先清掉旧的 app/，避免上一次 release 的 entry-*.html 越积越多
    for stale in ('app', 'next-home-panel.js', 'release.json'):
        target = dst / stale
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
    shutil.copytree(src, dst, dirs_exist_ok=True)
    _ensure_entry(dst)
    return f'面板已同步到 {dst}'


async def async_sync_panel(hass) -> None:
    src = Path(__file__).parent / 'www' / FOLDER
    dst = Path(hass.config.path('www', FOLDER))
    try:
        message = await asyncio.get_running_loop().run_in_executor(None, _sync, src, dst)
    except Exception:  # noqa: BLE001 —— 同步失败不能把集成拖挂，面板没上由部署探针发现
        _LOGGER.exception('同步 Next Home 面板资源失败')
        return
    _LOGGER.debug('Next Home %s', message)
