# RecordLens

RecordLens is a local stdio MCP server for inspecting structured files, Windows Event Logs, and Group Policy backups. It runs on Python 3.11+.

## Input scope

| Input | Scope |
| --- | --- |
| Absolute local or SMB folder | Direct files by default. Set `max_depth` to 1, 2, or 3 to include subfolders. |
| Absolute file path | One file. |
| Explicit ZIP path | Supported members at every directory level. Embedded ZIPs are ignored. |

Supported formats include JSON, CSV, TSV, text, logs, XML, CMTX, INF, Registry.pol, and EVTX. Start with `summarize_folder` for large data sets. Check `complete`, `errors`, and `limit_reasons` before drawing conclusions from results.

## Install for Codex in VS Code

With Python, PowerShell, and the Codex CLI available, run from the checkout:

```powershell
.\Deploy-RecordLens.ps1
```

The installer registers RecordLens in the current user's Codex configuration, so it is available throughout the VS Code workspace and other local projects using the same Codex host. Restart the extension and call `get_server_health`.

To install without modifying client configuration, run `python -m pip install .`. You can register the installed `recordlens` executable as a stdio server in the Codex extension.

WinEvent queries require Windows PowerShell and access to the selected log. Reads run with your Windows identity. The export tool is disabled until you configure `RECORDLENS_EXPORT_DIR` as an existing local directory in the server environment.

## Use with GitHub Copilot in VS Code

Run `.\Deploy-RecordLens.ps1 -InstallOnly` if RecordLens is not already installed. An existing Codex installation of RecordLens can be reused.

In a Copilot chat session, run **Chat: Open Customizations**, select **MCP Servers**, and add a **Command (stdio)** server named `recordlens` under **Global → Copilot Global**. Use the absolute path to `recordlens.exe`. The default installer puts it at `%LOCALAPPDATA%\RecordLens\1.1.0\Scripts\recordlens.exe`. Expand the environment variable before entering the path.

You can also merge the following entry into the `mcpServers` object in `$COPILOT_HOME/mcp-config.json` (default `~/.copilot/mcp-config.json`). Replace the example path with the actual executable path.

```json
{
  "mcpServers": {
    "recordlens": {
      "type": "stdio",
      "command": "C:/Users/your-user/AppData/Local/RecordLens/1.1.0/Scripts/recordlens.exe",
      "args": [],
      "tools": ["*"]
    }
  }
}
```

In Copilot Chat, ask RecordLens to run `get_server_health` and confirm the reported version and tool count. See the [VS Code MCP configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration).


## Development

Run `python -m pip install .`, then `python -m unittest discover -s tests`.
