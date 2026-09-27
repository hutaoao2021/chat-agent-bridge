# 新电脑安装

支持 Windows 10/11 x64。下载 Release 中的 `ChatAgentBridge-Setup-0.2.0-x64.exe`，双击安装。软件自带完整 Python 和官方 Tunnel 客户端，无需安装 Python、Node 或手动运行两个终端。

## 首次设置一次

1. 打开桌面“Chat Agent Bridge”，在“工作区”添加本机项目目录并选择权限。
2. 在“连接”填写你自己账号的 Tunnel ID 和 restricted API key。需要代理时填写本机 HTTP 代理地址；直连可留空。保存设置，然后检查连接。需要开机运行时勾选登录后自动启动。
3. 点击首页“启动”。诊断分别显示本地 Bridge、代理、Tunnel、云端连接等状态。监听端口可用不等于云端接入成功。
4. 在“浏览器扩展”打开软件的扩展目录。Chrome 打开 `chrome://extensions`（Edge 用 `edge://extensions`），开启开发者模式，点“加载已解压的扩展程序”，选择这个目录。
5. 在软件生成一次性配对码，在浏览器扩展粘贴并点击“配对”。这一步连接浏览器与本机。码五分钟有效，配对完成后输入框留空正常。
6. 通过 OpenAI 的 Tunnel / ChatGPT 接入流程，把 MCP 注册到你所用的 ChatGPT workspace，并在对话中选用对应应用。账号登录、workspace 权限和开发者模式由账号界面决定。
7. 刷新 ChatGPT 页面，在准备工作的对话点一次“启用当前对话”，以后直接发送需求。

Tunnel 的创建、organization / workspace 关联和 API key 权限，以 [OpenAI 官方 Secure MCP Tunnels 文档](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) 为准。只把本机 MCP 端口（默认 8899）接入 Tunnel，扩展控制端口（默认 8900）留在本机。

软件不会自动登录你的账号，也不会增加 ChatGPT 额度。首次真实账号接入需要上述账号操作；软件设置可以先离线保存。

## 升级

运行新安装程序安装到相同位置。安装程序先请求后台退出；无法确认退出时停止更新，避免覆盖正在使用的程序。浏览器扩展目录更新后，在扩展页面点重新加载，再刷新 ChatGPT。

## 验收状态

0.2.0 为预览版：发布页列出实际安装与运行验证。没有通过干净 Windows 或真实账号验收的部分，不视为已完成验收。项目安装程序尚未代码签名；下载后可按同页 SHA256SUMS 核对文件。
