"""Read-only filesystem and archive traversal."""
import csv
import io
import json
from pathlib import Path
import shutil
import re
import stat
import struct
import tempfile
import zipfile

from lxml import etree

FORMATS = {"json", "csv", "tsv", "log", "txt", "xml", "cmtx", "inf", "pol", "evtx"}
MAX_BYTES = 1024**3
MAX_MEMBER_COUNT = 100_000


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x400
    )


def visit(folder, depth, errors):
    try:
        entries = sorted(folder.iterdir(), key=lambda p: (p.name.casefold(), p.name))
    except OSError as exc:
        errors.append({"code": type(exc).__name__, "source": str(folder)})
        return
    for entry in entries:
        try:
            if is_link(entry):
                continue
            info = entry.stat()
            if stat.S_ISREG(info.st_mode):
                yield entry, info.st_size
            elif depth != 0 and stat.S_ISDIR(info.st_mode):
                yield from visit(entry, depth - 1, errors)
        except OSError as exc:
            errors.append({"code": type(exc).__name__, "source": str(entry)})


def sources(path, max_depth, result, policy_scan=False):
    path = Path(path)
    if not path.is_absolute() or (not policy_scan and max_depth not in range(4)):
        raise ValueError("invalid_path_or_depth")
    if path.is_dir():
        for file, size in visit(path, max_depth, result["errors"]):
            yield str(file), file.suffix[1:].lower(), size, file.open
        return
    if max_depth:
        raise ValueError("depth_requires_folder")
    if path.suffix.lower() != ".zip":
        yield str(path), path.suffix[1:].lower(), path.stat().st_size, path.open
        return
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("archive_size_limit")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_MEMBER_COUNT:
            raise ValueError("archive_member_limit")
        for member in sorted(entries, key=lambda entry: (entry.filename.casefold(), entry.filename)):
            if member.is_dir() or stat.S_ISLNK(member.external_attr >> 16):
                continue
            extension = Path(member.filename).suffix[1:].lower()
            yield (
                f"{path}!/{member.filename}", extension, member.file_size,
                lambda mode, member=member: archive.open(member, "r"),
            )


def registry_records(data):
    if data[:8] != b"PReg\x01\x00\x00\x00":
        raise ValueError("invalid_pol_header")
    position = 8

    def token(delimiter):
        nonlocal position
        for end in range(position, len(data) - 1, 2):
            if data[end:end + 2] == delimiter:
                value = data[position:end]
                position = end + 2
                return value
        raise ValueError("invalid_pol_delimiter")

    def consume(expected):
        nonlocal position
        if data[position:position + len(expected)] != expected:
            raise ValueError("invalid_pol_delimiter")
        position += len(expected)

    while position < len(data):
        offset = position
        consume(b"[\0")
        key = token(b";\0").decode("utf-16-le").rstrip("\0")
        name = token(b";\0").decode("utf-16-le").rstrip("\0")
        kind = struct.unpack_from("<I", data, position)[0]
        position += 4
        consume(b";\0")
        length = struct.unpack_from("<I", data, position)[0]
        position += 4
        consume(b";\0")
        raw = data[position:position + length]
        position += length
        consume(b"]\0")
        value = raw.hex()
        if kind in {1, 2, 7}:
            value = raw.decode("utf-16-le").rstrip("\0")
            if kind == 7:
                value = value.split("\0")
        elif (kind, length) in {(4, 4), (5, 4), (11, 8)}:
            value = int.from_bytes(raw, "big" if kind == 5 else "little")
        names = {1: "REG_SZ", 2: "REG_EXPAND_SZ", 3: "REG_BINARY", 4: "REG_DWORD",
                 5: "REG_DWORD_BIG_ENDIAN", 7: "REG_MULTI_SZ", 11: "REG_QWORD"}
        yield {"key": key, "value_name": name, "reg_type": names.get(kind, "UNKNOWN"),
               "reg_type_code": kind, "data": value, "data_raw": raw.hex(),
               "is_deleted": name.startswith("**Del"), "byte_offset": offset}


def xml_records(stream):
    stack = []
    for action, node in etree.iterparse(stream, events=("start", "end"),
                                        load_dtd=False, resolve_entities=False,
                                        no_network=True, huge_tree=False):
        if action == "start":
            if node.getroottree().docinfo.doctype:
                raise ValueError("xml_doctype_forbidden")
            stack.append(str(node.tag))
            continue
        fields = dict(node.attrib)
        if node.text and node.text.strip():
            fields["text"] = node.text.strip()
        compact = {str(key)[:256]: str(value)[:1024]
                   for key, value in list(fields.items())[:32]}
        yield {"xml_path": "/" + "/".join(stack), "element": str(node.tag),
               "fields": compact, "evidence_truncated": compact != fields}
        stack.pop()
        node.clear()
        while node.getprevious() is not None:
            del node.getparent()[0]


def event_records(stream):
    from Evtx.Evtx import Evtx

    # EVTX needs a seekable, memory-mappable file.
    with tempfile.TemporaryDirectory(prefix="recordlens-") as temp:
        path = Path(temp) / "input.evtx"
        with path.open("wb") as output:
            shutil.copyfileobj(stream, output, length=1024 * 1024)
        with Evtx(str(path)) as events:
            for event in events.records():
                root = etree.fromstring(event.xml().encode(),
                                        parser=etree.XMLParser(resolve_entities=False, no_network=True))
                system = root.find("{*}System")
                record = {etree.QName(node).localname: node.text or dict(node.attrib)
                          for node in system} if system is not None else {}
                details = root.findall(".//{*}EventData/{*}Data")
                record.update({node.get("Name", str(index)): node.text
                               for index, node in enumerate(details)})
                yield record


def records(extension, stream):
    if extension in {"xml", "cmtx"}:
        yield from xml_records(stream)
        return
    if extension == "pol":
        yield from registry_records(stream.read(64 * 1024**2 + 1))
        return
    if extension == "evtx":
        yield from event_records(stream)
        return

    encoding = "utf-8-sig"
    buffered = io.BufferedReader(stream)
    if buffered.peek(2).startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    with io.TextIOWrapper(buffered, encoding=encoding, errors="replace", newline="") as text:
        if extension in {"csv", "tsv"}:
            yield from csv.DictReader(text, delimiter="\t" if extension == "tsv" else ",",
                                      restkey="_extra_columns")
        elif extension == "json":
            value = json.load(text)
            for row in value if isinstance(value, list) else [value]:
                yield row if isinstance(row, dict) else {"value": row}
        else:
            section = ""
            for line_number, line in enumerate(text, 1):
                line = line.rstrip("\r\n")
                if extension == "inf":
                    stripped = line.strip()
                    if stripped.startswith("[") and stripped.endswith("]"):
                        section = stripped[1:-1]
                        continue
                    if not stripped or stripped.startswith(";"):
                        continue
                    key, _, value = line.partition("=")
                    yield {"section": section, "key": key.strip(), "value": value.strip(),
                           "raw": line, "entry_type": "assignment" if _ else "raw",
                           "line": line_number}
                else:
                    record = {"line": line_number, "text": line}
                    if extension == "log":
                        level = re.search(r"\b(?:TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\b", line, re.I)
                        moment = re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?", line)
                        if level:
                            record["level"] = level[0].upper()
                        if moment:
                            record["timestamp"] = moment[0]
                    yield record
