/* HA owns authentication and the websocket. Only the same-origin child receives this bridge. */
/**
 * 面板显示偏好里与 HA 外壳相关的键（与 `features/dashboard/homePrefs.ts` 的 `HOME_PREFS_KEY` 同值）。
 * 桥与面板同源，所以桥能直接读同一份 localStorage——挂载那一刻就知道该不该收侧栏，
 * 不会出现「先看见侧栏、再被收起」的闪动。
 */
const HOME_PREFS_KEY = 'next-home:home-prefs';
/** iframe 通知桥调整侧栏的消息类型（`features/home-assistant/embed.ts` 同值）。 */
const SIDEBAR_MESSAGE_TYPE = 'next-home:sidebar';

class NextHomePanel extends HTMLElement {
  constructor() {
    super();
    this._sidebar = null;
    this._sidebarDisplay = '';
    this._main = null;
    this._mainWidth = '';
    this._shell = null;
    this._shellDisplay = '';
    /** `true` = 用户要求收起（偏好或消息）；默认 false，即**不接管** HA 的外壳。 */
    this._wanted = false;
    this._onMessage = (event) => {
      // 只认同源消息，且 `data.type` 精确匹配：HA 自己的、浏览器扩展的消息一概不碰。
      if (event.origin !== location.origin) return;
      const data = event.data;
      if (!data || data.type !== SIDEBAR_MESSAGE_TYPE) return;
      this.setSidebarWanted(data.hidden === true);
    };
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML = `<style>:host{display:block;height:100%;min-height:100dvh}iframe{display:block;border:0;width:100%;height:100dvh}</style><iframe title="Next Home" allow="fullscreen; screen-wake-lock"></iframe>`;
    this.frame = this.shadowRoot.querySelector('iframe');
    this.frame.addEventListener('load', () => this.publish());
    this.loadRelease();
  }
  connectedCallback() {
    window.addEventListener('message', this._onMessage);
    // 默认**不接管**（2026-10-26 用户要求）：只有偏好打开时才收侧栏。
    this.setSidebarWanted(this.readPreference());
  }
  disconnectedCallback() {
    window.removeEventListener('message', this._onMessage);
    this.restoreSidebar();
  }
  /** 读同源存储里的「自动隐藏侧边栏」；读不到（无权限 / 坏 JSON / 缺项）按不收起处理。 */
  readPreference() {
    try {
      const raw = localStorage.getItem(HOME_PREFS_KEY);
      if (!raw) return false;
      const prefs = JSON.parse(raw);
      return Boolean(prefs && prefs.autoHideSidebar === true);
    } catch {
      return false;
    }
  }
  /**
   * 用户要求的收起状态变化（偏好打开 / 关掉，或 iframe 发来消息）。
   *
   * **打开时立即生效**：面板已经在屏上，不必重新进入；关掉时立刻把侧栏放回去。
   * 记住 `_wanted` 是因为 `hass` 更新会回头再调一次收起，那条路径只应该在
   * 「仍然想要收起」时生效——否则默认配置下也会动 HA 的 DOM。
   */
  setSidebarWanted(wanted) {
    this._wanted = wanted;
    if (wanted) this.collapseSidebar();
    else this.restoreSidebar();
  }
  /**
   * 收起 HA 自己的侧边栏：面板要整屏时用。
   *
   * HA 没有给 `panel_custom` 提供「隐藏侧栏」的开关，侧栏是 `home-assistant-main`
   * 影子树里的一个兄弟节点 `ha-sidebar`——只能从面板这一侧改它（HACS 的 kiosk-mode 同一手法，
   * 只是它作为前端模块跑在页面里，我们靠面板元素本身就在那棵影子树里，一路往上找）。
   *
   * **宽度是变量给的，不是子元素撑的**（2026-10-26 第一版踩到）：`ha-drawer` 里
   * `.sidebar-shell { width: var(--ha-sidebar-width) }`、`.app-content { padding-inline-start:
   * var(--ha-sidebar-width) }`——只把 `ha-sidebar` 藏掉，内容没了但那 56 px 的空白还在。
   * 所以同时把 `home-assistant-main` 上的 `--ha-sidebar-width` 归零（覆盖 `:host` 与
   * `:host([expanded])` 的 256px），并顺手藏掉抽屉里的侧栏壳（它自带 1px 分隔线）。
   *
   * **安全失败**：HA 升级改了结构就找不到，那时什么都不做、侧栏照常显示——面板照常能用。
   */
  collapseSidebar() {
    try {
      let root = this.getRootNode();
      while (root && root.host) {
        const sidebar = root.querySelector && root.querySelector('ha-sidebar');
        if (sidebar) {
          if (this._sidebar !== sidebar) {
            this._sidebar = sidebar;
            this._sidebarDisplay = sidebar.style.display;
          }
          if (sidebar.style.display !== 'none') sidebar.style.display = 'none';

          const main = root.host;
          if (main && main.style) {
            if (this._main !== main) {
              this._main = main;
              this._mainWidth = main.style.getPropertyValue('--ha-sidebar-width');
            }
            main.style.setProperty('--ha-sidebar-width', '0px');
          }

          const drawer = root.querySelector('ha-drawer');
          const shell =
            drawer && drawer.shadowRoot && drawer.shadowRoot.querySelector('.sidebar-shell');
          if (shell) {
            this._shell = shell;
            this._shellDisplay = shell.style.display;
            shell.style.display = 'none';
          }
          return;
        }
        root = root.host.getRootNode();
      }
    } catch {
      /* 结构不符：保持原样 */
    }
  }
  /** 离开面板（或用户关掉偏好）时把侧栏放回去：只在面板里整屏，不接管 HA 的其它页面。 */
  restoreSidebar() {
    const sidebar = this._sidebar;
    if (!sidebar) return;
    try {
      sidebar.style.display = this._sidebarDisplay || '';
    } catch {
      /* 节点已被移除：没有可恢复的东西 */
    }
    if (this._main && this._main.style) {
      try {
        if (this._mainWidth) this._main.style.setProperty('--ha-sidebar-width', this._mainWidth);
        else this._main.style.removeProperty('--ha-sidebar-width');
      } catch {
        /* 同上 */
      }
    }
    if (this._shell) {
      try {
        this._shell.style.display = this._shellDisplay || '';
      } catch {
        /* 同上 */
      }
    }
    this._sidebar = null;
    this._sidebarDisplay = '';
    this._main = null;
    this._mainWidth = '';
    this._shell = null;
    this._shellDisplay = '';
  }
  async loadRelease() {
    try {
      const response = await fetch(`/local/next-home-studio/release.json?v=${Date.now()}`, {
        cache: 'no-store',
      });
      if (!response.ok) throw new Error('Release lookup failed');
      const { release } = await response.json();
      if (!/^\d{8}-\d{6}$/.test(release)) throw new Error('Invalid release');
      this.frame.src = `/local/next-home-studio/app/entry-${release}.html`;
    } catch {
      const message = document.createElement('p');
      message.textContent = 'Next Home 加载失败，请刷新页面重试。';
      this.shadowRoot.append(message);
    }
  }
  set hass(value) {
    this._hass = value;
    this.publish();
    // HA 每次状态变化都会重设 hass；侧栏若被 HA 自己重建回来，这里顺手再收一次。
    // 只在「用户仍然想要收起」时生效——默认配置下不碰 HA 的 DOM。
    if (this._wanted && this._sidebar && this._sidebar.style.display !== 'none') {
      this.collapseSidebar();
    }
  }
  get hass() {
    return this._hass;
  }
  publish() {
    const win = this.frame.contentWindow;
    if (!win || !this._hass) return;
    try {
      if (win.location.origin !== location.origin) return;
      win.nextHomeHass = this._hass;
      win.dispatchEvent(new win.Event('next-home-hass'));
    } catch {
      /* The frame may be between navigations. */
    }
  }
}
if (!customElements.get('next-home-panel')) customElements.define('next-home-panel', NextHomePanel);
