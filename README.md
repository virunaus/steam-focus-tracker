# Steam Focus Tracker

一个 Windows 桌面应用，后台静默记录你每天在 Steam 上的在线时长，帮你看见时间花在了哪。

## 🎯 为什么做这个

我做这个软件的原因很直接：我发现自己每天在 Steam 上花的时间远超预期。

靠意志力硬扛很难，于是换了个思路——**把时间花在哪"看见"**。当每天的游戏时长变成一张柱状图、一个数字摆在那里，比任何自我提醒都管用。这个工具不是为了统计而统计，而是为了让数据本身成为一种约束，把注意力拉回到学习上。

## ✨ 功能

- **每日在线统计**：今日时长、本周累计、本周日均，最近 7 天柱状图
- **多账号支持**：自动扫描本机所有 Steam 账号，下拉切换查看各自数据
- **游戏时长排行**：按累计时长排序，支持搜索过滤
- **Steam Web API 同步**：填入 API Key 后一键拉取完整游戏库（含已卸载游戏），自动补全游戏名
- **系统托盘后台运行**：关闭窗口最小化到托盘继续计时，开机自启
- **纯本地运行**：不收集任何数据，所有记录保存在本地 JSON 文件

## 🖥 技术栈

| 模块 | 技术 |
|------|------|
| GUI | Python 3 + Tkinter / ttk |
| 系统托盘 | pystray + Pillow |
| 本地数据解析 | 自实现 VDF（Valve KeyValue）递归下降解析器 |
| Steam API | urllib 调用 GetOwnedGames / GetPlayerSummaries |
| 进程监控 | tasklist 轮询 steam.exe |
| 打包 | PyInstaller（单文件 exe） |

## 🏗 架构

```
steam_tracker.py          # 单文件，约 700 行
├── find_steam_path()     # 注册表探测 Steam 安装路径
├── parse_vdf()           # 递归下降解析 VDF/ACF 配置文件
├── scan_accounts()       # 扫描 userdata 下所有账号
├── is_steam_running()    # tasklist 轮询
├── api_fetch_owned_games()  # Steam Web API 客户端
└── App (Tkinter)         # 多标签 GUI + 后台监控线程
```

数据流向：

```
steam.exe 进程 ──轮询──▶ 今日秒数累计 ──▶ tracker_data.json
localconfig.vdf ──VDF解析──▶ 各游戏累计分钟
Steam Web API ──HTTP──▶ 完整游戏库 + 游戏名
```

## 🚀 使用

1. 下载 `Steam在线时长.exe`，双击运行
2. （可选）点「设置」填入 Steam Web API Key（[免费申请](https://steamcommunity.com/dev/apikey)）
3. 点「☁ 从 Steam 同步」拉取完整游戏库
4. 关掉窗口会最小化到托盘，后台继续计时

## 🔨 从源码构建

```
pip install -r requirements.txt
python steam_tracker.py            # 开发运行
pyinstaller --onefile --windowed --name "Steam在线时长" steam_tracker.py
```

## 🔒 隐私

- API Key 仅保存在本地 `config.json`，不上传任何服务器
- 所有使用记录保存在本地 `tracker_data.json`
- 不读取、不上传任何账号密码
- 
