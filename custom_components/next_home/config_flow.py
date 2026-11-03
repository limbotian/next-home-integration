"""Next Home 的配置流程：界面上点「添加集成」即可，不再只能改 YAML。

为什么要有它（2026-11-02）：给已有 HA 的用户装 Next Home 时，改 `configuration.yaml` 加
`next_home:` 是硬门槛——改错一行整个 HA 起不来。有了 config_flow，「设置 → 设备与服务 →
添加集成 → Next Home → 提交」就能完成。

YAML 的老路子保留：`async_setup` 见到 `next_home:` 会走 `async_step_import` 自动建一条
配置项，老用户的 configuration.yaml 不用动。
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN
from .updater import CONF_ALLOW_INSECURE, CONF_MANIFEST_URL, UPDATE_MANIFEST_URL

_LOGGER = logging.getLogger(__name__)

TITLE = 'Next Home'


class NextHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """单实例：一个 HA 只有一份户型存储，不允许添加第二个。"""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return NextHomeOptionsFlow()

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title=TITLE, data={})

        # 没有可填的字段：面板入口、侧边栏标题都按常量走，少一个表单变量就少一处配错。
        return self.async_show_form(step_id='user', data_schema=vol.Schema({}))

    async def async_step_import(self, import_info: dict | None = None) -> FlowResult:
        """configuration.yaml 里写了 `next_home:` 时自动建条目（老用户零改动）。"""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        _LOGGER.info('从 configuration.yaml 导入 Next Home')
        return self.async_create_entry(title=f'{TITLE}（YAML）', data={})


class NextHomeOptionsFlow(config_entries.OptionsFlow):
    """自更新的发布源。默认值就是我们的正式源，客户不用动；内网调试才改这里。"""

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title='', data=user_input)

        current = self.config_entry.options
        schema = vol.Schema({
            vol.Optional(CONF_MANIFEST_URL,
                         default=current.get(CONF_MANIFEST_URL, UPDATE_MANIFEST_URL)): str,
            vol.Optional(CONF_ALLOW_INSECURE,
                         default=bool(current.get(CONF_ALLOW_INSECURE, False))): bool,
        })
        return self.async_show_form(step_id='init', data_schema=schema)
