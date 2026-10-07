"""Dedicated STEP output jobs and verification, independent of the MCP runtime."""

import asyncio
import configparser
import hashlib
import json
import locale
import os
from pathlib import Path
import re
import shutil
import tempfile
import time


CONTAINER_NAME = "PCB STEP export"
NO_VARIATIONS = "[No Variations]"


def _safe_value(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 32 or c == "|" for c in value):
        raise ValueError(f"{label} must be nonempty and cannot contain control characters or '|'")
    return value


def _absolute_path(value: str, suffixes: set, label: str) -> Path:
    _safe_value(value, label)
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    path = path.resolve()
    if str(path).startswith("\\\\"):
        raise ValueError(f"{label} must be on a local drive; use a local working copy for Altium")
    if path.suffix.lower() not in suffixes:
        raise ValueError(f"{label} must have one of these extensions: {', '.join(sorted(suffixes))}")
    return path


def _project_text(project: Path, encoding: str) -> tuple:
    raw = project.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    try:
        text = raw.decode("utf-8-sig")
        return text, "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    except UnicodeDecodeError:
        return raw.decode(encoding), encoding


def _read_project(project: Path, encoding: str) -> configparser.ConfigParser:
    text, _ = _project_text(project, encoding)
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.read_string(text)
    return ini


def _document_path(project: Path, value: str) -> Path:
    # Project files use Windows separators even in offline, cross-platform tests.
    path = Path(value.replace("\\", os.sep))
    return (path if path.is_absolute() else project.parent / path).resolve()


def build_export_project(project: Path, outjob: Path, encoding: str) -> bytes:
    """Preserve project settings/variants, rebasing only its document references.

    The export job belongs to this disposable project, so Altium resolves its
    assembly variant in an explicit project rather than a free-document context.
    The source project's text and files are never rewritten.
    """
    text, source_encoding = _project_text(project, encoding)
    lines = text.splitlines(keepends=True)
    document_section = False
    highest_document = 0
    result = []
    for line in lines:
        section = re.match(r"^\s*\[([^]\r\n]+)\]", line)
        if section:
            document = re.fullmatch(r"Document(\d+)", section.group(1), re.IGNORECASE)
            document_section = document is not None
            if document:
                highest_document = max(highest_document, int(document.group(1)))
        reference = re.match(r"^(\s*DocumentPath\s*=)([^\r\n]*)(\r?\n)?$", line, re.IGNORECASE)
        if document_section and reference:
            resolved = str(_document_path(project, reference.group(2).strip()))
            _safe_value(resolved, "project document path")
            line = reference.group(1) + resolved + (reference.group(3) or "")
        result.append(line)
    newline = "\r\n" if "\r\n" in text else "\n"
    result.append(newline + f"[Document{highest_document + 1}]" + newline
                  + f"DocumentPath={outjob}" + newline)
    return "".join(result).encode(source_encoding)


def validate_sources(project_path: str, pcb_path: str, output_path: str,
                     variant: str, encoding: str) -> tuple:
    project = _absolute_path(project_path, {".prjpcb"}, "project_path")
    pcb = _absolute_path(pcb_path, {".pcbdoc"}, "pcb_path")
    output = _absolute_path(output_path, {".step", ".stp"}, "output_path")
    _safe_value(variant, "variant")
    for source in (project, pcb):
        if not source.is_file():
            raise ValueError(f"Source file not found: {source}")
    if output.exists():
        raise ValueError(f"Refusing to overwrite an existing output: {output}")
    if not output.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {output.parent}")
    ini = _read_project(project, encoding)
    documents = {
        _document_path(project, ini.get(section, "DocumentPath"))
        for section in ini.sections()
        if re.fullmatch(r"Document\d+", section, re.IGNORECASE)
        and ini.has_option(section, "DocumentPath")
    }
    if pcb not in documents:
        raise ValueError("pcb_path is not a document of project_path")
    variants = {
        ini.get(section, "Description")
        for section in ini.sections()
        if re.fullmatch(r"ProjectVariant\d+", section, re.IGNORECASE)
        and ini.has_option(section, "Description")
    }
    if variant != NO_VARIATIONS and variant not in variants:
        raise ValueError(f"Unknown assembly variant {variant!r}; available: {sorted(variants)}")
    protected = {project, pcb} | {
        doc for doc in documents if doc.is_file()
    }
    return project, pcb, output, protected


