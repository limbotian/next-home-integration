# Next Home

Home Assistant 的户型面板与本地存储集成。楼层户型、底图、空间风格、通知已读都存放在本机，
不上云。

## 安装（HACS）

1. HACS → 右上角「自定义存储库」→ 填入本仓库地址，类型选 **Integration**
2. 搜「Next Home」→ 下载
3. 重启 Home Assistant

面板静态资源由集成在启动时自动补到 `/config/www/next-home-studio/`，不需要手动拷。

## 安装（手动）

1. 把 `custom_components/next_home/` 放进 HA 的 `/config/custom_components/`
2. 重启 Home Assistant
3. 设置 → 设备与服务 → 添加集成 → 搜「Next Home」

老版本靠 `configuration.yaml` 里的 `next_home:` 启用的，升级后会自动导入成配置项，
不用改 YAML。

## 要求

- Home Assistant **2026.8.0** 及以上
- 无额外 Python 依赖

## 版本

版本号与镜像内预置的一致，见 `manifest.json` 的 `version`。
