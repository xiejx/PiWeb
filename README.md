<p align="center">
  <img src="docs/logo.svg" alt="PiWeb — 树莓派 Web 控制面板" width="720">
</p>

# PiWeb — 树莓派 Web 控制面板与仪表盘

**PiWeb** 是一个轻量级、可自托管的**树莓派 Web 控制面板**。你可以在任意浏览器里监控树莓派、**操作 GPIO 引脚**、管理 **systemd 服务**与进程、执行 shell 命令、编辑脚本，并通过类 REST 的 **HTTP API** 触发自定义**命令按钮**。

## 目录

- [PiWeb — 树莓派 Web 控制面板与仪表盘](#piweb--树莓派-web-控制面板与仪表盘)
  - [目录](#目录)
  - [功能特性](#功能特性)
  - [快速安装](#快速安装)
    - [用首次运行向导代替自动生成密码](#用首次运行向导代替自动生成密码)
  - [手动安装](#手动安装)
  - [配置](#配置)
    - [模块](#模块)
    - [数据目录](#数据目录)
  - [Button API](#button-api)
    - [TeHyBug 传感器上报端点](#tehybug-传感器上报端点)
  - [开发](#开发)
  - [卸载](#卸载)
  - [安全提示](#安全提示)
  - [致谢与许可](#致谢与许可)

***

## 功能特性

- **仪表盘** —— 实时 CPU 占用、温度、内存/交换分区/磁盘、运行时长、负载均值、在线用户、各网卡统计（IP、收发流量、Wi-Fi 信号）、树莓派供电/降频状态、可配置的服务徽章，以及已连接的 USB 与块设备。通过 HTMX 每 5 秒刷新，无需重新加载页面。
- **服务管理** —— 列出 systemd 管理的各类单元（service、socket、timer、mount、automount、path、swap、target），按单元类型分区展示：活动状态与子状态、开机自启行为；可按状态（全部 / 活动 / 失败 / 未激活）筛选、按名称或描述搜索；启动、停止、重启单元，或一键切换 `enable` / `disable`。
- **进程管理** —— 按内存占用排序浏览进程，在浏览器内过滤，按 PID 或名称结束进程。
- **GPIO 控制** —— 查看并切换所有排针引脚的模式（IN/OUT）、电平（HIGH/LOW）与上下拉；自动识别 WiringPi（Pi 1–4）或 raspi-gpio（Pi 5）。
- **树莓派工具** —— 通过 `vcgencmd` 查看固件、时钟、电压、内存分配与编解码器；启用/禁用 I2C、SPI、1-Wire、SSH、VNC 和摄像头；编辑 `config.txt` / `cmdline.txt` 时自动备份；实时温度与 CPU 频率曲线。
- **软件包更新** —— 列出可升级的 apt 包，刷新索引，一键在浏览器中升级全部软件包。
- **系统日志** —— 浏览 systemd journal、`dmesg` 和 `/var/log` 文件，可调整行数并过滤。
- **Cron 任务** —— 查看、添加、删除用户的 crontab 条目；只读查看 `/etc/crontab`；一键把已保存的脚本加入计划任务。
- **用户与用户组** —— 从 `/etc/passwd` 和 `/etc/group` 只读列出系统账户。
- **Docker** —— 列出容器，启动/停止/重启/暂停/删除容器，查看日志与镜像列表（默认关闭）。
- **命令按钮** —— 为任意 shell 命令创建一键按钮，可选择二次确认弹窗或直接执行，结果就地显示，支持拖拽排序。
- **Button API** —— 可选功能：每个按钮生成一个密钥，让 curl、Home Assistant 或任何自动化工具无需登录即可触发；可按 IP 限制访问。
- **脚本编辑器** —— 使用 **CodeMirror 6**（已内置，离线可用）编辑 shell 和 Python 脚本；前台或后台运行；可从树莓派示例起步（GPIO、DHT11/22、BME280、DS18B20、HC-SR04、PIR、LDR、I2C 扫描、备份、Wi-Fi 扫描等）。脚本存放于 `user_scripts/`，永远不会作为静态文件对外提供。
- **仪表盘脚本磁贴** —— 最多固定六个脚本，在仪表盘上显示其最近一次输出。
- **备份与恢复** —— 把配置、按钮、菜单顺序和脚本打包成单个 JSON 文件下载；SD 卡损坏后可据此恢复。
- **告警** —— 通过 **ntfy** 或 **Telegram** 推送高温、欠压、磁盘将满和连续登录失败的通知。
- **两步登录** —— 可选的 TOTP 动态口令，兼容任意验证器 App，在「系统检查」中完成设置。
- **模块管理页** —— 无需编辑文件即可启用/禁用任意内置模块；可把外部工具（Grafana、Node-RED 等）配置为模块，并在沙箱 iframe 中展示。
- **系统检查** —— 检查运行环境、安全性、目录权限、执行后端与 GPIO 工具，对常见问题提供一键修复。
- **运行信息** —— Python/操作系统/依赖版本、路径与脱敏后的配置（替代旧的 `phpinfo` 页面）。
- **深色主题**、**菜单拖拽排序**，以及**五种语言**（英语、德语、乌克兰语、西班牙语、法语）。
- **身份认证** —— 登录页、可选的 HTTP Basic Auth、可选 TOTP、密码哈希（bcrypt / pbkdf2）、暴力破解限速、空闲/绝对会话超时，以及登录审计日志。

***

## 快速安装

在树莓派上（通过 SSH），进入已检出的仓库或新下载的代码目录执行：

```bash
git clone https://github.com/xiejx/PiWeb.git
cd PiWeb
bash installer.sh
```

或者不预先克隆：

```bash
wget -O installer.sh https://raw.githubusercontent.com/xiejx/PiWeb/master/installer.sh
bash ./installer.sh
```

安装器会：

1. 安装 `git`、`python3-venv`、`nginx` 以及 WiringPi / `raspi-gpio`；
2. 创建 `piweb` 服务用户，并将其加入 `gpio`、`video`、`i2c`、`spi` 和 `docker` 组；
3. 创建 `/opt/piweb/.venv` 并安装 `requirements.txt`；
4. 写入 `config.toml`，其中包含一个**随机生成的管理员密码**（仅打印一次），以及面板所需命令的 `sudoers` 配置；
5. 安装并启动 `piweb.service`（Uvicorn）和 Nginx 站点。

打开 `http://<树莓派地址>/`，用安装结束时打印的凭据登录（用户名为 `admin`）。

> **HTTPS：** 在把面板暴露到公网之前，请运行 `sudo certbot --nginx`，并在 `config.toml` 中设置 `session.cookie_secure = true`。

### 用首次运行向导代替自动生成密码

如果没有配置凭据（例如手动运行时），PiWeb 会跳转到 `/setup`。出于安全考虑，该页面只接受来自树莓派本机的请求，请在你的电脑上通过 SSH 隧道访问：

```bash
ssh -L 8000:127.0.0.1:8000 pi@raspberrypi
# 然后打开 http://127.0.0.1:8000/setup
```

设置表单会把**哈希后的密码**写回 `config.toml` 的 `[auth]` 段。

***

## 手动安装

```bash
sudo apt-get install -y python3 python3-venv nginx
sudo useradd --system --create-home --home-dir /opt/piweb --shell /usr/sbin/nologin piweb
sudo git clone https://github.com/xiejx/PiWeb.git /opt/piweb
sudo chown -R piweb:piweb /opt/piweb
sudo -u piweb python3 -m venv /opt/piweb/.venv
sudo -u piweb /opt/piweb/.venv/bin/pip install -r /opt/piweb/requirements.txt

sudo cp /opt/piweb/config.example.toml /opt/piweb/config.toml
sudo -u piweb /opt/piweb/.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, "/opt/piweb")
from app.config import save_auth_credentials
from app.security import hash_password
save_auth_credentials("/opt/piweb/config.toml", "admin", hash_password("change-me-now"))
PY
sudo chown piweb:piweb /opt/piweb/config.toml && sudo chmod 600 /opt/piweb/config.toml

sed 's|@PIWEB_DIR@|/opt/piweb|g' /opt/piweb/deploy/piweb.service | sudo tee /etc/systemd/system/piweb.service
sed 's|@PIWEB_DIR@|/opt/piweb|g' /opt/piweb/deploy/piweb-nginx.conf | sudo tee /etc/nginx/sites-available/piweb
sudo ln -sf /etc/nginx/sites-available/piweb /etc/nginx/sites-enabled/piweb
sudo install -m 0440 /opt/piweb/deploy/piweb-sudoers /etc/sudoers.d/piweb

sudo systemctl daemon-reload
sudo systemctl enable --now piweb nginx
sudo nginx -t && sudo systemctl reload nginx
```

查看服务状态与日志：

```bash
systemctl status piweb
sudo journalctl -u piweb -f
```

***

## 配置

所有设置都在 `app/` 同级目录的 `config.toml` 中（从 `config.example.toml` 复制而来）。该文件只在启动时读取一次，修改后需重启服务：

```bash
sudo systemctl restart piweb
```

| 段                  | 作用                                                                                     |
| ------------------ | -------------------------------------------------------------------------------------- |
| `[server]`         | 绑定地址/端口、根路径、是否信任来自 Nginx 的 `X-Real-IP`                                                 |
| `[execution]`      | `local`（以服务用户身份通过 `sudo` 执行命令）或 `ssh`（Paramiko，类似 1.x 的行为）                             |
| `[ssh]`            | `mode = "ssh"` 时的主机、端口、用户和密码                                                           |
| `[auth]`           | 登录开关、用户名、密码/哈希、可选的 `check_system_user`、TOTP 密钥、`allow_unauthenticated`、应急 `update_key` |
| `[auth.basic]`     | 可选的 HTTP Basic Auth                                                                    |
| `[session]`        | 空闲与绝对超时时间、`cookie_secure`                                                              |
| `[login_throttle]` | 登录失败次数上限、时间窗口与锁定时长                                                                     |
| `[alerts]`         | ntfy URL、Telegram token/chat、温度与磁盘阈值                                                   |
| `[ui]`             | 默认语言、仪表盘服务徽章、Button API 的 IP 白名单、debug 开关                                              |
| `[modules.*]`      | 模块标题、分组、启用标志，以及外部模块的 `url` / `iframe`                                                  |

密码可以是明文、`$2y$`/`$2b$` 开头的 bcrypt 哈希，或 `pbkdf2_sha256$...` 哈希。**强烈建议存储哈希**：

```bash
sudo -u piweb /opt/piweb/.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "/opt/piweb")
from app.security import hash_password
print(hash_password("your-new-password"))
PY
```

### 模块

`/modules` 页面可以在不改动文件的情况下开关任意模块。选择结果保存在 `data/modules_state.json`，并在启动时覆盖 `[modules.*]` 的配置。外部工具也可以作为模块加入：

```toml
[modules.grafana]
title = "Grafana"
group = "System"
enabled = true
url = "http://127.0.0.1:3000"
iframe = true
```

### 数据目录

| 路径                     | 内容                                       |
| ---------------------- | ---------------------------------------- |
| `config.toml`          | 配置文件（权限 `600`，含密钥）                       |
| `buttons/buttons.json` | 命令按钮及其 API 密钥                            |
| `user_scripts/`        | 你的脚本及 `.pins.json`；不会通过 HTTP 对外提供        |
| `command_logs/`        | 后台命令输出、`auth.log`、`api_calls.log`        |
| `data/`                | 会话密钥、菜单顺序、模块状态、告警状态、TeHyBug 的 SQLite 数据库 |

安装器会把这些目录的属主设为 `piweb`。迁移已有安装时请保持相同的目录结构。

***

## Button API

在界面中创建的每个按钮都会获得一个 32 位密钥。启用 `button_api` 模块后即可触发：

```bash
# 路径形式
curl "http://raspberrypi/api/button/<key>"

# 兼容 1.x 的旧形式（保证已有自动化继续可用）
curl "http://raspberrypi/api.php?hash=<key>"

# 把密钥放在请求头里，而不是 URL 中
curl -H "X-PiWeb-Key: <key>" http://raspberrypi/api/button

# POST 同样可用
curl -X POST -d "hash=<key>" http://raspberrypi/api.php
```

响应为 JSON：成功时形如 `{"success": true, "button": "...", "output": "..."}`，失败时形如 `{"success": false, "error": "..."}`。可用 `ui.api_allow_ips = ["192.168.1.0/24"]` 限制访问来源。每次调用都会写入 `command_logs/api_calls.log`，其中包含截断后的密钥、来源 IP 和 User-Agent。

### TeHyBug 传感器上报端点

```bash
# ESP8266/ESP32 传感器上报温度、湿度与气压
curl "http://raspberrypi/modules/tehybug/track?bug_key=<key>&t=21.4&h=48&p=1013"
```

该模块默认关闭；可通过 `[modules.tehybug]` 或 `/modules` 页面启用。

***

## 开发

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

运行测试（pytest 仅测试时需要，未列入 `requirements.txt`）：

```bash
pip install pytest
python -m pytest tests -q
```

CodeMirror 6 的打包文件已预构建在 `app/static/vendor/codemirror.bundle.js`。修改 `.frontend-build/editor-entry.js` 后可按如下方式重新构建：

```bash
cd .frontend-build
npm install
npx esbuild editor-entry.js --bundle --minify --format=iife --outfile=../app/static/vendor/codemirror.bundle.js
```

前端资源全部本地内置于 `app/static/vendor/`，因此面板可以完全离线工作。

***

## 卸载

```bash
bash uninstall.sh
```

该脚本会停止服务、移除 Nginx 站点和 `sudoers` 配置，并可选地删除 `/opt/piweb`；它会先询问是否把配置和数据备份到你的主目录。当 PiWeb 是唯一启用的站点时，脚本还会恢复发行版默认的 Nginx 站点，并询问是否清除服务用户 crontab 中的计划任务。Nginx、Git、Python 和 WiringPi 会保留。

***

## 安全提示

- `config.toml` 保存了会话密钥，可能还有 SSH 密码：请保持权限 `600`，并不要提交到 git（默认已忽略）。
- 面板一旦离开局域网，请设置 `session.cookie_secure = true` 并启用 HTTPS（Certbot）。
- 优先使用 `execution.mode = "local"` 配合权限收敛的 `sudoers` 文件，而不是存储高权限账户的 SSH 密码。
- Button API 密钥属于 bearer 凭证：拿到 URL 的人就能执行对应命令。除非确实需要，请保持 `button_api` 关闭，并限制 `api_allow_ips`。
- 启动配置编辑和软件包升级是高危操作；只把面板访问权限交给你愿意给 shell 权限的人。

***

## 致谢与许可

PiWeb基于[gumslone/GumCP](https://github.com/gumslone/GumCP)，对其进行了大幅重构。

本项目基于 **MIT License** 发布，详见 [LICENSE](LICENSE)。
