# Chat Agent Bridge

通过已接入 Bridge 的 ChatGPT 对话，按授权读取或修改本地/SSH 项目；命令经审批后执行。扩展可自动发送续接消息，但当前不会替后续消息选用 ChatGPT App，因此不能保证续接消息仍可调用 Bridge 工具。Windows 管理软件集中管理启动、工作区、Tunnel、代理、首次配对和诊断。

独立实现，不依赖 Chat On Steroids；不调用模型推理 API。它不会增加账号额度。

## 开始使用

从 [版本下载](https://github.com/hutaoao2021/chat-agent-bridge/releases) 下载 Windows x64 安装程序。自带 Python 和官方 Tunnel，勾选并保存登录后自动运行后，可在当前用户登录时启动后台服务。

- [首次安装 / 换电脑](docs/new-computer.md)
- [日常使用和工作流程](docs/user-guide.md)
- [故障排查](docs/troubleshooting.md)
- [构建安装包](docs/build.md) / [源码运行](docs/setup.md)

0.2.3 是预览版，实际验收范围见 [验证记录](docs/verification.md)。首次需要配置自己的账号 Tunnel、在 ChatGPT 创建开发者模式 App 并配对浏览器扩展；无需另下载 App。扩展启用的工作模式会按对话记住，但需要 Bridge 工具的消息仍须在 ChatGPT 中选用 App 或提及它；App 选择只对当前消息生效。暂停或解除配对后需重新启用，配对凭据过期或丢失后需重新配对。自动续接只保证尝试发送消息，后续工具调用需要核验；命令仍由你批准。

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
