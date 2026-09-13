"""Structural evidence for the generic, verification-instrumented CPU netlist.

This does not prove RTL/netlist equivalence or provide FPGA utilization, ASIC
area, timing, or PPA. Electrical consistency remains Yosys check -assert's job.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


CPU_CORE_PORTS: dict[str, tuple[str, int]] = {
    "clk": ("input", 1), "rst_n": ("input", 1),
    "ibus_req_valid": ("output", 1), "ibus_req_ready": ("input", 1),
    "ibus_req_addr": ("output", 32), "ibus_resp_valid": ("input", 1),
    "ibus_resp_ready": ("output", 1), "ibus_resp_rdata": ("input", 32),
    "dbus_req_valid": ("output", 1), "dbus_req_ready": ("input", 1),
    "dbus_req_addr": ("output", 32), "dbus_req_wdata": ("output", 32),
    "dbus_req_wstrb": ("output", 4), "dbus_resp_valid": ("input", 1),
    "dbus_resp_ready": ("output", 1), "dbus_resp_rdata": ("input", 32),
    "fetch_enable": ("input", 1), "verification_halt": ("input", 1),
    "core_idle": ("output", 1),
    **{f"trace_{name}": ("output", width) for name, width in {
        "valid": 1, "epoch": 32, "event_order": 64, "retire_order": 64,
        "cycle": 64, "uid": 64, "trap": 1, "pc": 32, "instr": 32,
        "next_pc": 32, "rd_we": 1, "rd": 5, "rd_data": 32,
        "mem_valid": 1, "mem_write": 1, "mem_addr": 32, "mem_size": 2,
        "store_data": 32, "mem_wdata": 32, "mem_wstrb": 4,
        "mem_raw": 32, "mem_rdata": 32, "cause": 32, "epc": 32,
    }.items()},
    **{f"dbg_{name}": ("output", width) for name, width in {
        "forward_a": 2, "forward_b": 2, "forward_blocked": 1, "load_hazard": 1,
        "wb_id_bypass_rs": 1, "wb_id_bypass_rt": 1, "redirect": 1,
        "mispredict": 1, "branch_resolve": 1, "fetch_discard": 1, "exmem_wait": 1,
    }.items()},
}

# Yosys generic gate families from its simcells.v contract. A '$' prefix alone
# is not sufficient: it could conceal an unresolved or still-unmapped cell.
_COMBINATIONAL_INPUTS = {
    **{f"$_{name}_": "AB" for name in ("AND", "NAND", "OR", "NOR", "XOR", "XNOR", "ANDNOT", "ORNOT")},
    "$_BUF_": "A", "$_NOT_": "A", "$_MUX_": "ABS", "$_NMUX_": "ABS",
    "$_MUX4_": "ABCDST", "$_MUX8_": "ABCDEFGHSTU",
    "$_MUX16_": "ABCDEFGHIJKLMNOPSTUV", "$_AOI3_": "ABC", "$_OAI3_": "ABC",
    "$_AOI4_": "ABCD", "$_OAI4_": "ABCD", "$_TBUF_": "AE",
}
_FLIPFLOP_INPUTS = (
    (r"\$_FF_", ("D",)),
    (r"\$_DFF_[NP]_", ("D", "C")),
    (r"\$_DFFE_[NP]{2}_", ("D", "C", "E")),
    (r"\$_(?:DFF|SDFF)_[NP]{2}[01]_", ("D", "C", "R")),
    (r"\$_(?:DFFE|SDFFE|SDFFCE)_[NP]{2}[01][NP]_", ("D", "C", "R", "E")),
    (r"\$_ALDFF_[NP]{2}_", ("D", "C", "L", "AD")),
    (r"\$_ALDFFE_[NP]{3}_", ("D", "C", "L", "AD", "E")),
    (r"\$_DFFSR_[NP]{3}_", ("D", "C", "S", "R")),
    (r"\$_DFFSRE_[NP]{4}_", ("D", "C", "S", "R", "E")),
)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _flag(value: Any, label: str) -> bool:
    if type(value) is int and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and re.fullmatch(r"[01]+", value):
        return int(value, 2) != 0
    raise ValueError(f"{label} must be a Yosys binary flag")


def _bits(value: Any, label: str, *, allow_empty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{label} must be a bit vector")
    if any(not ((type(bit) is int and bit >= 0) or
                (isinstance(bit, str) and bit in ("0", "1", "x", "z"))) for bit in value):
        raise ValueError(f"{label} has an invalid bit")
    return value


def _primitive_ports(cell_type: str) -> tuple[dict[str, str], bool]:
    if "latch" in cell_type.lower() or cell_type == "$sr" or re.fullmatch(r"\$_SR_[NP]{2}_", cell_type):
        raise ValueError(f"latch-family cell is forbidden in generic FF-mapped flow: {cell_type}")
    if cell_type.lower().startswith("$mem"):
        raise ValueError(f"residual memory cell: {cell_type}")
    if cell_type in _COMBINATIONAL_INPUTS:
        return {**{name: "input" for name in _COMBINATIONAL_INPUTS[cell_type]}, "Y": "output"}, False
    for pattern, inputs in _FLIPFLOP_INPUTS:
        if re.fullmatch(pattern, cell_type):
            return {**{name: "input" for name in inputs}, "Q": "output"}, True
    raise ValueError(f"unresolved or unmapped cell type: {cell_type}")


def inspect_netlist(path: Path) -> dict[str, Any]:
    """Validate a Yosys JSON netlist and count reachable leaf cells by instance.

    Return JSON-safe structural/resource evidence, or raise ValueError. The
    exported CPU_CORE_PORTS defines the complete current top-level contract.
    Repeated module instances contribute repeatedly; unused definitions do not.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"cannot read synthesized JSON netlist: {exc}") from exc
    modules = _mapping(_mapping(data, "netlist").get("modules"), "modules")
    top = _mapping(modules.get("cpu_core"), "cpu_core module")
    attributes = _mapping(top.get("attributes", {}), "cpu_core attributes")
    if not _flag(attributes.get("top", "0"), "cpu_core top"):
        raise ValueError("cpu_core is not marked as the synthesized top")
    ports = _mapping(top.get("ports"), "cpu_core ports")
    if set(ports) != set(CPU_CORE_PORTS):
        raise ValueError(f"cpu_core port contract mismatch: missing={sorted(set(CPU_CORE_PORTS) - set(ports))}, "
                         f"extra={sorted(set(ports) - set(CPU_CORE_PORTS))}")
    for name, (direction, width) in CPU_CORE_PORTS.items():
        port = _mapping(ports[name], f"cpu_core port {name}")
        bits = _bits(port.get("bits"), f"cpu_core port {name}")
        if port.get("direction") != direction or len(bits) != width:
            raise ValueError(f"cpu_core port {name} must be {direction}[{width}]")
        if direction == "input" and any(type(bit) is not int for bit in bits):
            raise ValueError(f"cpu_core input {name} is tied to a constant")

    cache: dict[str, tuple[Counter[str], Counter[str], int]] = {}
    visiting: set[str] = set()
    per_module: dict[str, Any] = {}

    def expand(name: str) -> tuple[Counter[str], Counter[str], int]:
        if name in visiting:
            raise ValueError(f"recursive module hierarchy at {name}")
        if name in cache:
            return cache[name]
        if len(visiting) >= 256:
            raise ValueError("module hierarchy exceeds supported depth 256")
        module = _mapping(modules[name], f"module {name}")
        attrs = _mapping(module.get("attributes", {}), f"{name} attributes")
        for attr in ("blackbox", "whitebox"):
            if _flag(attrs.get(attr, "0"), f"{name} {attr}"):
                raise ValueError(f"reachable {attr} module: {name}")
        for field in ("memories", "processes"):
            if _mapping(module.get(field, {}), f"{name} {field}"):
                raise ValueError(f"residual {field} in module {name}")
        cells = _mapping(module.get("cells"), f"{name} cells")
        leaves: Counter[str] = Counter()
        instances: Counter[str] = Counter({name: 1})
        sequential = direct_leaves = direct_instances = 0
        visiting.add(name)
        for cell_name, raw_cell in cells.items():
            label = f"{name}.{cell_name}"
            cell = _mapping(raw_cell, f"cell {label}")
            cell_type = cell.get("type")
            if not isinstance(cell_type, str) or not cell_type:
                raise ValueError(f"cell {label} has no valid type")
            connections = _mapping(cell.get("connections"), f"{label} connections")
            directions = _mapping(cell.get("port_directions"), f"{label} port_directions")
            if set(connections) != set(directions):
                raise ValueError(f"cell {label} connection/direction ports differ")
            for port_name, bits in connections.items():
                if directions[port_name] not in ("input", "output", "inout"):
                    raise ValueError(f"cell {label}.{port_name} has an invalid direction")
                _bits(bits, f"cell {label}.{port_name}", allow_empty=directions[port_name] == "output")
            if cell_type in modules:
                child_leaves, child_instances, child_seq = expand(cell_type)
                leaves.update(child_leaves)
                instances.update(child_instances)
                sequential += child_seq
                direct_instances += 1
            else:
                expected_ports, is_sequential = _primitive_ports(cell_type)
                if directions != expected_ports or any(len(bits) != 1 for bits in connections.values()):
                    raise ValueError(f"generic primitive {label} has an invalid scalar port contract")
                leaves[cell_type] += 1
                sequential += int(is_sequential)
                direct_leaves += 1
        visiting.remove(name)
        per_module[name] = {"direct_leaf_cells": direct_leaves,
                            "direct_module_instances": direct_instances,
                            "expanded_leaf_cells": sum(leaves.values()),
                            "expanded_sequential_cells": sequential}
        cache[name] = leaves, instances, sequential
        return cache[name]

    leaves, instances, sequential = expand("cpu_core")
    leaf_count = sum(leaves.values())
    if leaf_count == 0:
        raise ValueError("cpu_core has no reachable generic leaf cells")
    parameters = _mapping(top.get("parameter_default_values", {}), "cpu_core default parameters")
    return {
        "status": "PASS", "top": "cpu_core", "creator": data.get("creator"),
        "scope": "Generic synthesized cpu_core including trace/debug outputs and live fetch_enable/verification_halt inputs; external instruction/data memories excluded",
        "resource_boundary": "Generic leaf primitive counts only; no target FPGA LUT/BRAM utilization, ASIC area, timing, PPA, or RTL/netlist equivalence claim",
        "parameters": {name: int(value, 2) if isinstance(value, str) and re.fullmatch(r"[01]+", value) else value
                       for name, value in parameters.items()},
        "top_ports": len(ports), "top_port_bits": sum(len(port["bits"]) for port in ports.values()),
        "module_definitions": len(modules), "reachable_modules": len(instances),
        "module_instances": sum(instances.values()), "child_module_instances": sum(instances.values()) - 1,
        "module_instances_by_type": dict(sorted(instances.items())),
        "leaf_cells": leaf_count, "leaf_cells_by_type": dict(sorted(leaves.items())),
        "sequential_cells": sequential, "combinational_cells": leaf_count - sequential,
        "latch_cells": 0, "residual_memories": 0, "residual_processes": 0,
        "blackbox_modules": 0, "unresolved_cells": 0,
        "per_module": dict(sorted(per_module.items())),
    }
