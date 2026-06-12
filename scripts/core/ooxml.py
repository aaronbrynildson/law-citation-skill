"""
ooxml.py — Defensive helpers for reading .docx ZIP/XML parts.
"""
from __future__ import annotations

import zipfile
from pathlib import PurePosixPath
import re
from xml.etree import ElementTree as ET

MAX_ZIP_MEMBERS = 5000
MAX_MEMBER_SIZE = 50 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 250 * 1024 * 1024


class CitationCheckerError(Exception):
    """Base class for user-facing citation checker failures."""


class DocxFormatError(CitationCheckerError):
    """The input is not a readable Word .docx package."""


class DocxSecurityError(CitationCheckerError):
    """The input package violates conservative safety limits."""


ET.register_namespace("w", "http://schemas.openxmlformats.org/wordprocessingml/2006/main")
ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
ET.register_namespace("rel", "http://schemas.openxmlformats.org/package/2006/relationships")


def xml_parser() -> ET.XMLParser:
    return ET.XMLParser()


def validate_docx_zip(z: zipfile.ZipFile) -> None:
    infos = z.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        raise DocxSecurityError(f".docx has too many ZIP members ({len(infos)} > {MAX_ZIP_MEMBERS})")

    seen: set[str] = set()
    total = 0
    for info in infos:
        name = info.filename
        if name in seen:
            raise DocxSecurityError(f".docx contains duplicate ZIP member: {name}")
        seen.add(name)
        _validate_member_name(name)
        if info.file_size > MAX_MEMBER_SIZE:
            raise DocxSecurityError(f".docx member too large: {name}")
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise DocxSecurityError(".docx uncompressed content exceeds safety limit")


def read_zip_member(z: zipfile.ZipFile, member: str) -> bytes:
    try:
        info = z.getinfo(member)
    except KeyError as exc:
        raise DocxFormatError(f"missing required .docx member: {member}") from exc
    if info.file_size > MAX_MEMBER_SIZE:
        raise DocxSecurityError(f".docx member too large: {member}")
    try:
        return z.read(member)
    except zipfile.BadZipFile as exc:
        raise DocxFormatError(f"could not read .docx member: {member}") from exc


def parse_xml_bytes(xml_bytes: bytes, member: str):
    _reject_dtd_or_entity(xml_bytes, member)
    try:
        return ET.fromstring(xml_bytes, parser=xml_parser())
    except ET.ParseError as exc:
        raise DocxFormatError(f"malformed XML in {member}: {exc}") from exc


def parse_xml_member(z: zipfile.ZipFile, member: str):
    return parse_xml_bytes(read_zip_member(z, member), member)


def _validate_member_name(name: str) -> None:
    if not name or name.startswith("/") or "\\" in name:
        raise DocxSecurityError(f"unsafe ZIP member name: {name}")
    parts = PurePosixPath(name).parts
    if any(part == ".." for part in parts):
        raise DocxSecurityError(f"unsafe ZIP member name: {name}")


def _reject_dtd_or_entity(xml_bytes: bytes, member: str) -> None:
    if re.search(br"<!\s*(?:doctype|entity)\b", xml_bytes, flags=re.IGNORECASE):
        raise DocxSecurityError(f"unsafe XML DTD/entity declaration in {member}")
