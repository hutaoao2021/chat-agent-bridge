# 源码运行（开发者入口）

日常用户请使用 [Windows 安装程序](new-computer.md)。本页仅用于源码开发。

```powershell
py -3.14 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.lock
.venv/Scripts/python -m pip install --no-deps -e .
Copy-Item config.example.toml config.local.toml
./scripts/start.ps1
```

配置默认只读，项目目录自行选择，修改配置后重启。开发测试运行 `python -m pytest -q` 和 `node --test extension/tests/*.test.js`。源码服务的默认 MCP 为 `http://127.0.0.1:8899/mcp`，扩展控制服务为 `http://127.0.0.1:8900`。只把 MCP 服务接入 Tunnel。

账号注册和官方 Tunnel 接入见 [新电脑安装](new-computer.md)；工具行为、命令审批和续接见 [使用手册](user-guide.md)。Git/SSH 使用本机已有工具。

旧版 `scripts/install-autostart.ps1` 和 `scripts/autostart.py` 仍保留用于已有源码安装。新 Windows 软件使用独立的 supervisor，不需要执行旧启动脚本。
