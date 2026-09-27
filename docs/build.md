# Windows 构建

构建机需要 Windows x64、完整 CPython 3.14.3 和 Inno Setup 6.7.3。用户电脑不需要这些构建工具。

1. 从 [官方目录](https://www.python.org/ftp/python/3.14.3/) 下载 `python-3.14.3-amd64.zip`，按 `windows-3.14.3.json` 核对 SHA256 后解压。嵌入版缺少 Tk，不能用于此构建。
2. 下载 [官方 Tunnel v0.0.15 Windows amd64 zip](https://github.com/openai/tunnel-client/releases/tag/v0.0.15)，用同页 SHA256SUMS 核验并保留 LICENSE、NOTICE 和 dependency notices。
3. 使用 Inno Setup 官方签名有效的编译器。可以安装构建工具，或提取 Tools.InnoSetup 6.7.3 便携 NuGet 包并核对 ISCC.exe 的 Pyrsys 签名。
4. 运行：

```powershell
./scripts/build-windows.ps1 -PythonRoot ./build-input/python -TunnelClient ./build-input/tunnel/tunnel-client.exe -Iscc ./build-input/inno/ISCC.exe
```

构建将锁定的运行依赖下载到 wheelhouse，再从它离线安装非 editable 软件包。开发 pytest 不进入用户包。staging 只选软件、运行环境、扩展、公开文档和第三方声明；不复制用户配置或数据。重复构建请使用新的 OutputDir。

`scripts/verify_bundle.py <bundle> --run-imports` 验证 Python/Tk、包来源、资源、文档及私有文件禁入。安装包、扩展 ZIP、构建清单和 SHA256SUMS 位于输出目录。

发行前需要本机独立目录安装、更新、卸载保留数据、真实 job_runner 与干净 Windows 验证。没有干净环境验收时必须标为预览。自带解释器仅用于 Bridge，项目所需 Git/SSH/其他工具需另备。
