"""RecordLens MCP tools."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from io import StringIO
from pathlib import Path
from typing import Any
import csv
import json
import platform
import shutil
import subprocess
import time
import os
import re

from mcp.server.fastmcp import FastMCP
from .readers import FORMATS, MAX_BYTES, is_link, records, sources, visit

mcp = FastMCP("mcp-recordlens", instructions="Inspect a file, folder or ZIP. Folder depth defaults to zero. Summarize large inputs before searching for specific records.")
STARTED = time.monotonic()


def result(**data):
    return {"success": True, "complete": True, "truncated": False, "errors": [],
            "limit_reasons": [], "offset": 0, "next_offset": None,
            "files_scanned": 0, "files_with_matches": 0, "total_matches": 0,
            "returned_results": 0, **data}


def limit(output, reason):
    output["complete"] = False
    output["truncated"] = True
    if reason not in output["limit_reasons"]:
        output["limit_reasons"].append(reason)


def error(output, exc, source=None):
    output["complete"] = False
    output["errors"].append({"code": str(exc) if type(exc) is ValueError and
                             re.fullmatch(r"[a-z_]+", str(exc)) else type(exc).__name__,
                             **({"source": source} if source else {})})


def field(record, key):
    for fields in (record, record.get("fields", {})):
        for name, value in fields.items():
            if name.casefold() == key.casefold():
                return value
    return None


def timestamp(value):
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def matcher(keyword, name, value, regex, event_ids, level, start_time, end_time):
    pattern = re.compile(regex, re.IGNORECASE) if regex else None
    ids = {str(event_id) for event_id in event_ids or ()}
    start = timestamp(start_time) if start_time else None
    end = timestamp(end_time) if end_time else None

    def matches(record):
        text = json.dumps(record, ensure_ascii=False)
        if keyword and keyword.casefold() not in text.casefold():
            return False
        if pattern and not pattern.search(text):
            return False
        if name and (field(record, name) is None or str(field(record, name)).casefold() != str(value).casefold()):
            return False
        if ids and str(field(record, "EventID")) not in ids:
            return False
        if level and str(field(record, "Level")).casefold() != level.casefold():
            return False
        if start or end:
            raw = field(record, "TimeCreated") or field(record, "timestamp")
            if isinstance(raw, dict):
                raw = raw.get("SystemTime")
            try:
                moment = timestamp(str(raw))
            except ValueError:
                return False
            return (start is None or moment >= start) and (end is None or moment <= end)
        return True
    return matches


def scan(path, max_depth=0, pattern="*", offset=0, max_files=None,
         max_records=None, per_file=None, group_by=None, top_n=20,
         predicate=None, inventory=False, supported_only=True, formats=None,
         annotate=None):
    from fnmatch import fnmatch

    output = result(offset=offset, records=[], total_records=0)
    counts = Counter()
    groups = Counter()
    total_bytes = processed_bytes = file_count = response_bytes = 0
    try:
        if offset < 0 or any(v is not None and v < 1 for v in (max_files, max_records, per_file, top_n)):
            raise ValueError("invalid_limit")
        with closing(sources(path, max_depth, output, policy_scan=annotate is not None)) as entries:
            for source, extension, size, opener in entries:
                if (supported_only and extension not in FORMATS) or (formats and extension not in formats):
                    continue
                if not fnmatch(source.replace("\\", "/").rsplit("/", 1)[-1], pattern):
                    continue
                identity = annotate(source) if annotate else {}
                if annotate and not identity:
                    continue
                index = file_count
                file_count += 1
                counts[extension] += 1
                total_bytes += size
                if index < offset:
                    continue
                if max_files is not None and index >= offset + max_files:
                    limit(output, "file_page")
                    output["next_offset"] = offset + max_files
                    continue
                output["files_scanned"] += 1
                if inventory:
                    output["records"].append({"source_path": source, "file_type": extension, "size_bytes": size})
                    continue
                if size > MAX_BYTES or processed_bytes + size > 4 * MAX_BYTES:
                    limit(output, "input_size_limit")
                    continue
                if extension in {"json", "pol"} and size > 64 * 1024**2:
                    limit(output, "parser_size_limit")
                    continue
                processed_bytes += size
                file_matches = 0
                try:
                    with opener("rb") as stream, closing(records(extension, stream)) as rows:
                        for record in rows:
                            if len(json.dumps(record, ensure_ascii=False)) > 64 * 1024:
                                limit(output, "record_size_limit")
                                continue
                            output["total_records"] += 1
                            if predicate and not predicate(record):
                                continue
                            output["total_matches"] += 1
                            file_matches += 1
                            if group_by:
                                group = json.dumps(field(record, group_by), sort_keys=True)
                                if len(groups) >= 100_000 and group not in groups:
                                    limit(output, "group_limit")
                                else:
                                    groups[group] += 1
                                continue
                            if ((max_records is not None and len(output["records"]) >= max_records)
                                    or (per_file is not None and file_matches > per_file)):
                                limit(output, "record_limit")
                                continue
                            found = {**record, "source_path": source, "file_type": extension}
                            if annotate:
                                found.update(identity)
                            response_bytes += len(json.dumps(found, ensure_ascii=True))
                            if response_bytes <= 4 * 1024**2:
                                output["records"].append(found)
                            else:
                                limit(output, "response_size_limit")
                except Exception as exc:
                    error(output, exc, source)
                output["files_with_matches"] += bool(file_matches)
    except Exception as exc:
        error(output, exc)
    if group_by:
        output["top_groups"] = [{"value": json.loads(key), "count": count}
                                for key, count in sorted(groups.items(), key=lambda entry: (-entry[1], entry[0]))[:top_n]]
    output.update(file_count=file_count, file_types=dict(counts), size_bytes=total_bytes)
    output["returned_results"] = len(output.get("top_groups", output["records"]))
    if output["errors"]:
        output["complete"] = False
        output["success"] = bool(output["files_scanned"] > len(output["errors"]))
    return output


@mcp.tool()
def get_server_health() -> dict[str, Any]:
    """Report server health and available capabilities."""
    dependencies = {name: version(name) for name in ("mcp", "lxml", "python-evtx")}
    powershell = shutil.which("powershell.exe") is not None
    return result(version="1.1.0", status="healthy", tool_count=13,
                  python_version=platform.python_version(), os=platform.system(),
                  uptime_seconds=int(time.monotonic() - STARTED),
                  supported_formats=sorted(FORMATS), dependencies=dependencies,
                  self_check={"dependencies_available": True, "powershell_available": powershell,
                              "eventlog_timeout_valid": True},
                  max_file_size_mb=1024, eventlog_timeout_seconds=30)


@mcp.tool()
def inventory_folder(path: str, max_depth: int = 0, pattern: str = "*", offset: int = 0,
                     max_files: int = 1000, supported_only: bool = True) -> dict[str, Any]:
    """Inventory files in a folder or ZIP with whole-scope totals."""
    return scan(path, max_depth, pattern, offset, max_files, inventory=True, supported_only=supported_only)


@mcp.tool()
def read_folder(path: str, max_depth: int = 0, pattern: str = "*", offset: int = 0,
                max_files: int | None = None, max_records_per_file: int = 100,
                max_total_records: int = 1000) -> dict[str, Any]:
    """Read supported records from a folder, file or ZIP."""
    return scan(path, max_depth, pattern, offset, max_files, max_total_records, max_records_per_file)


@mcp.tool()
def search_folder(path: str, keyword: str | None = None, field: str | None = None,
                  value: str | None = None, regex: str | None = None,
                  event_ids: list[int] | None = None, level: str | None = None,
                  start_time: str | None = None, end_time: str | None = None,
                  max_depth: int = 0, pattern: str = "*", offset: int = 0,
                  max_files: int | None = None, max_results: int = 1000,
                  max_results_per_file: int = 100) -> dict[str, Any]:
    """Search records across a folder, file or ZIP."""
    try:
        predicate = matcher(keyword, field, value, regex, event_ids, level, start_time, end_time)
    except (ValueError, re.error) as exc:
        output = result(success=False)
        error(output, exc)
        return output
    return scan(path, max_depth, pattern, offset, max_files, max_results, max_results_per_file,
                predicate=predicate)


@mcp.tool()
def summarize_folder(path: str, group_by: str, max_depth: int = 0, pattern: str = "*",
                     offset: int = 0, max_files: int | None = None, top_n: int = 20) -> dict[str, Any]:
    """Count records grouped by a field across the selected path."""
    return scan(path, max_depth, pattern, offset, max_files, group_by=group_by, top_n=top_n)


@mcp.tool()
def read_file_structured(path: str, max_records: int = 1000) -> dict[str, Any]:
    """Read records from the specified file."""
    return read_folder(path, max_total_records=max_records, max_records_per_file=max_records)


@mcp.tool()
def search_file(path: str, keyword: str | None = None, field: str | None = None,
                value: str | None = None, regex: str | None = None,
                event_ids: list[int] | None = None, level: str | None = None,
                start_time: str | None = None, end_time: str | None = None,
                max_results: int = 1000) -> dict[str, Any]:
    """Search records from the specified file."""
    return search_folder(path, keyword, field, value, regex, event_ids, level,
                         start_time, end_time, max_results=max_results, max_results_per_file=max_results)


@mcp.tool()
def summarize_file(path: str, group_by: str, top_n: int = 20) -> dict[str, Any]:
    """Summarize records in the specified file."""
    return summarize_folder(path, group_by, top_n=top_n)


def serialize(rows, output_format):
    if output_format == "json":
        return json.dumps(rows, ensure_ascii=False)
    if output_format == "txt":
        return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    if output_format not in {"csv", "tsv"}:
        raise ValueError("unsupported_output_format")
    stream = StringIO()
    columns = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(stream, columns, delimiter="\t" if output_format == "tsv" else ",")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


@mcp.tool()
def convert_file(path: str, output_format: str = "json", max_records: int = 1000) -> dict[str, Any]:
    """Convert records and return the resulting text."""
    output = read_file_structured(path, max_records)
    try:
        output["content"] = serialize(output.pop("records", []), output_format)
    except ValueError as exc:
        error(output, exc)
        output["success"] = False
    return output


@mcp.tool()
def export_results(records: list[dict], filename: str, output_format: str = "json") -> dict[str, Any]:
    """Write a new output file to the configured local export directory."""
    output = result()
    try:
        export_dir = os.environ.get("RECORDLENS_EXPORT_DIR")
        if not export_dir or not Path(export_dir).is_absolute() or export_dir.startswith(("\\\\", "//")):
            raise ValueError("local_export_directory_required")
        if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", filename):
            raise ValueError("invalid_filename")
        directory = Path(export_dir).resolve(strict=True)
        if str(directory).startswith(("\\\\", "//")):
            raise ValueError("local_export_directory_required")
        path = directory / filename
        content = serialize(records, output_format)
        if len(content.encode("utf-8")) > 4 * 1024**2:
            raise ValueError("export_size_limit")
        with path.open("x", encoding="utf-8", newline="") as target:
            target.write(content)
        output.update(export_path=str(path), returned_results=len(records))
    except (ValueError, OSError) as exc:
        error(output, exc)
        output["success"] = False
    return output


@mcp.tool()
def query_live_eventlog(log_name: str, event_ids: list[int] | None = None,
                        keyword: str | None = None, max_events: int = 100,
                        hours_back: int = 24, computer_name: str | None = None,
                        timeout_seconds: int = 30) -> dict[str, Any]:
    """Read recent Windows events using the current Windows identity."""
    output = result(records=[])
    script = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$q = [Console]::In.ReadToEnd() | ConvertFrom-Json
$f = @{ LogName = [string]$q.log_name; StartTime = [datetime]::Parse($q.start) }
if ($q.event_ids) { $f.Id = [int[]]$q.event_ids }
$p = @{ FilterHashtable = $f; MaxEvents = [int]$q.max_events }
if ($q.computer_name) { $p.ComputerName = [string]$q.computer_name }
try {
    Get-WinEvent @p | ForEach-Object {
        @{ EventID=$_.Id; Level=$_.LevelDisplayName; TimeCreated=$_.TimeCreated.ToUniversalTime().ToString('o');
           ProviderName=$_.ProviderName; Message=$_.Message } | ConvertTo-Json -Compress
    }
} catch {
    if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound,*') { exit 1 }
}
"""
    try:
        if not (1 <= max_events <= 5000 and 1 <= hours_back <= 8760 and 1 <= timeout_seconds <= 300):
            raise ValueError("invalid_eventlog_limits")
        query = {"log_name": log_name, "event_ids": event_ids, "max_events": max_events + 1,
                 "computer_name": computer_name,
                 "start": (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()}
        process = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                 input=json.dumps(query, ensure_ascii=True), encoding="utf-8",
                                 capture_output=True, timeout=timeout_seconds)
        if process.returncode:
            raise ValueError("eventlog_query_failed")
        rows = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
        if len(rows) > max_events:
            limit(output, "event_limit")
        output["records"] = [row for row in rows[:max_events]
                             if not keyword or keyword.casefold() in str(row.get("Message")).casefold()]
        output["returned_results"] = output["total_matches"] = len(output["records"])
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        error(output, exc)
        output["success"] = False
    return output


