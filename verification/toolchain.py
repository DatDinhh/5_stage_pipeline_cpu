"""Independent RTL lint/synthesis evidence, including native and WSL tools.

Missing optional tools are BLOCKED_TOOLING, never PASS. A discovered executable
that fails to run, times out, or rejects the RTL is FAIL. The caller must fail
signoff on FAIL and preserve any BLOCKED_TOOLING result in its manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from verification.synthesis import inspect_netlist


def _output_text(value: str | bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


def _invoke(command: list[str], *, root: Path, log: Path, timeout: int) -> dict[str, Any]:
    result: dict[str, Any] = {"command": command, "log": str(log), "returncode": None}
    try:
        process = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=timeout,
                                 check=False)
        result.update(returncode=process.returncode, stdout=process.stdout, stderr=process.stderr)
    except subprocess.TimeoutExpired as exc:
        result.update(error=f"TIMEOUT after {timeout}s", stdout=_output_text(exc.stdout),
                      stderr=_output_text(exc.stderr))
    except OSError as exc:
        result.update(error=f"EXECUTION_ERROR: {exc}", stdout="", stderr="")
    log.write_text(
        f"COMMAND: {json.dumps(command)}\nCWD: {root}\n"
        f"RETURN_CODE: {result['returncode']}\nERROR: {result.get('error', '')}\n"
        f"STDOUT:\n{result['stdout']}\nSTDERR:\n{result['stderr']}\n", encoding="utf-8")
    return result


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key not in ("stdout", "stderr")}


def _native_directories() -> list[Path]:
    if os.name != "nt":
        return [Path("/usr/local/bin"), Path("/opt/oss-cad-suite/bin")]
    directories = [Path(base) / suffix for base in ("C:/", "D:/") for suffix in (
        "oss-cad-suite/bin", "tools/oss-cad-suite/bin", "tools/bin",
        "msys64/mingw64/bin", "msys64/ucrt64/bin", "msys64/usr/bin")]
    for variable, fallback in (("ProgramFiles", "C:/Program Files"),
                               ("LOCALAPPDATA", str(Path.home() / "AppData/Local")),
                               ("ChocolateyInstall", "C:/ProgramData/chocolatey")):
        base = Path(os.environ.get(variable, fallback))
        directories.extend((base / "oss-cad-suite/bin", base / "bin",
                            base / "Programs/oss-cad-suite/bin"))
        for tool in ("verilator", "yosys"):
            directories.extend((base / tool / "bin", base / "lib" / tool / "tools" / "bin"))
    directories.append(Path.home() / "oss-cad-suite/bin")
    return directories


def discover_tool(name: str, *, root: Path, output_dir: Path) -> dict[str, Any]:
    """Find a native tool, then the default WSL distribution on Windows.

    CPU_VERIFY_VERILATOR / CPU_VERIFY_YOSYS can select a native executable.
    An invalid explicit selection is a configuration failure, not missing tooling.
    Discovery does not install packages or execute shell command strings.
    """
    override_name = f"CPU_VERIFY_{name.upper()}"
    override = os.environ.get(override_name)
    report: dict[str, Any] = {"searched": [], "commands": []}
    if override:
        report["searched"].append(f"{override_name}={override}")
        executable = shutil.which(override)
        if not executable:
            report.update(status="FAIL", reason=f"{override_name} does not resolve to an executable")
            return report
        report.update(status="FOUND", backend="native", executable=executable, prefix=[])
        return report

    names = (name, f"{name}_bin") if name == "verilator" and os.name == "nt" else (name,)
    for candidate in names:
        report["searched"].append(f"PATH:{candidate}")
        executable = shutil.which(candidate)
        if executable:
            report.update(status="FOUND", backend="native", executable=executable, prefix=[])
            return report
    for directory in _native_directories():
        for candidate in names:
            report["searched"].append(str(directory / candidate))
            executable = shutil.which(str(directory / candidate))
            if executable:
                report.update(status="FOUND", backend="native", executable=executable, prefix=[])
                return report

    wsl = shutil.which("wsl.exe") if os.name == "nt" else None
    if wsl:
        # which uses the WSL PATH, including any enabled OSS CAD suite. Its
        # normal exit 1 is absence; startup errors are preserved as evidence.
        probe = _invoke([wsl, "--exec", "which", name], root=root,
                        log=output_dir / "discovery_wsl.log", timeout=30)
        report["commands"].append(_summary(probe))
        report["searched"].append(f"WSL default distribution PATH:{name}")
        executable = probe["stdout"].strip()
        if probe["returncode"] == 0 and executable.startswith("/") and "\n" not in executable:
            report.update(status="FOUND", backend="wsl", executable=executable,
                          prefix=[wsl, "--exec"])
            return report
        if probe.get("error") or probe["returncode"] not in (0, 1):
            report["discovery_issue"] = probe.get("error", f"WSL probe exit {probe['returncode']}")
    report.update(status="BLOCKED_TOOLING", reason=f"{name} was not found in the searched locations")
    return report


def run_toolchain_checks(root: Path, output_dir: Path, *, timeout: int = 180) -> dict[str, Any]:
    """Write logs/report.json and return JSON-safe independent toolchain evidence.

    All rtl/core/*.v inputs are linted/synthesized with cpu_core as top. Verilator
    keeps its default warning-fatal policy. Yosys must pass hierarchy/check -assert
    and emit a nonempty JSON netlist. Neither tool replaces Icarus simulation.
    output_dir must not already exist, so stale logs or a previous netlist can
    never be reused as fresh evidence.
    """
    root, output_dir = root.resolve(), output_dir.resolve()
    if timeout <= 0:
        raise ValueError("toolchain timeout must be positive")
    sources = sorted((root / "rtl/core").glob("*.v"))
    if not sources or not (root / "rtl/core/cpu_core.v").is_file():
        raise ValueError("toolchain checks require rtl/core/cpu_core.v and its RTL sources")
    output_dir.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "top": "cpu_core", "scope": "RTL core only; independent of Icarus simulation",
        "synthesis_scope": {
            "target": "generic cells; no FPGA device or ASIC cell library",
            "parameters": {"DMEM_BYTES": 4096, "ENABLE_BPU": 1},
            "instrumentation": "default cpu_core includes trace/debug and verification control ports",
            "external_memory": "instruction/data RAM are outside cpu_core and excluded",
            "physical_results": "no area, timing, Fmax, power, or placed/routed implementation",
        },
        "sources": {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sources},
        "checks": [],
    }
    for name in ("verilator", "yosys"):
        directory = output_dir / name
        directory.mkdir(parents=True, exist_ok=True)
        discovery = discover_tool(name, root=root, output_dir=directory)
        check: dict[str, Any] = {"name": name, "kind": "lint" if name == "verilator" else "synthesis",
                                 "discovery": discovery, "commands": []}
        report["checks"].append(check)
        if discovery["status"] != "FOUND":
            check.update(status=discovery["status"], reason=discovery["reason"])
            continue
        check.update(executable=discovery["executable"], backend=discovery["backend"])
        prefix = discovery["prefix"] + [discovery["executable"]]
        version = _invoke(prefix + (["--version"] if name == "verilator" else ["-V"]),
                          root=root, log=directory / "version.log", timeout=30)
        check["commands"].append(_summary(version))
        check["version"] = (version["stdout"] + version["stderr"]).strip()[:2048]
        if version["returncode"] != 0 or not check["version"]:
            check.update(status="FAIL", reason="discovered tool did not report its version successfully")
            continue

        mapped_root, mapped_directory = root.as_posix(), directory.as_posix()
        if discovery["backend"] == "wsl":
            mapped = []
            for label, path in (("root", root), ("output", directory)):
                conversion = _invoke(discovery["prefix"] + ["wslpath", "-a", str(path)],
                                     root=root, log=directory / f"path_{label}.log", timeout=30)
                check["commands"].append(_summary(conversion))
                value = conversion["stdout"].strip()
                if conversion["returncode"] != 0 or not value.startswith("/") or "\n" in value:
                    check.update(status="FAIL", reason=f"WSL {label} path conversion failed")
                    break
                mapped.append(value)
            if check.get("status") == "FAIL":
                continue
            mapped_root, mapped_directory = mapped

        source_names = [f"{mapped_root}/{path.relative_to(root).as_posix()}" for path in sources]
        if name == "verilator":
            command = prefix + ["--lint-only", "--top-module", "cpu_core",
                                "--Mdir", f"{mapped_directory}/obj_dir"] + source_names
            check["warning_policy"] = "default fatal warnings; no warning suppression"
        else:
            def quoted(value: str) -> str:
                return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
            script = "\n".join((
                "read_verilog -sv " + " ".join(quoted(path) for path in source_names),
                "hierarchy -check -top cpu_core", "synth -top cpu_core", "check -assert", "stat",
                # Yosys 0.33 tee treats quote characters literally in -o.
                # Execute in the fresh output directory and use a fixed name;
                # subprocess cwd preserves paths containing spaces on native/WSL.
                "tee -o statistics.json stat -json",
                f"write_json {quoted(mapped_directory + '/netlist.json')}",
                f"write_verilog -noattr {quoted(mapped_directory + '/synthesized.v')}", ""))
            (directory / "synthesis.ys").write_text(script, encoding="utf-8")
            command = prefix + ["-s", f"{mapped_directory}/synthesis.ys"]
            check["script"] = str(directory / "synthesis.ys")
            check["script_sha256"] = hashlib.sha256((directory / "synthesis.ys").read_bytes()).hexdigest()
        execution = _invoke(command, root=directory if name == "yosys" else root,
                            log=directory / "run.log", timeout=timeout)
        check["commands"].append(_summary(execution))
        warnings = [line.strip() for line in (execution["stdout"] + "\n" + execution["stderr"]).splitlines()
                    if line.strip().startswith("Warning:")]
        check["warnings"] = sorted(set(warnings))
        check["warning_count"] = len(warnings)
        if execution["returncode"] != 0:
            check.update(status="FAIL", reason=execution.get("error", f"tool exited {execution['returncode']}"))
        elif name == "yosys" and (not (directory / "netlist.json").is_file()
                                   or (directory / "netlist.json").stat().st_size == 0):
            check.update(status="FAIL", reason="synthesis exited zero but did not emit a nonempty netlist")
        else:
            check["status"] = "PASS"
            if name == "yosys":
                check["netlist"] = str(directory / "netlist.json")
                check["netlist_sha256"] = hashlib.sha256((directory / "netlist.json").read_bytes()).hexdigest()
                try:
                    summary = inspect_netlist(directory / "netlist.json")
                    statistics = json.loads((directory / "statistics.json").read_text(encoding="utf-8"))
                    if statistics.get("design", {}).get("num_cells") != summary["leaf_cells"]:
                        raise ValueError("Yosys statistics and expanded netlist cell counts differ")
                    verilog = directory / "synthesized.v"
                    if not verilog.is_file() or verilog.stat().st_size == 0:
                        raise ValueError("synthesis did not emit a nonempty Verilog netlist")
                    summary_path = directory / "netlist_summary.json"
                    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")
                    check["netlist_summary"] = summary
                    check["artifacts"] = {filename: {
                        "path": str(directory / filename),
                        "sha256": hashlib.sha256((directory / filename).read_bytes()).hexdigest(),
                    } for filename in ("statistics.json", "synthesized.v", "netlist_summary.json")}
                except (OSError, ValueError, TypeError, AttributeError) as exc:
                    check.update(status="FAIL", reason=f"synthesis artifact validation failed: {exc}")
    statuses = {check["status"] for check in report["checks"]}
    report["status"] = "FAIL" if "FAIL" in statuses else "BLOCKED_TOOLING" if "BLOCKED_TOOLING" in statuses else "PASS"
    report["report_path"] = str(output_dir / "report.json")
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
