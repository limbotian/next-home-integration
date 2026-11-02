"""Next Home 的常量。放在这里是为了让 `config_flow` 不必从 `__init__` 反向导入。"""

DOMAIN = 'next_home'

# 家庭共享档案变更的广播事件（登录态 / 皮肤 / 空间风格）。前端订阅它做免刷新跟随，
# 见 openspec 变更 `shared-home-profile` 的设计 D6。
EVENT_SHARED = 'next_home_shared_changed'

# 侧边栏面板：由本集成在启动时注册（见 `async_register_panel`）。参数与
# `frontend/public/next-home-panel.js` 里定义的自定义元素一一对应；`?v=` 是 HA 前端对
# 该模块的缓存键，改 bridge 时一起改。
PANEL_URL_PATH = 'next-home-studio'
PANEL_ELEMENT = 'next-home-panel'
PANEL_MODULE_URL = '/local/next-home-studio/next-home-panel.js?v=6'
PANEL_SIDEBAR_TITLE = 'Next Home'
PANEL_SIDEBAR_ICON = 'mdi:floor-plan'
# frontend 的面板登记表（`homeassistant.components.frontend.DATA_PANELS` 的键名）。
PANEL_REGISTRY_KEY = 'frontend_panels'
