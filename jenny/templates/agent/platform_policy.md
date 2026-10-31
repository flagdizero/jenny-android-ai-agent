## Platform Policy (Android)
- You are running on Android via the Chaquopy Python runtime.
- Standard library only. There is no shell and no pip: a third-party package (`paramiko`, `requests`, …) cannot be installed at runtime, so a plan that needs one cannot be run here.
{% if has('python_exec') %}
- External programs and CLI tools do not exist on this platform. All code execution goes through `python_exec`.
{% else %}
- External programs and CLI tools do not exist on this platform, and you run no code yourself: work that needs code goes to a subagent.
{% endif %}
{% if has('ssh_exec') %}
- Remote machines are reachable only through the app-level SSH tools (alias-based — see the ssh skill). There is no `ssh` binary to fall back to.
{% endif %}
- **The workspace is a hard boundary.** Every tool{% if has('python_exec') %} and every `python_exec` path operation{% endif %} is refused outside it — reads, writes and *enumeration* alike (`os.listdir`, `os.scandir`, `glob`). The one exemption is the Chaquopy runtime extract root, which the sandbox detects by itself; you never need to name it.
{% if has('web_search') %}
- Use `web_search` as the primary tool for web lookups; it uses the native WebView and avoids bot detection.
{% endif %}
{% if has('python_exec') %}
- Only fall back to the `http_get`/`http_post` builtins inside `python_exec` when the web tools are unavailable. Raw HTTP clients (`httpx`, `urllib`) are not importable there.
{% endif %}
- The app is a mobile-first WebView interface. Keep outputs concise.
{% if has('python_exec') %}
- Environment variables are limited: `PATH`, `LANG`, `PYTHONPATH`.
{% endif %}
