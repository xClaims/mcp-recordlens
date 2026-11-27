# RecordLens

RecordLens is a local stdio MCP server for inspecting structured files, Windows Event Logs, and Group Policy backups. It runs on Python 3.11+.

## Input scope

| Input | Scope |
| --- | --- |
| Absolute local or SMB folder | Direct files by default. Set `max_depth` to 1, 2, or 3 to include subfolders. |
| Absolute file path | One file. |
| Explicit ZIP path | Supported members at every directory level. Embedded ZIPs are ignored. |

Supported formats include JSON, CSV, TSV, text, logs, XML, CMTX, INF, Registry.pol, and EVTX. Start with `summarize_folder` for large data sets. Check `complete`, `errors`, and `limit_reasons` before drawing conclusions from results.

## Data access

RecordLens runs locally as a stdio server. It reads files and Windows Event Logs with the permissions of the account running it. Results are returned to the MCP client, which can include them in model context. Use files and logs you are authorized to share with that client.

## Install for Codex in VS Code

With Python, PowerShell, and the Codex CLI available, run from the checkout:

```powershell
.\Deploy-RecordLens.ps1 -Client Codex
```

The installer registers RecordLens in the current user's Codex configuration, so it is available throughout the VS Code workspace and other local projects using the same Codex host. Restart the extension and call `get_server_health`.

To install without modifying client configuration, run `python -m pip install .`. You can register the installed `recordlens` executable as a stdio server in the Codex extension.

WinEvent queries require Windows PowerShell and access to the selected log. Reads run with your Windows identity. The export tool is disabled until you configure `RECORDLENS_EXPORT_DIR` as an existing local directory in the server environment.

## Use with GitHub Copilot in VS Code

On a Copilot-only host, run:

```powershell
.\Deploy-RecordLens.ps1 -Client Copilot
```

This installs and tests RecordLens, then registers it for all local workspaces in the current user's Copilot Global configuration. It backs up an existing configuration before updating the RecordLens entry. The default `-Client Auto` selects Codex when its CLI is on PATH and Copilot otherwise. Use `-InstallOnly` to skip client registration.

For manual registration, an existing RecordLens installation can be reused.

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


## Check RecordLens and inventory a folder

In Codex or Copilot Chat, ask:

> Call RecordLens `get_server_health` and report its status and version.

A successful response with `status: healthy` confirms the server is responding in that session.

To list files without reading their records, ask:

> Use RecordLens `inventory_folder` on `C:\path\to\folder` with `max_depth=2` and `max_files=25`. Show the total file count, counts by file type, and whether there is another page.

The MCP tool is `inventory_folder`, exposed as `mcp__recordlens__inventory_folder` in Codex. Example arguments:

```json
{
  "path": "C:\\path\\to\\folder",
  "max_depth": 2,
  "max_files": 25,
  "offset": 0
}
```

`max_depth` defaults to `0` (top-level files only). Set `supported_only` to `false` to include other file types. `max_files` limits the entries returned per page. If `next_offset` is non-null, pass it as `offset` on the next call with the same path and filters. `file_count` and `file_types` describe the full matching scope.

## Development

Run `python -m pip install .`, then `python -m unittest discover -s tests`.
