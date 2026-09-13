import copy
import json
from pathlib import Path
import tempfile
import unittest

from verification.synthesis import CPU_CORE_PORTS, inspect_netlist


def primitive(cell_type="$_DFF_P_"):
    inputs, output = (("D", "C"), "Q") if cell_type == "$_DFF_P_" else (("A",), "Y")
    return {"type": cell_type,
            "connections": {name: [index + 2] for index, name in enumerate((*inputs, output))},
            "port_directions": {**{name: "input" for name in inputs}, output: "output"}}


def instance(module):
    return {"type": module, "connections": {}, "port_directions": {}}


def valid_netlist():
    return {"creator": "Yosys test fixture", "modules": {"cpu_core": {
        "attributes": {"top": "1"},
        "ports": {name: {"direction": direction, "bits": list(range(width))}
                  for name, (direction, width) in CPU_CORE_PORTS.items()},
        "cells": {"state": primitive()},
    }}}


class SynthesisEvidenceTests(unittest.TestCase):
    def inspect(self, netlist):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "netlist.json"
            path.write_text(json.dumps(netlist), encoding="utf-8")
            return inspect_netlist(path)

    def test_valid_core_reports_generic_scope_and_current_full_interface(self):
        summary = self.inspect(valid_netlist())
        self.assertEqual(summary["leaf_cells"], 1)
        self.assertEqual(summary["sequential_cells"], 1)
        self.assertEqual(summary["module_instances"], 1)
        self.assertEqual(summary["latch_cells"], 0)
        self.assertIn("trace/debug", summary["scope"])
        self.assertIn("external instruction/data memories excluded", summary["scope"])
        self.assertIn("no target FPGA", summary["resource_boundary"])

    def test_expansion_counts_repeated_nested_instances_and_ignores_unused_definitions(self):
        netlist = valid_netlist()
        netlist["modules"]["leaf"] = {"cells": {"ff": primitive(), "not": primitive("$_NOT_")}}
        netlist["modules"]["wrapper"] = {"cells": {"a": instance("leaf"), "b": instance("leaf")}}
        netlist["modules"]["cpu_core"]["cells"] = {
            "a": instance("wrapper"), "b": instance("wrapper"), "local": primitive("$_NOT_")}
        netlist["modules"]["unused"] = {"attributes": {"blackbox": "1"}}
        result = self.inspect(netlist)
        self.assertEqual(result["leaf_cells"], 9)
        self.assertEqual(result["sequential_cells"], 4)
        self.assertEqual(result["combinational_cells"], 5)
        self.assertEqual(result["module_instances"], 7)
        self.assertEqual(result["reachable_modules"], 3)
        self.assertEqual(result["module_definitions"], 4)
        self.assertEqual(result["module_instances_by_type"], {"cpu_core": 1, "wrapper": 2, "leaf": 4})
        self.assertEqual(result["per_module"]["wrapper"]["expanded_leaf_cells"], 4)

    def test_malformed_unrelated_empty_and_duplicate_json_fail(self):
        for text in ("", "not json", "[]", "{}", '{"modules":{"unrelated":{}}}',
                     '{"modules":{},"modules":{}}'):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "bad.json"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    inspect_netlist(path)

    def test_missing_or_wrong_top_port_contract_and_constant_input_fail(self):
        mutations = []
        for change in ("missing", "width", "direction", "constant", "bad_bit", "not_top"):
            netlist = valid_netlist()
            top = netlist["modules"]["cpu_core"]
            if change == "missing":
                del top["ports"]["verification_halt"]
            elif change == "width":
                top["ports"]["trace_uid"]["bits"] = [1]
            elif change == "direction":
                top["ports"]["dbus_req_wdata"]["direction"] = "input"
            elif change == "constant":
                top["ports"]["clk"]["bits"] = ["0"]
            elif change == "bad_bit":
                top["ports"]["clk"]["bits"] = [True]
            else:
                top["attributes"]["top"] = "0"
            mutations.append((change, netlist))
        for change, netlist in mutations:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.inspect(netlist)

    def test_zero_reachable_cells_fail_even_when_unrelated_module_has_cells(self):
        netlist = valid_netlist()
        netlist["modules"]["unrelated"] = copy.deepcopy(netlist["modules"]["cpu_core"])
        netlist["modules"]["cpu_core"]["cells"] = {}
        with self.assertRaisesRegex(ValueError, "no reachable generic leaf cells"):
            self.inspect(netlist)

    def test_unresolved_hierarchy_and_unmapped_or_fake_primitives_fail(self):
        for cell_type in ("missing_module", "$alu", "$_FAKE_"):
            netlist = valid_netlist()
            netlist["modules"]["cpu_core"]["cells"]["bad"] = instance(cell_type)
            with self.subTest(cell_type=cell_type), self.assertRaisesRegex(ValueError, "unresolved or unmapped"):
                self.inspect(netlist)

    def test_recursive_hierarchy_and_reachable_blackboxes_fail(self):
        for condition in ("recursive", "blackbox", "whitebox"):
            netlist = valid_netlist()
            netlist["modules"]["cpu_core"]["cells"]["child"] = instance("child")
            child = {"cells": {"leaf": primitive()}}
            netlist["modules"]["child"] = child
            if condition == "recursive":
                child["cells"]["parent"] = instance("cpu_core")
            else:
                child["attributes"] = {condition: "0001"}
            with self.subTest(condition=condition), self.assertRaisesRegex(ValueError, condition):
                self.inspect(netlist)

    def test_residual_memories_processes_and_memory_cells_fail(self):
        for condition in ("memories", "processes", "$mem_v2"):
            netlist = valid_netlist()
            top = netlist["modules"]["cpu_core"]
            if condition.startswith("$"):
                top["cells"]["unmapped"] = instance(condition)
            else:
                top[condition] = {"unmapped": {}}
            with self.subTest(condition=condition), self.assertRaisesRegex(ValueError, "residual"):
                self.inspect(netlist)

    def test_all_latch_families_are_rejected(self):
        for cell_type in ("$dlatch", "$adlatch", "$dlatchsr", "$_DLATCH_P_", "$_DLATCHSR_PPP_", "$_SR_PP_"):
            netlist = valid_netlist()
            netlist["modules"]["cpu_core"]["cells"]["latch"] = instance(cell_type)
            with self.subTest(cell_type=cell_type), self.assertRaisesRegex(ValueError, "latch-family"):
                self.inspect(netlist)

    def test_malformed_cells_cannot_count_as_valid_primitive_evidence(self):
        for change in ("missing_connections", "missing_direction", "wrong_direction", "non_scalar"):
            netlist = valid_netlist()
            cell = netlist["modules"]["cpu_core"]["cells"]["state"]
            if change == "missing_connections":
                del cell["connections"]
            elif change == "missing_direction":
                del cell["port_directions"]["D"]
            elif change == "wrong_direction":
                cell["port_directions"]["Q"] = "input"
            else:
                cell["connections"]["Q"] = [2, 3]
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.inspect(netlist)


if __name__ == "__main__":
    unittest.main()
