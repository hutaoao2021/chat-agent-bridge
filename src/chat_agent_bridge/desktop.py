"""Single Chinese desktop entry point. Closing the window leaves services running."""
import argparse
from dataclasses import replace
import json
import queue
from pathlib import Path
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import webbrowser
from . import __version__
from .config import WorkspacePolicy, SSHTarget
from .desktop_settings import Layout, DesktopSettings, load_settings
from .desktop_controller import DesktopController, collect_diagnostics, export_diagnostics, issue_pairing, import_legacy
from .supervisor import request_stop, set_autostart

def workspace_from_input(name, directory, flags, ssh_targets=()):
    if not directory.strip():
        raise ValueError('请选择明确的工作目录')
    root = Path(directory.strip()).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('工作目录不存在')
    return WorkspacePolicy(name.strip(), root, *flags, ssh_targets=ssh_targets)


class Manager:
    def __init__(self, root, layout):
        self.root, self.layout = root, layout
        self.controller = DesktopController(layout)
        self.results = queue.Queue()
        self.busy = False
        self.buttons = []
        self.checks = []
        try:
            self.settings = load_settings(layout)
            self._load_error = False
            initial_error = '部分工作目录不可用；请在工作区修改目录后保存，已有连接设置已保留。' if any(not w.root.is_dir() for w in self.settings.workspaces) else ''
        except Exception:
            self.settings = DesktopSettings()
            self._load_error = True
            initial_error = '设置无法读取或校验失败，已禁止覆盖保存。请先检查原因；必要时在连接页备份并重置设置。'
        self.workspaces = list(self.settings.workspaces)
        self.ssh_targets = list(self.settings.ssh_targets)
        root.title('Chat Agent Bridge · 本地工作桥')
        root.geometry('960x730')
        root.minsize(850, 730)
        root.configure(bg='#f3f6fa')
        style = ttk.Style(root)
        if 'clam' in style.theme_names(): style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#f3f6fa', foreground='#172b4d')
        style.configure('TButton', padding=(12, 8))
        style.configure('TNotebook.Tab', padding=(18, 10))
        style.configure('TEntry', fieldbackground='white', padding=5)
        style.configure('Treeview', rowheight=30, fieldbackground='white', background='white')
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'), padding=5)
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 23, 'bold'))
        head = ttk.Frame(root, padding=(24, 18, 24, 12)); head.pack(fill='x')
        ttk.Label(head, text='Chat Agent Bridge', style='Title.TLabel').pack(anchor='w')
        ttk.Label(head, text=f'ChatGPT 本地工作桥  ·  v{__version__}').pack(anchor='w', pady=(6, 0))
        notebook = ttk.Notebook(root); notebook.pack(fill='both', expand=True, padx=24, pady=8)
        self.pages = {}
        for name in ('首页', '工作区', '连接', '扩展', '诊断', '帮助'):
            page = ttk.Frame(notebook, padding=18); notebook.add(page, text=name); self.pages[name] = page
        self.message = tk.StringVar(value=initial_error or '首次使用：选择工作区 → 保存连接设置 → 启动 → 安装扩展并配对。')
        ttk.Label(root, textvariable=self.message, wraplength=780).pack(fill='x', padx=24, pady=(5, 16), before=notebook)
        self.home(); self.workspace_page(); self.connection_page(); self.extension_page(); self.diagnostics_page(); self.help_page()
        self.root.after(100, self.refresh)
        self.root.after(100, self.consume)

    def button(self, parent, text, action):
        button = ttk.Button(parent, text=text, command=action)
        button.pack(side='left', padx=(0, 8), pady=8)
        self.buttons.append(button)
        return button

    def action(self, operation, fn):
        if self.busy: return
        self.busy = True
        self.message.set(operation + '…')
        for button in self.buttons: button.configure(state='disabled')
        def worker():
            try: self.results.put((operation, fn(), None))
            except Exception as error: self.results.put((operation, None, error))
        threading.Thread(target=worker, daemon=True).start()

    def consume(self):
        try:
            while True:
                operation, result, error = self.results.get_nowait()
                self.busy = False
                for button in self.buttons: button.configure(state='normal')
                if error:
                    text = str(error) if isinstance(error, ValueError) else f'{type(error).__name__}：操作失败，请查看诊断。'
                    self.message.set(text)
                    if operation != '检查状态': messagebox.showerror('Chat Agent Bridge', text, parent=self.root)
                else:
                    if operation == '检查状态':
                        self.checks = result; self.render_checks()
                        self.message.set('检查结果已更新。已检测到的事实见诊断说明；未核验项目不代表已连接或可用。')
                    elif operation in ('启动', '重启'):
                        self.message.set(operation + '请求已处理；请稍后点击“检查状态”核对服务。此提示不代表 Tunnel 或 ChatGPT 已连接。')
                    elif operation == '停止':
                        self.message.set('本软件的后台服务已停止；已启动的持久命令可能仍在运行。“停止”不会取消这些命令。')
                    elif operation == '生成配对码':
                        self.code.set(result[0]); self.pair_expiry.set('配对码有效至 ' + time.strftime('%H:%M:%S', time.localtime(result[1])) + '，限使用一次；凭据失效时需重新配对。')
                    elif operation == '重置设置':
                        self.settings = load_settings(self.layout); self._load_error = False
                        self.workspaces = list(self.settings.workspaces); self.ssh_targets = list(self.settings.ssh_targets)
                        self.render_workspaces(); self.load_connection(); self.secret.set('')
                        self.message.set(f'设置已重置，原设置备份在 {result}。密钥和任务记录保留。请重新选择工作区并保存连接设置。')
                    elif operation == '保存设置':
                        self.secret.set(''); self.message.set('设置已保存。连接参数变更后点首页“重启”。密钥框为空表示保留已有密钥。')
                    elif operation == '导入旧版':
                        self.settings = load_settings(self.layout); self.workspaces = list(self.settings.workspaces); self.ssh_targets = list(self.settings.ssh_targets)
                        self.load_connection(); self.render_workspaces(); self.message.set('已导入，原目录未修改。请检查代理设置；切换前手动关闭旧版服务。')
                    else: self.message.set(operation + '完成。')
        except queue.Empty:
            pass
        self.root.after(100, self.consume)

    def refresh(self):
        self.action('检查状态', lambda: collect_diagnostics(self.layout))

    def home(self):
        page = self.pages['首页']
        ttk.Label(page, text='运行状态', font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor='w')
        self.overview = tk.StringVar(value='正在检查…')
        ttk.Label(page, textvariable=self.overview, justify='left', wraplength=780).pack(anchor='w', pady=10)
        bar = ttk.Frame(page); bar.pack(anchor='w')
        self.button(bar, '启动', lambda: self.action('启动', self.controller.start))
        self.button(bar, '停止', lambda: self.action('停止', self.controller.stop))
        self.button(bar, '重启', lambda: self.action('重启', self.controller.restart))
        self.button(bar, '检查状态', self.refresh)
        bar = ttk.Frame(page); bar.pack(anchor='w')
        self.button(bar, '打开 ChatGPT', lambda: webbrowser.open('https://chatgpt.com'))
        self.button(bar, '导入旧版设置', self.import_old)
        ttk.Separator(page).pack(fill='x', pady=12)
        ttk.Label(page, text='日常使用', font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w')
        ttk.Label(page, text='在连接页勾选并保存登录后自动运行；网络和账号配置有效时可后台连接。\n浏览器配对后还需在 ChatGPT 创建并选用 Tunnel App；需要工具的消息须选用该 App。\n已启用工作模式的对话可发送需求；暂停或解除配对后需重新启用。\n扩展可发送续接消息，但不会为续接消息选 App；后续工具调用须另行验证。\n命令需在扩展里审批；续接还受页面可用性与账号额度限制。\n\n关闭窗口不停止服务。“停止”停止本软件的服务，已启动的持久命令可能继续运行。\n要取消任务及其命令，请在服务运行时使用扩展“停止任务”，并核对取消结果。', justify='left', wraplength=780).pack(anchor='w', pady=8)

    def workspace_page(self):
        page = self.pages['工作区']
        ttk.Label(page, text='选择允许 ChatGPT 使用的项目目录，首次默认只读。').pack(anchor='w', pady=(0, 12))
        self.tree = ttk.Treeview(page, columns=('name', 'root', 'write', 'command'), show='headings', height=8)
        for key, label, width in [('name', '名称', 100), ('root', '目录', 450), ('write', '写文件', 80), ('command', '命令', 80)]:
            self.tree.heading(key, text=label); self.tree.column(key, width=width, minwidth=60)
        self.tree.pack(fill='both', expand=True)
        bar = ttk.Frame(page); bar.pack(anchor='w')
        self.button(bar, '添加工作区', lambda: self.edit_workspace())
        self.button(bar, '编辑选中', lambda: self.edit_workspace(True))
        self.button(bar, '移除选中', self.remove_workspace)
        self.button(bar, '高级 SSH', self.edit_ssh)
        self.button(bar, '保存设置', self.save)
        ttk.Label(page, text='开启命令权限后，每条命令仍需审批。本机命令使用当前 Windows 用户身份；SSH 命令使用目标 SSH 账号身份。', wraplength=780).pack(anchor='w', pady=8)
        self.render_workspaces()

    def render_workspaces(self):
        self.tree.delete(*self.tree.get_children())
        for i, w in enumerate(self.workspaces):
            self.tree.insert('', 'end', iid=str(i), values=(w.name, str(w.root) + ('（目录不可用）' if not w.root.is_dir() else ''), '允许' if w.allow_write else '只读', '允许' if w.allow_command else '禁用'))

    def edit_workspace(self, editing=False):
        selection = self.tree.selection()
        if editing and not selection: return
        index = int(selection[0]) if editing else None
        old = self.workspaces[index] if editing else None
        window = tk.Toplevel(self.root); window.title('工作区设置'); window.geometry('640x370'); window.transient(self.root)
        frame = ttk.Frame(window, padding=18); frame.pack(fill='both', expand=True)
        name = tk.StringVar(value=old.name if old else '')
        path = tk.StringVar(value=str(old.root) if old else '')
        for label, value in [('名称', name), ('工作目录', path)]:
            ttk.Label(frame, text=label).pack(anchor='w'); ttk.Entry(frame, textvariable=value).pack(fill='x', pady=6)
        ttk.Button(frame, text='选择目录', command=lambda: path.set(filedialog.askdirectory(parent=window) or path.get())).pack(anchor='w')
        flags = [tk.BooleanVar(value=getattr(old, field) if old else False) for field in ('allow_write', 'allow_command', 'allow_git_write')]
        for text, flag in zip(('允许写文件', '允许请求执行命令（需审批）', '允许 Git 写操作'), flags): ttk.Checkbutton(frame, text=text, variable=flag).pack(anchor='w', pady=4)
        def accept():
            try:
                w = workspace_from_input(name.get(), path.get(), tuple(f.get() for f in flags), old.ssh_targets if old else ())
                candidate = list(self.workspaces)
                if index is None: candidate.append(w)
                else: candidate[index] = w
                replace(self.settings, workspaces=tuple(candidate), ssh_targets=tuple(self.ssh_targets)).validate(require_existing=False)
                self.workspaces = candidate; self.render_workspaces(); window.destroy()
            except Exception as error: messagebox.showerror('工作区设置', str(error), parent=window)
        ttk.Button(frame, text='确定', command=accept).pack(anchor='e', pady=8)

    def remove_workspace(self):
        selection = self.tree.selection()
        if selection:
            del self.workspaces[int(selection[0])]; self.render_workspaces()

    def edit_ssh(self):
        window = tk.Toplevel(self.root); window.title('高级 SSH 设置'); window.geometry('780x470'); window.transient(self.root)
        frame = ttk.Frame(window, padding=16); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='使用已配置的 OpenSSH 别名。在 workspaces 中给工作区填写 targets 的目标名称。校验通过后还需在主窗口保存设置；此处不测试远端连接。', wraplength=730).pack(anchor='w')
        editor = tk.Text(frame, height=15, font=('Consolas', 10), undo=True); editor.pack(fill='both', expand=True, pady=10)
        from dataclasses import asdict
        editor.insert('1.0', json.dumps({'targets': [asdict(t) for t in self.ssh_targets], 'workspaces': {w.name: list(w.ssh_targets) for w in self.workspaces}}, ensure_ascii=False, indent=2))
        def accept():
            try:
                raw = json.loads(editor.get('1.0', 'end'))
                targets = tuple(SSHTarget(**t) for t in raw['targets'])
                workspaces = tuple(replace(w, ssh_targets=tuple(raw['workspaces'].get(w.name, []))) for w in self.workspaces)
                replace(self.settings, workspaces=workspaces, ssh_targets=targets).validate()
                self.ssh_targets = list(targets); self.workspaces = list(workspaces); window.destroy()
            except Exception as error: messagebox.showerror('SSH 设置', str(error), parent=window)
        ttk.Button(frame, text='校验并采用', command=accept).pack(anchor='e')

    def connection_page(self):
        page = self.pages['连接']
        self.fields = {name: tk.StringVar() for name in ('tunnel_id', 'proxy_url', 'mcp_port', 'control_port', 'tunnel_port')}
        self.secret = tk.StringVar(); self.autostart = tk.BooleanVar()
        labels = [('tunnel_id', 'Tunnel ID（平台创建后填写）'), ('proxy_url', '本地 HTTP 代理地址（留空直连）'), ('mcp_port', '本机 MCP 服务端口'), ('control_port', '本机扩展控制端口'), ('tunnel_port', 'Tunnel 本机管理端口')]
        for key, label in labels:
            row = ttk.Frame(page); row.pack(fill='x', pady=6)
            ttk.Label(row, text=label, width=38).pack(side='left'); ttk.Entry(row, textvariable=self.fields[key]).pack(side='left', fill='x', expand=True)
        row = ttk.Frame(page); row.pack(fill='x', pady=6)
        ttk.Label(row, text='Tunnel API key（空白表示保留）', width=38).pack(side='left'); ttk.Entry(row, textvariable=self.secret, show='●').pack(side='left', fill='x', expand=True)
        ttk.Checkbutton(page, text='登录当前 Windows 用户后自动运行', variable=self.autostart).pack(anchor='w', pady=14)
        bar = ttk.Frame(page); bar.pack(anchor='w')
        self.button(bar, '保存设置', self.save); self.button(bar, '检查连接状态', self.refresh)
        self.button(bar, '打开 Tunnel 设置', lambda: webbrowser.open('https://platform.openai.com/settings/organization/tunnels'))
        self.button(bar, '备份并重置设置', self.reset_settings)
        ttk.Label(page, text='密钥由当前 Windows 用户加密保存。配对码用于浏览器连接，不是 API key。\n保存只校验本机设置格式，不核验密钥有效性、云端权限或网络。\n代理须提供 HTTP 转发；Mixed 端口需支持 HTTP，本软件不接收 SOCKS 地址。\n更改设置后在首页重启；ChatGPT 账号接入需另行配置。', justify='left', wraplength=850).pack(anchor='w', pady=18)
        self.load_connection()

    def load_connection(self):
        for name, value in self.fields.items(): value.set(str(getattr(self.settings, name)))
        self.autostart.set(self.settings.autostart)

    def reset_settings(self):
        if messagebox.askyesno('备份并重置设置', '先备份原设置，再重置连接和工作区设置。密钥、任务记录和项目文件保留。是否继续？', parent=self.root):
            self.action('重置设置', self.controller.backup_reset_settings)

    def save(self):
        if self._load_error:
            messagebox.showerror('保存设置', '现有配置损坏，禁止用默认值覆盖。请先备份并重置设置。', parent=self.root)
            return
        try:
            settings = replace(self.settings, **{k: int(v.get()) if k.endswith('_port') else v.get().strip() for k, v in self.fields.items()},
                               autostart=self.autostart.get(), workspaces=tuple(self.workspaces), ssh_targets=tuple(self.ssh_targets))
            settings.validate(); secret = self.secret.get()
            def persist(): self.controller.save(settings, secret); self.settings = settings
            self.action('保存设置', persist)
        except ValueError as error: messagebox.showerror('保存设置', str(error), parent=self.root)

    def extension_page(self):
        page = self.pages['扩展']
        ttk.Label(page, text='浏览器扩展与 ChatGPT App', font=('Microsoft YaHei UI', 14, 'bold')).pack(anchor='w')
        ttk.Label(page, text='1. 在 Chrome / Edge 扩展管理页加载下方目录，生成配对码并在扩展弹窗配对。这只连接浏览器与本机。\n2. 在 ChatGPT 的 Plugins → ＋ 创建开发者模式 App；连接选 Tunnel，选当前 Tunnel，扫描工具并创建。无需另下载 App。\n3. 需要 Bridge 工具的消息须选用该 App（或在消息中提及）；选用只对当前消息生效。已有 App 可直接选用。\n4. 在工作对话启用扩展工作模式；首次发送保留扩展加入的任务说明。\n自动续接只发送消息，不会替后续消息选 App；工具调用能否成功需现场验证。\n浏览器和 ChatGPT 的开发者模式分开设置；看不到 App 或 Tunnel 时请核对账号与 workspace 权限。', justify='left', wraplength=780).pack(anchor='w', pady=10)
        bar = ttk.Frame(page); bar.pack(anchor='w')
        self.button(bar, '打开扩展目录', lambda: self.open_path(self.layout.extension_dir))
        self.button(bar, '复制 Chrome 扩展页地址', lambda: self.copy('chrome://extensions'))
        self.button(bar, '复制 Edge 扩展页地址', lambda: self.copy('edge://extensions'))
        self.button(bar, '查看完整接入步骤', lambda: self.read_help('new-computer.md'))
        self.code = tk.StringVar(); self.pair_expiry = tk.StringVar(value='配对码有效 5 分钟，限一次使用；配对凭据有效 30 天，失效或丢失时需重新配对。')
        ttk.Entry(page, textvariable=self.code, state='readonly').pack(fill='x', pady=16)
        ttk.Label(page, textvariable=self.pair_expiry).pack(anchor='w')
        bar = ttk.Frame(page); bar.pack(anchor='w')
        self.button(bar, '生成配对码', lambda: self.action('生成配对码', lambda: issue_pairing(self.layout)))
        self.button(bar, '复制配对码', lambda: self.copy(self.code.get()))
        ttk.Label(page, text='重新加载扩展之后，刷新 ChatGPT 页面。\n若显示“发送结果未确认”，先检查最后一条消息，按扩展人工恢复说明操作。', justify='left', wraplength=850).pack(anchor='w', pady=16)

    def diagnostics_page(self):
        page = self.pages['诊断']
        self.diagnostic_tree = ttk.Treeview(page, columns=('component', 'status', 'message'), show='headings', height=11)
        for name, title, width in [('component', '组件', 140), ('status', '状态', 80), ('message', '诊断', 590)]:
            self.diagnostic_tree.heading(name, text=title); self.diagnostic_tree.column(name, width=width)
        self.diagnostic_tree.pack(fill='both', expand=True)
        bar = ttk.Frame(page); bar.pack(anchor='w')
        self.button(bar, '刷新诊断', self.refresh)
        self.button(bar, '导出脱敏诊断', self.export)
        self.button(bar, '查看后台日志', self.show_log)
        self.button(bar, '打开 Tunnel 管理页', lambda: webbrowser.open(f'http://127.0.0.1:{self.settings.tunnel_port}/ui'))

    def render_checks(self):
        self.diagnostic_tree.delete(*self.diagnostic_tree.get_children())
        for row in self.checks:
            self.diagnostic_tree.insert('', 'end', values=(row['component'], {'ok': '检查通过', 'unknown': '未核验', 'waiting': '待处理', 'error': '检查异常'}[row['status']], row['message']))
        self.overview.set('\n'.join(f"{r['component']}：{r['message']}" for r in self.checks if r['component'] in ('Bridge', 'Tunnel', '代理', '扩展配对', '后台')))

    def export(self):
        target = filedialog.asksaveasfilename(parent=self.root, defaultextension='.json', initialfile='bridge-diagnostics.json')
        if target: self.action('导出诊断', lambda: export_diagnostics(self.layout, Path(target)))

    def show_log(self):
        path = self.layout.data_dir / 'autostart' / 'startup.log'
        if not path.exists(): self.message.set('暂无后台日志'); return
        with path.open('rb') as file:
            file.seek(max(0, path.stat().st_size - 65536)); text = file.read(65536).decode('utf-8', errors='replace')
        self.show_text('后台生命周期日志', text)

    def show_text(self, title, text):
        window = tk.Toplevel(self.root); window.title(title); window.geometry('820x590')
        editor = tk.Text(window, wrap='word', font=('Microsoft YaHei UI', 10), padx=16, pady=16)
        editor.pack(fill='both', expand=True); editor.insert('1.0', text); editor.configure(state='disabled')

    def help_page(self):
        page = self.pages['帮助']
        ttk.Label(page, text='从安装到日常使用，都从这里开始。', font=('Microsoft YaHei UI', 14, 'bold')).pack(anchor='w', pady=10)
        for label, name in [('使用手册', 'user-guide.md'), ('新电脑安装', 'new-computer.md'), ('故障排查', 'troubleshooting.md')]:
            bar = ttk.Frame(page); bar.pack(anchor='w')
            self.button(bar, label, lambda filename=name: self.read_help(filename))
        ttk.Label(page, text='工作流程（需账号接入和本地服务可用）\n\n发送需求 → 扩展准备任务绑定 → ChatGPT 调用 Bridge\n→ 按工作区权限读取或修改 → 请求命令审批 → 保存进度 → 符合条件时续接。\n完成状态来自 Bridge 任务记录；具体修改和测试结果需核对工具返回。\n\n工作模式仅作用于已启用对话。浏览器配对、任务绑定与账号 Tunnel 接入用途不同。\n本软件为独立实现的预览版；尚未验收的场景见验证记录。', justify='left', wraplength=850).pack(anchor='w', pady=20)

    def read_help(self, filename):
        path = self.layout.app_dir / 'docs' / filename
        self.show_text('Chat Agent Bridge 帮助', path.read_text(encoding='utf-8') if path.exists() else '说明文件尚未安装，请修复安装。')

    def copy(self, text):
        self.root.clipboard_clear(); self.root.clipboard_append(text)
        self.message.set('已复制。配对码不要发送到聊天或 GitHub。' if text == self.code.get() and text else '已复制，请在浏览器使用。')

    def open_path(self, path):
        import os
        if Path(path).exists(): os.startfile(path)
        else: self.message.set('目录不存在，请修复安装。')

    def import_old(self):
        source = filedialog.askdirectory(parent=self.root, title='选择旧版项目目录')
        if source and messagebox.askokcancel('导入旧版', '复制设置、任务记录、已结束的本机命令日志和可解密密钥；项目文件不迁移。旧版本机命令记录未确认结束或目标已有数据时拒绝导入。远端命令需另行确认已结束。导入后需核对登录启动设置。', parent=self.root):
            self.action('导入旧版', lambda: import_legacy(self.layout, Path(source)))


def main():
    parser = argparse.ArgumentParser(description='Chat Agent Bridge 中文管理窗口')
    parser.add_argument('--app-dir', type=Path)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--disable', action='store_true')
    parser.add_argument('--maintenance', action='store_true')
    parser.add_argument('--smoke-test', action='store_true')
    args = parser.parse_args()
    layout = Layout.detect(args.app_dir, args.data_dir)
    if args.stop or args.disable or args.maintenance:
        controller = DesktopController(layout)
        if args.maintenance: controller.stop_for_maintenance()
        else: controller.stop()
        if args.disable: set_autostart(layout, False)
        return
    root = tk.Tk(); Manager(root, layout)
    if args.smoke_test: root.after(2500, root.destroy)
    root.mainloop()


if __name__ == '__main__':
    main()