def build_step_outjob(pcb_path: str, output_directory: str, variant: str,
                      include_extruded_bodies: bool = False) -> str:
    """Build one explicit ExportSTEP generator, with all components and holes.

    The serialized configuration is Altium's ExportSTEPView record. Model
    options are AsStep=0 and AsBoth=2; component/holes option 0 means all.
    """
    for value, label in ((pcb_path, "pcb_path"), (output_directory, "output_directory"),
                         (variant, "variant")):
        _safe_value(value, label)
    models = 2 if include_extruded_bodies else 0
    configuration = (
        "Record=ExportSTEPView|ExportComponentOptions=0"
        f"|ExportModelsOption={models}|ExportHolesOption=0|CanSelectPrimitives=False"
        "|IncludeMechanicalPadHoles=True|IncludeElectricalPadHoles=True"
        "|IncludeFreePadHoles=True|ComponentSuffixType=0|ComponentSuffix="
        "|ExportAsSinglePart=False|SkipFreeBodies=False|SkipHidden=False"
    )
    lines = [
        "[OutputJobFile]", "Version=1.0", "", "[OutputGroup1]",
        "Name=PCB STEP export", f"TargetOutputMedium={CONTAINER_NAME}",
        f"VariantName={variant}", "VariantScope=1", "CurrentConfigurationName=",
        f"OutputMedium1={CONTAINER_NAME}", "OutputMedium1_Type=GeneratedFiles",
        "OutputType1=ExportSTEP", "OutputName1=PCB STEP", "OutputCategory1=Export",
        f"OutputDocumentPath1={pcb_path}", f"OutputVariantName1={variant}",
        "OutputEnabled1=1", "OutputEnabled1_OutputMedium1=1", "OutputDefault1=0",
        "Configuration1_Name1=OutputConfigurationParameter1",
        f"Configuration1_Item1={configuration}", "", "[PublishSettings]",
        "OutputFilePath1=", "ReleaseManaged1=1", f"OutputBasePath1={output_directory}",
        "OutputPathMedia1=", "OutputPathMediaValue1=", "OutputPathOutputer1=",
        "OutputPathOutputerPrefix1=", "OutputPathOutputerValue1=",
        "OutputFileName1=", "OutputFileNameMulti1=", "UseOutputNameForMulti1=1",
        "OutputFileNameSpecial1=", "OpenOutput1=0", "", "[GeneratedFilesSettings]",
        f"RelativeOutputPath1={output_directory}", "OpenOutputs1=0", "AddToProject1=0",
        "TimestampFolder1=0", "UseOutputName1=0", "OpenODBOutput1=0",
        "OpenGerberOutput1=0", "OpenNCDrillOutput1=0", "OpenIPCOutput1=0",
        "EnableReload1=0", "",
    ]
    return "\r\n".join(lines)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bridge_error(response: dict) -> str:
    if not isinstance(response, dict) or not response.get("success"):
        return str(response.get("error", response)) if isinstance(response, dict) else str(response)
    result = response.get("result")
    if isinstance(result, str):
        if result.startswith("ERROR:"):
            return result
        try:
            result = json.loads(result)
        except ValueError:
            result = None
    if isinstance(result, dict) and (result.get("success") is False or result.get("error")):
        return str(result.get("error", result))
    return ""


def _complete_step(path: Path, started_ns: int) -> bool:
    stat = path.stat()
    # A private, initially empty directory is the primary freshness guarantee.
    # The small allowance accommodates coarse filesystem timestamp resolution.
    if stat.st_size < 100 or stat.st_mtime_ns < started_ns - 2_000_000_000:
        return False
    with path.open("rb") as stream:
        header = stream.read(4096)
        stream.seek(max(0, stat.st_size - 4096))
        trailer = stream.read()
    return (header.lstrip().startswith(b"ISO-10303-21;")
            and b"FILE_SCHEMA" in header and trailer.rstrip().endswith(b"END-ISO-10303-21;"))


