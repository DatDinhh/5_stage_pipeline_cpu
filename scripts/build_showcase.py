#!/usr/bin/env python3
"""Regenerate two evidence-backed CPU bug waveforms using only the stdlib.

This is a small demonstration suite, not a replacement for signoff. Both
"fault injected" panels are measured simulations of the existing isolated
mutations, not historical waveforms. Repaired runs retain all normal checks.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
import csv
from dataclasses import dataclass
from html import escape
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_tests as runner_api


class ShowcaseError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ShowcaseError(message)


@dataclass
class Signal:
    width: int
    events: list[tuple[int, str]]

    def at(self, timestamp: int) -> str:
        index = bisect_right(self.events, (timestamp, chr(0x10FFFF))) - 1
        return self.events[index][1] if index >= 0 else "x"


class VCD:
    """Read selected VCD signals, preserving actual settled timestamp changes.

    Multiple delta updates at one timestamp are coalesced to the final value.
    No signals are inferred from the CSV, reconstructed, or interpolated.
    """

    def __init__(self, path: Path, requested: set[str]) -> None:
        content = path.read_text(encoding="utf-8")
        header, changes = content.split("$enddefinitions $end", 1)
        scale = re.search(r"\$timescale\s+(\d+)\s*(s|ms|us|ns|ps|fs)\s+\$end", header)
        require(scale is not None, f"missing VCD timescale: {path}")
        self.tick_ns = int(scale[1]) * {
            "s": 1e9, "ms": 1e6, "us": 1e3, "ns": 1, "ps": 1e-3, "fs": 1e-6,
        }[scale[2]]
        self.timescale = f"{scale[1]}{scale[2]}"
        scopes: list[str] = []
        definitions: dict[str, tuple[str, int]] = {}
        for line in header.splitlines():
            words = line.split()
            if not words:
                continue
            if words[0] == "$scope":
                scopes.append(words[2])
            elif words[0] == "$upscope":
                scopes.pop()
            elif words[0] == "$var":
                name = ".".join(scopes + [words[4]])
                if name in requested:
                    definitions[name] = (words[3], int(words[2]))
        require(requested <= definitions.keys(),
                f"VCD missing signals {sorted(requested - definitions.keys())}: {path}")
        by_code = {code: [] for code, _ in definitions.values()}
        timestamp = 0
        for line in changes.splitlines():
            line = line.strip()
            if not line or line.startswith("$"):
                continue
            if line.startswith("#"):
                timestamp = int(line[1:])
                continue
            if line[0] in "bB":
                value, code = line[1:].split()
            elif line[0] in "01xXzZ":
                value, code = line[0], line[1:]
            else:
                continue
            if code in by_code:
                events = by_code[code]
                event = (timestamp, value.lower())
                if events and events[-1][0] == timestamp:
                    events[-1] = event
                elif not events or events[-1][1] != event[1]:
                    events.append(event)
        # Delta activity that returns to the previous settled value is not a
        # visible transition in the rendered waveform.
        for code, events in by_code.items():
            settled = []
            for event in events:
                if not settled or settled[-1][1] != event[1]:
                    settled.append(event)
            by_code[code] = settled
        self.signals = {name: Signal(width, by_code[code])
                        for name, (code, width) in definitions.items()}
        self.timestamps = sorted({time for signal in self.signals.values()
                                  for time, _ in signal.events})

    def number(self, name: str, timestamp: int) -> int | None:
        value = self.signals[name].at(timestamp)
        return None if "x" in value or "z" in value else int(value, 2)

    def find(self, condition: Callable[[int], bool]) -> int:
        return next((time for time in self.timestamps if condition(time)), -1)


COMMON = {"tb_core.clk", "tb_core.trace_valid", "tb_core.trace_pc",
          "tb_core.trace_rd_data", "tb_core.trace_rd", "tb_core.trace_rd_we",
          "tb_core.trace_cycle"}
FORWARD = COMMON | {"tb_core.dut.ID_EX_pc", "tb_core.dut.idex_fire",
                    "tb_core.dbg_forward_a", "tb_core.dut.fwdA",
                    "tb_core.dut.exmem_result_value", "tb_core.dut.MEM_WB_result"}
LANE = COMMON | {"tb_core.dut.EX_MEM_pc", "tb_core.db_req_a",
                "tb_core.db_resp_v", "tb_core.db_resp_r", "tb_core.db_resp_d",
                "tb_core.dut.external_load_value"}


def trace_at(path: Path, pc: int) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as stream:
        matches = [row for row in csv.DictReader(stream) if int(row["pc"], 16) == pc]
    require(len(matches) == 1, f"expected exactly one retire at PC {pc:#x}: {path}")
    return matches[0]


def format_value(bits: str, style: str) -> str:
    if "x" in bits or "z" in bits:
        return bits.upper()
    value = int(bits, 2)
    if style == "select":
        return {0: "00 RF", 1: "01 WB", 2: "10 EX"}.get(value, f"{value:02b}")
    if style.startswith("hex"):
        return f"0x{value:0{int(style[3:])}x}"
    return str(value)


def render_svg(title: str, subtitle: str, decision: str, panels: list[dict],
               rows: list[tuple[str, str, str]]) -> str:
    width = 1360
    row_h = 36
    panel_h = 108 + row_h * len(rows)
    height = 164 + 2 * panel_h + 82
    x0, x1 = 302, 1316
    start = min(panel["decision_time"] for panel in panels) - panels[0]["period"]
    end = max(panel["retire_time"] for panel in panels) + panels[0]["period"]
    tick_ns = panels[0]["vcd"].tick_ns
    require(all(panel["vcd"].tick_ns == tick_ns for panel in panels), "timescale mismatch")
    require(end > start >= 0, "invalid measured waveform window")
    def x(time: int) -> float:
        return x0 + (time - start) / (end - start) * (x1 - x0)
    def text(px: float, py: float, value: str, size: int = 14,
             color: str = "#25364b", extra: str = "") -> str:
        return f'<text x="{px:g}" y="{py:g}" font-size="{size}" fill="{color}" {extra}>{escape(value)}</text>'
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
           f'<title id="title">{escape(title)}</title>',
           '<desc id="desc">Real Icarus VCD transitions. Fault-injected isolated mutation compared with current repaired RTL. Absolute simulation time in nanoseconds; delta updates at a timestamp show their final settled value.</desc>',
           '<style>text{font-family:Arial,sans-serif}.mono{font-family:Consolas,monospace}</style>',
           f'<rect width="{width}" height="{height}" fill="#f5f7fb"/>',
           text(44, 43, "MEASURED RTL WAVEFORMS / TWO CONTROLLED RUNS", 13, "#526780"),
           text(44, 82, title, 28, "#11263f"),
           text(44, 111, subtitle, 16),
           text(44, 139, "True VCD transitions · 10 ns clock · axes show absolute simulation time (ns) · final delta value at each timestamp", 13, "#526780")]
    for index, panel in enumerate(panels):
        top = 164 + index * panel_h
        color = "#bb3c42" if index == 0 else "#167755"
        wave_top = top + 88
        wave_bottom = wave_top + row_h * len(rows)
        out.extend([f'<rect x="28" y="{top}" width="1304" height="{panel_h - 12}" rx="12" fill="white" stroke="#d8e0ea"/>',
                    f'<rect x="28" y="{top}" width="7" height="{panel_h - 12}" rx="3" fill="{color}"/>',
                    text(48, top + 30, panel["heading"], 18, color),
                    text(48, top + 57, panel["result"], 14),
                    text(48, top + 79, "Signal / actual RTL name", 12, "#65778b")])
        decision_time = panel["decision_time"]
        out.append(f'<rect x="{x(decision_time):g}" y="{wave_top - 6}" width="{x(decision_time + panel["period"]) - x(decision_time):g}" height="{wave_bottom - wave_top}" fill="#fff3d9"/>')
        tick = start
        while tick <= end:
            px = x(tick)
            out.extend([f'<line x1="{px:g}" y1="{wave_top - 8}" x2="{px:g}" y2="{wave_bottom - 8}" stroke="#e4eaf2"/>',
                        text(px, wave_top - 12, f"{tick * tick_ns:g}", 12, "#65778b", 'text-anchor="middle"')])
            tick += panel["period"]
        retire_x = x(panel["retire_time"])
        out.append(f'<line x1="{retire_x:g}" y1="{wave_top - 8}" x2="{retire_x:g}" y2="{wave_bottom - 8}" stroke="{color}" stroke-dasharray="4 4"/>')
        for row_index, (name, label, style) in enumerate(rows):
            y = wave_top + row_index * row_h
            signal = panel["vcd"].signals[name]
            out.append(text(48, y + 18, label, 13, "#25364b", 'class="mono"'))
            times = [start] + [t for t, _ in signal.events if start < t < end] + [end]
            if style == "bit":
                points = []
                for left, right in zip(times, times[1:]):
                    value = signal.at(left)
                    py = y + (6 if value == "1" else 23 if value == "0" else 14)
                    points.extend([(x(left), py), (x(right), py)])
                out.append('<polyline points="' + " ".join(f"{px:g},{py:g}" for px, py in points) + f'" fill="none" stroke="{color}" stroke-width="2"/>')
            else:
                for left, right in zip(times, times[1:]):
                    xa, xb = x(left), x(right)
                    value = format_value(signal.at(left), style)
                    corner = min(4, (xb - xa) / 3)
                    out.append(f'<path d="M {xa:g},{y + 14} L {xa + corner:g},{y + 4} H {xb - corner:g} L {xb:g},{y + 14} L {xb - corner:g},{y + 25} H {xa + corner:g} Z" fill="#f8fafc" stroke="{color}" stroke-width="1.2"/>')
                    if xb - xa > len(value) * 7 + 10:
                        out.append(text((xa + xb) / 2, y + 19, value, 13, color, 'class="mono" text-anchor="middle"'))
        out.append(text(48, wave_bottom + 3, f"Gold band: {decision}. Dashed line: checked retirement at PC {panel['pc']:#x}, cycle {panel['cycle']}.", 12, "#526780"))
    footer = 164 + 2 * panel_h
    out.extend([text(44, footer + 16, "Fault injection is a deliberate regression reproduction in a copied source tree; repaired RTL is checked against the independent ISS.", 14),
                text(44, footer + 41, "VCD/CSV paths, SHA-256 hashes and exact decision samples: showcase_evidence.json", 12, "#526780"),
                '</svg>'])
    return "\n".join(out) + "\n"


def make_case(runner: runner_api.VerificationRunner, *, kind: str,
              repaired_name: str, mutant_name: str, pc: int, rd: int,
              expected: int, faulty: int) -> tuple[str, dict]:
    records = {record["name"]: record for record in runner.results}
    sides = [("fault_injected", records[mutant_name]), ("repaired", records[repaired_name])]
    panels = []
    metadata: dict = {"kind": kind, "pc": f"0x{pc:08x}", "register": rd,
                      "expected_value": f"0x{expected:08x}", "faulty_value": f"0x{faulty:08x}",
                      "runs": {}}
    for side, record in sides:
        folder = ROOT / record["artifacts"]
        expected_trace = trace_at(folder / "expected_trace.csv", pc)
        trace = trace_at(folder / "trace.csv", pc)
        value = faulty if side == "fault_injected" else expected
        require(int(expected_trace["rd_data"], 16) == expected, f"unexpected ISS result: {kind}")
        require(int(trace["rd_data"], 16) == value and int(trace["rd"]) == rd and trace["rd_we"] == "1",
                f"unexpected measured retirement: {kind}/{side}")
        if side == "fault_injected":
            require(record["status"] == "pass" and record["failure_category"] == "TRACE_MISMATCH",
                    f"mutation was not functionally detected: {kind}")
        else:
            require(record["status"] == "pass", f"repaired run failed: {kind}")
        vcd = VCD(folder / "waves.vcd", FORWARD if kind == "forwarding" else LANE)
        rising = [t for t, v in vcd.signals["tb_core.clk"].events if v == "1"]
        require(len(rising) > 2 and len(set(b - a for a, b in zip(rising, rising[1:]))) == 1,
                "nonuniform or missing VCD clock")
        period = rising[1] - rising[0]
        require(abs(period * vcd.tick_ns - 10) < 1e-9, "unexpected clock period")
        retire_time = vcd.find(lambda t: vcd.number("tb_core.trace_valid", t) == 1
                               and vcd.number("tb_core.trace_pc", t) == pc
                               and vcd.number("tb_core.trace_rd", t) == rd
                               and vcd.number("tb_core.trace_rd_we", t) == 1)
        require(retire_time >= 0 and vcd.number("tb_core.trace_rd_data", retire_time) == value
                and vcd.number("tb_core.trace_cycle", retire_time) == int(trace["cycle"]),
                f"VCD and checked CSV disagree: {kind}/{side}")
        if kind == "forwarding":
            decision_time = vcd.find(lambda t: vcd.number("tb_core.dut.ID_EX_pc", t) == pc
                                    and vcd.number("tb_core.dut.idex_fire", t) == 1)
            checks = {"tb_core.dbg_forward_a": 1 if side == "fault_injected" else 2,
                      "tb_core.dut.fwdA": 1 if side == "fault_injected" else 3,
                      "tb_core.dut.exmem_result_value": 3, "tb_core.dut.MEM_WB_result": 1}
            heading = "Fault injected / WB incorrectly wins" if side == "fault_injected" else "Repaired RTL / newer EX/MEM value wins"
        else:
            decision_time = vcd.find(lambda t: vcd.number("tb_core.dut.EX_MEM_pc", t) == pc
                                    and vcd.number("tb_core.db_resp_v", t) == 1
                                    and vcd.number("tb_core.db_resp_r", t) == 1)
            checks = {"tb_core.db_req_a": 0x42, "tb_core.db_resp_d": 0x80ff7f01,
                      "tb_core.dut.external_load_value": value}
            heading = "Fault injected / byte offset 2 selects bits 15:8" if side == "fault_injected" else "Repaired RTL / byte offset 2 selects bits 23:16"
        require(decision_time >= 0, f"missing decision interval: {kind}/{side}")
        for name, checked_value in checks.items():
            require(vcd.number(name, decision_time) == checked_value,
                    f"unexpected decision value {name}: {kind}/{side}")
        panels.append({"vcd": vcd, "period": period, "decision_time": decision_time,
                       "retire_time": retire_time, "pc": pc, "cycle": int(trace["cycle"]),
                       "heading": heading,
                       "result": f"Checked retirement: r{rd} = 0x{value:08x}; independent ISS expects 0x{expected:08x}. "
                                 + ("Mutation killed by TRACE_MISMATCH." if side == "fault_injected" else "Complete trace and final architectural state PASS.")})
        metadata["runs"][side] = {
            "result_name": record["name"], "artifacts": record["artifacts"],
            "retire_trace_row": trace,
            "iss_trace_row": expected_trace, "timescale": vcd.timescale,
            "decision_timestamp": decision_time, "retire_timestamp": retire_time,
            "decision_sample": {name: vcd.number(name, decision_time) for name in checks},
            "artifact_hashes": {name: runner_api.sha256_file(folder / name)
                                for name in ("waves.vcd", "trace.csv", "expected_trace.csv",
                                             "state.txt", "expected_state.txt", "program.hex", "data.hex",
                                             "coverage.txt", "source.asm", "sim.log")},
        }
    if kind == "forwarding":
        rows = [("tb_core.clk", "clk", "bit"),
                ("tb_core.dut.ID_EX_pc", "ID_EX_pc", "hex2"),
                ("tb_core.dbg_forward_a", "forwardA (00 RF / 01 WB / 10 EX)", "select"),
                ("tb_core.dut.exmem_result_value", "EX/MEM result (decimal)", "dec"),
                ("tb_core.dut.MEM_WB_result", "MEM/WB result (decimal)", "dec"),
                ("tb_core.dut.fwdA", "forwarded operand A (decimal)", "dec"),
                ("tb_core.trace_valid", "trace_valid", "bit"),
                ("tb_core.trace_pc", "trace_pc", "hex2"),
                ("tb_core.trace_rd_data", "trace_rd_data", "hex8")]
        title = "Forwarding priority: the newest producer must win"
        subtitle = "addi r1, r0, 1 → addi r1, r1, 2 → add r2, r1, r1     |     correct r2 = 3 + 3 = 6; injected fault gives 1 + 3 = 4"
        decision = "ADD operand selection"
    else:
        rows = [("tb_core.clk", "clk", "bit"),
                ("tb_core.db_req_a", "dbus_req_addr", "hex8"),
                ("tb_core.db_resp_v", "dbus_resp_valid", "bit"),
                ("tb_core.db_resp_d", "dbus_resp_rdata (raw word)", "hex8"),
                ("tb_core.dut.external_load_value", "external_load_value", "hex8"),
                ("tb_core.trace_valid", "trace_valid", "bit"),
                ("tb_core.trace_pc", "trace_pc", "hex2"),
                ("tb_core.trace_rd_data", "trace_rd_data", "hex8")]
        title = "Byte lanes: select the addressed byte before sign extension"
        subtitle = "lb r3, 2(r10), r10 = 0x40     |     RAM word = 0x80ff7f01; offset 2 is 0xff, so signed LB must produce 0xffffffff"
        decision = "owned load response / byte selection"
    metadata["waveform_signals"] = [name for name, _, _ in rows]
    return render_svg(title, subtitle, decision, panels, rows), metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sim", default="iverilog")
    parser.add_argument("--vvp", default=None)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    cli = ["--suite", "directed", "--waves", "--sim", args.sim, "--timeout", str(args.timeout)]
    if args.vvp:
        cli.extend(["--vvp", args.vvp])
    runner_args = runner_api.parse_args(cli)
    runner_args.suite = "showcase"
    tests = runner_api.load_plan(ROOT / "tests/testplan.json")
    runner = runner_api.VerificationRunner(runner_args, tests)
    try:
        runner.run_directed([test for test in tests
                             if test["id"] in {"raw_forwarding_priority", "memory_lanes"}])
        runner.run_mutations()
        evidence_dir = runner.session_dir / "showcase"
        evidence_dir.mkdir()
        cases = [
            ("forwarding_waveform.svg", dict(kind="forwarding",
             repaired_name="raw_forwarding_priority__fast__seed-0",
             mutant_name="wb_over_ex_forwarding__fast__seed-0", pc=8, rd=2, expected=6, faulty=4)),
            ("byte_lane_waveform.svg", dict(kind="byte_lane",
             repaired_name="memory_lanes__fast__seed-0",
             mutant_name="wrong_byte_lane__fast__seed-0", pc=12, rd=3, expected=0xffffffff, faulty=0x7f)),
        ]
        metadata = {"schema_version": 2, "kind": "measured_vcd_showcase",
                    "scope": "Two isolated fault injections and repaired directed runs; not full signoff",
                    "generator": "scripts/build_showcase.py",
                    "reproduce": "python scripts/build_showcase.py",
                    "session": runner_api.rel(runner.session_dir), "cases": {}}
        for filename, parameters in cases:
            svg, case = make_case(runner, **parameters)
            output = evidence_dir / filename
            output.write_text(svg, encoding="utf-8", newline="\n")
            metadata["cases"][parameters["kind"]] = dict(case, svg=runner_api.rel(output),
                                                       svg_sha256=runner_api.sha256_file(output))
        metadata["mutation_sources"] = {
            path.parent.name: json.loads(path.read_text(encoding="utf-8"))
            for path in (runner.session_dir / "mutation_sources").glob("*/mutation.json")}
        runner.enforce_input_hashes()
        runner.manifest["showcase"] = {"metadata": runner_api.rel(evidence_dir / "showcase_evidence.json"),
                                       "assets": [case["svg"] for case in metadata["cases"].values()]}
        manifest = runner.finalize("pass")
        metadata.update(manifest=runner_api.rel(manifest), manifest_sha256=runner_api.sha256_file(manifest),
                        source_input_rehash=runner.manifest["source_input_rehash"],
                        source_hashes=runner.manifest["final_input_hashes"])
        (evidence_dir / "showcase_evidence.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        published = ROOT / "docs/assets"
        published.mkdir(parents=True, exist_ok=True)
        for filename in ("forwarding_waveform.svg", "byte_lane_waveform.svg", "showcase_evidence.json"):
            shutil.copy2(evidence_dir / filename, published / filename)
        print(f"SHOWCASE PASS: {runner_api.rel(manifest)}")
        print("Measured waveform assets: docs/assets/forwarding_waveform.svg, docs/assets/byte_lane_waveform.svg")
        return 0
    except (OSError, ValueError, KeyError, runner_api.RunnerError, ShowcaseError) as error:
        manifest = runner.finalize("fail", str(error))
        print(f"SHOWCASE FAIL: {error}\nManifest: {manifest}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
