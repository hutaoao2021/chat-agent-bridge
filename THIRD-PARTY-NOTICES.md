# Third party notices

The Windows distribution contains CPython 3.14.3 (Python Software Foundation license), its bundled Tcl/Tk and libraries; OpenAI tunnel-client v0.0.15 (Apache-2.0) and its bundled cloudflared and dependencies; and the Python packages installed from `requirements.lock`.

Exact upstream license text is included in the installation:

- `licenses/PYTHON-LICENSE.txt`: PSF license and notices for libraries bundled with CPython.
- `licenses/TUNNEL-LICENSE.txt` and `licenses/TUNNEL-NOTICE.txt`: official Tunnel license and NOTICE.
- `licenses/tunnel-dependencies.txt`: complete upstream dependency notices supplied with the official Windows release. The Tunnel directory also retains its SPDX inventory and cloudflared notices.
- `licenses/python-dependencies.json`: exact installed package versions and relative locations of their license files. Each wheel's original `.dist-info` license files are retained under `runtime/Lib/site-packages`.

Sources: [CPython 3.14.3](https://www.python.org/ftp/python/3.14.3/), [Tunnel v0.0.15](https://github.com/openai/tunnel-client/releases/tag/v0.0.15), [PyPI](https://pypi.org/).

Inno Setup 6.7.3 compiles the installer. Its license and required embedded installer notices are retained by the compiler. It is a build tool, not a dependency users need to install. The portable compiler can be obtained from the [redistributor's package](https://www.nuget.org/packages/Tools.InnoSetup/6.7.3); verify the official Pyrsys code signature before use.

No open source license has been selected for this project's own source. Public availability does not grant a separate license to reuse it. Third party components retain their respective licenses.
