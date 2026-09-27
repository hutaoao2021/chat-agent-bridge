# Chat Agent Bridge

通过普通 ChatGPT 对话，读取和修改已授权的本地/SSH 项目，批准命令并自动续接任务。Windows 管理软件集中管理启动、工作区、Tunnel、代理、首次配对和诊断。

独立实现，不依赖 Chat On Steroids；不调用模型推理 API。它不会增加账号额度。

## 开始使用

从 [版本下载](https://github.com/hutaoao2021/chat-agent-bridge/releases) 下载 Windows x64 安装程序。自带 Python 和官方 Tunnel，登录 Windows 后可自动后台运行。

- [首次安装 / 换电脑](docs/new-computer.md)
- [日常使用和工作流程](docs/user-guide.md)
- [故障排查](docs/troubleshooting.md)
- [构建安装包](docs/build.md) / [源码运行](docs/setup.md)

0.2.0 是预览版，实际验收范围见 [验证记录](docs/verification.md)。第一次需要配置自己的账号 Tunnel 和浏览器扩展；每个工作对话启用一次，以后直接发送需求，其他对话保持普通聊天。命令仍由你批准。

## 组成

- Python MCP 服务：工作区文本工具、Git、持久后台任务、SSH、SQLite 检查点与幂等记录。
- 独立配对 API：扩展只能读取任务状态、审批和获取续接资格。
- Chrome / Edge MV3 扩展：每个对话主动启用；遇到额度提示、手动输入、刷新时发送不明或轮数上限会暂停。

## 本地验证

```powershell
.venv\Scripts\python -m pytest -q
npm --prefix extension test
```

用户数据在 `%LOCALAPPDATA%\ChatAgentBridge\data`，不随源码公开。第三方许可见 [声明](THIRD-PARTY-NOTICES.md)。本项目未选择自身源码的开源许可。