async def _wait_for_step(directory: Path, started_ns: int, timeout: float) -> Path:
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        candidates = [p for p in directory.rglob("*")
                      if p.is_file() and p.suffix.lower() in {".step", ".stp"}]
        if len(candidates) > 1:
            raise ValueError("Export produced more than one STEP file; refusing an ambiguous result")
        if candidates:
            candidate = candidates[0]
            stat = candidate.stat()
            signature = (str(candidate), stat.st_size, stat.st_mtime_ns)
            if signature == previous and _complete_step(candidate, started_ns):
                return candidate
            previous = signature
        if time.monotonic() >= deadline:
            raise ValueError("No fresh, complete STEP file was produced in the isolated output directory")
        await asyncio.sleep(min(0.5, max(0, deadline - time.monotonic())))


async def export_step(execute_command, project_path: str, pcb_path: str, output_path: str,
                      variant: str = NO_VARIATIONS, include_extruded_bodies: bool = False,
                      verification_timeout: float = 30, encoding: str = None) -> dict:
    """Run the dedicated job through the existing bridge and verify its artifact.

    execute_command is the server's existing bridge callback. Tests replace it
    with a fake; this module never starts Altium or a second bridge itself.
    """
    result = {"success": False}
    try:
        if not 0 <= verification_timeout <= 600:
            raise ValueError("verification_timeout must be between 0 and 600 seconds")
        encoding = encoding or ("mbcs" if os.name == "nt" else locale.getpreferredencoding(False))
        project, pcb, output, protected = validate_sources(
            project_path, pcb_path, output_path, variant, encoding)
        snapshots = {str(path): file_hash(path) for path in sorted(protected)}
        directory = Path(tempfile.mkdtemp(prefix="altium-step-", dir=output.parent))
        outjob = directory / "PCB_STEP.OutJob"
        export_project = directory / "STEP_Export.PrjPcb"
        result.update(project_path=str(project), pcb_path=str(pcb), variant=variant,
                      output_path=str(output), outjob_path=str(outjob), run_directory=str(directory),
                      export_project_path=str(export_project))
        job = build_step_outjob(str(pcb), str(directory), variant, include_extruded_bodies)
        # TIniFile uses the Windows ANSI code page. Refuse names it cannot
        # represent instead of silently changing the source or variant.
        job_bytes = job.encode(encoding)
        with outjob.open("xb") as stream:
            stream.write(job_bytes)
        with export_project.open("xb") as stream:
            stream.write(build_export_project(project, outjob, encoding))
        started_ns = time.time_ns()
        response = await execute_command("export_pcb_step", {
            "project_path": str(project), "export_project_path": str(export_project),
            "pcb_path": str(pcb),
            "variant": variant, "outjob_path": str(outjob)})
        result["bridge_response"] = response
        error = _bridge_error(response)
        if error:
            raise ValueError(f"STEP export command failed: {error}")
        generated = await _wait_for_step(directory, started_ns, verification_timeout)
        changed = [name for name, expected in snapshots.items()
                   if not Path(name).is_file() or file_hash(Path(name)) != expected]
        if changed:
            raise ValueError(f"Source files changed during export: {changed}")
        digest = file_hash(generated)
        # Exclusive creation prevents both accidental overwrite and a race with
        # another export that finishes while this one is running.
        with output.open("xb") as destination, generated.open("rb") as source:
            shutil.copyfileobj(source, destination)
        if file_hash(output) != digest:
            raise ValueError("Exported STEP changed while being copied; output is not verified")
        result.update(success=True, generated_path=str(generated), size_bytes=output.stat().st_size,
                      sha256=digest, source_sha256=snapshots,
                      include_extruded_bodies=include_extruded_bodies,
                      export_as_single_part=False)
    except (OSError, ValueError, configparser.Error) as exc:
        result["error"] = str(exc)
    return result