def gpo_inventory(path):
    root = Path(path)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("gpo_folder_required")
    from lxml import etree
    candidates = [root] if any((root / name).exists() for name in ("bkupInfo.xml", "Backup.xml", "DomainSysvol")) else sorted(root.iterdir())
    for child in candidates:
        if is_link(child) or not child.is_dir():
            continue
        info = {"gpo_id": child.name, "gpo_name": None, "domain": None, "gpo_path": str(child)}
        for filename in ("bkupInfo.xml", "Backup.xml"):
            metadata = child / filename
            if not metadata.is_file() or is_link(metadata):
                continue
            parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
            document = etree.parse(str(metadata), parser)
            if document.docinfo.doctype:
                raise ValueError("xml_doctype_forbidden")
            for key, tags in {"gpo_name": ("GPODisplayName", "DisplayName"),
                              "gpo_id": ("GPOGuid", "ID"), "domain": ("GPODomain", "Domain"),
                              "created": ("BackupTime", "Created"), "modified": ("Modified",)}.items():
                for tag in tags:
                    text = document.findtext(f".//{{*}}{tag}")
                    if text:
                        info[key] = text
                        break
            yield info
            break
        else:
            if (child / "DomainSysvol").is_dir():
                yield info


@mcp.tool()
def inventory_gpo_backup(path: str, offset: int = 0, max_gpos: int = 100) -> dict[str, Any]:
    """List backup GPO identities with stable pagination."""
    output = result(offset=offset, gpos=[])
    try:
        if offset < 0 or max_gpos < 1:
            raise ValueError("invalid_limit")
        gpos = list(gpo_inventory(path))
        output.update(gpo_count=len(gpos), gpos=gpos[offset:offset + max_gpos])
        types = Counter()
        for gpo in gpos:
            counts = Counter()
            size_bytes = 0
            for file, size in visit(Path(gpo["gpo_path"]), -1, output["errors"]):
                extension = file.suffix[1:].lower()
                if extension in {"xml", "cmtx", "inf", "pol"}:
                    counts[extension] += 1
                    size_bytes += size
            gpo["size_bytes"] = size_bytes
            gpo["file_types"] = dict(counts)
            types.update(counts)
        output.update(file_types=dict(types), supported_policy_files=sum(types.values()),
                      returned_results=len(output["gpos"]))
        if output["errors"]:
            output["complete"] = False
        if offset + max_gpos < len(gpos):
            limit(output, "gpo_page")
            output["next_offset"] = offset + max_gpos
    except Exception as exc:
        error(output, exc)
        output["success"] = False
    return output


@mcp.tool()
def search_gpo_backup(path: str, keyword: str | None = None, field: str | None = None,
                      value: str | None = None, file_types: list[str] | None = None,
                      offset: int = 0, max_files: int | None = None, max_results: int = 1000) -> dict[str, Any]:
    """Search policy files while attaching backup identity to each match."""
    try:
        info = {Path(gpo["gpo_path"]): gpo for gpo in gpo_inventory(path)}
        predicate = matcher(keyword, field, value, None, None, None, None, None)

        def annotate(source):
            for parent in Path(source).parents:
                if parent in info:
                    return info[parent]
            return {}

        return scan(path, max_depth=-1, offset=offset, max_files=max_files, max_records=max_results,
                    formats=set(file_types or ("xml", "cmtx", "inf", "pol")),
                    predicate=predicate, annotate=annotate)
    except Exception as exc:
        output = result(success=False)
        error(output, exc)
        return output


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
