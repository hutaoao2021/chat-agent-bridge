# 新电脑安装

目标平台为 Windows 10/11 x64；已验证环境和未验收场景见下方验收状态。下载 Release 中的 `ChatAgentBridge-Setup-0.2.2-x64.exe`，双击安装。软件自带完整 Python 和官方 Tunnel 客户端，运行 Bridge 本身无需另装 Python、Node 或手动运行两个终端；项目工具链需按项目要求准备。

## 首次配置

1. 打开桌面“Chat Agent Bridge”，在“工作区”添加本机项目目录并选择权限。
2. 在“连接”填写你自己账号的 Tunnel ID 和 restricted API key。需要代理时填写本机 HTTP 代理地址；直连可留空。保存设置，然后检查连接。需要自动启动时勾选“登录当前 Windows 用户后自动运行”，再保存设置。
3. 点击首页“启动”。诊断检查本地 Bridge、代理端口、Tunnel 管理端口等本机证据；不核验云端连接。监听端口可用不等于云端接入成功。
4. 在软件“扩展”页点击“打开扩展目录”。Chrome 打开 `chrome://extensions`（Edge 用 `edge://extensions`），开启开发者模式，点“加载已解压的扩展程序”，选择这个目录。
5. 在软件生成一次性配对码，在浏览器扩展粘贴并点击“配对”。这一步连接浏览器与本机。码有效 5 分钟，限一次使用；浏览器配对凭据有效 30 天，失效或丢失后需重新配对。配对后码框为空正常。
6. 浏览器配对只连接扩展与本机 Bridge，不会自动让 ChatGPT 获得工具。无需另下载 App；在 ChatGPT 的 Plugins 中点击加号创建开发者模式 App，连接方式选 Tunnel，选择当前 Tunnel（或填入已有 Tunnel ID），扫描工具后创建。已有可用 App 可直接选用。创建 Tunnel 需要 Tunnels Read + Manage；运行客户端和选择 Tunnel 需要 Tunnels Read + Use。ChatGPT 开发者模式是独立的账号/workspace 权限，与浏览器扩展开发者模式不同。
7. 刷新 ChatGPT 页面，在已有工作对话启用扩展工作模式；首次发送时保留扩展加入的说明。需要 Bridge 工具的消息还须在 ChatGPT 中选用这个 App，或在消息里提及它；App 选择只对当前消息生效。扩展启用状态会记住，暂停或解除配对后需重新启用。自动续接只发送消息，当前扩展不会替续接消息选 App；后续工具调用需现场核验。

Tunnel 的创建、organization / workspace 关联和 API key 权限，以 [OpenAI 官方 Secure MCP Tunnels 文档](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) 为准。只把本机 MCP 端口（默认 8899）接入 Tunnel，扩展控制端口（默认 8900）留在本机。

软件不会自动登录你的账号，也不会增加 ChatGPT 额度。首次真实账号接入需要上述账号操作；软件设置可以先离线保存。

## 升级

运行新安装程序安装到相同位置。安装程序检查具有进程身份文件的本机命令记录并请求服务退出；这些记录对应的进程仍运行或状态无法确认时停止更新。检查不覆盖远端命令或缺少身份文件的本机记录，升级前需另行确认这些命令已结束。关闭管理窗口不会取消后台命令。浏览器扩展目录更新后，在扩展页面点重新加载，再刷新 ChatGPT。

## 验收状态

0.2.2 为预览版：发布页列出实际安装与运行验证。没有通过干净 Windows 或真实账号验收的部分，不视为已完成验收。项目安装程序尚未代码签名；下载后可按同页 SHA256SUMS 核对文件。
