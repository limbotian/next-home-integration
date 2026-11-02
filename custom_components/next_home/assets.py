"""把集成自带的静态面板同步到 `/config/www`。

为什么需要：用 HACS 装的时候，HACS 只搬 `custom_components/next_home/` 这一个目录，
`/config/www/next-home-studio/` 这种目录外的静态资源它管不到。面板模块路径
`/local/next-home-studio/next-home-panel.js` 是 Next.js 导出时就写死的（basePath），
改路径要重编前端，所以这里走"集成自带一份、启动时补到 www"的路子。

出厂镜像不受影响：payload 已经把面板放进 `/config/www` 了，两边 release.json 一样就直接跳过。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

FOLDER = 'next-home-studio'


def _release_of(folder: Path) -> str | None:
    marker = folder / 'release.json'
    if not marker.is_file():
        return None
    try:
        return marker.read_text(encoding='utf-8').strip()
    except OSError:
        return None


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
