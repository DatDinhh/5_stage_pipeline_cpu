# Generic RTL synthesis

This project synthesizes the default `cpu_core` using Yosys and checks the resulting netlist. The [validation report](validation_report.md) records the accepted run, measured counts, tool versions, and source hashes. The [showcase guide](showcase.md) explains the pipeline and the debugging examples.

## Reproduce

On Windows, the runner can use Icarus and Python natively and discover Yosys and Verilator in Ubuntu WSL. To install the synthesis dependency in an existing Ubuntu distribution:

```powershell
wsl.exe --user root --exec apt-get update
wsl.exe --user root --exec apt-get install --yes --no-install-recommends yosys
```

The Ubuntu package also supplies `yosys-abc`. The verification runner does not install packages; tool versions are recorded with each result.

From the project root, the complete reproduction command is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests.ps1 -Suite signoff
```

On Linux, install Python, Icarus, Verilator, and Yosys using the distribution's package manager, then run `python3 scripts/run_tests.py --suite signoff`. The runner discovers native tools first and the default WSL distribution second. Native executable overrides are available through `CPU_VERIFY_VERILATOR` and `CPU_VERIFY_YOSYS`.

## Synthesis scope

| Item | Configuration |
|---|---|
| Top | `cpu_core`; all eight `rtl/core/*.v` sources |
| Core parameters | `DMEM_BYTES=4096`, `ENABLE_BPU=1` |
| Predictor | 64 BHT entries and 32 BTB entries |
| Mapping | Yosys generic logic/flip-flop cells with ABC optimization |
| Included | Pipeline, register file, predictor, CSRs, trace/debug signals, UID/order/cycle registers, `fetch_enable` and `verification_halt` control ports |
| Excluded | External instruction/data RAM, simulation memories, bus shim, testbench, scoreboard, monitors |
| Technology | No FPGA part, ASIC standard-cell library, timing constraints, or place-and-route |

`DMEM_BYTES` checks the permitted external address range; it does not instantiate 4 KiB of internal RAM. The asynchronous reset behavior of the register-file and BPU arrays results in registers and selection logic in this generic mapping. Array-to-register replacement warnings are retained in the tool report. These counts must not be presented as FPGA LUT usage, SRAM area, production CPU area, achievable clock frequency, or power.

## Acceptance checks and artifacts

Each full signoff creates a fresh `build/runs/<run-id>/toolchain` directory. The generated Yosys script performs:

```text
read_verilog -sv <the explicit RTL source list>
hierarchy -check -top cpu_core
synth -top cpu_core
check -assert
stat
tee -o statistics.json stat -json
write_json netlist.json
write_verilog -noattr synthesized.v
```

Source and netlist paths in the actual script are absolute and quoted. Yosys executes in the fresh output directory so `tee` can use the fixed relative filename `statistics.json`, including when the directory contains spaces. `hierarchy -check` and `check -assert` reject structural issues reported by Yosys. The Python inspector also requires the expected top and interface, resolves reachable module instances, rejects blackboxes, residual processes/memories and latch-family cells, and counts leaf cells through the instance hierarchy. The expanded count must match Yosys's own statistics. Malformed, unrelated, empty, or stale artifacts cannot qualify as a passing synthesis result.

| Artifact under `toolchain/yosys/` | Purpose |
|---|---|
| `version.log`, `run.log` | Exact executable/version, command, diagnostics, and return code |
| `synthesis.ys` | Generated synthesis/check/export script |
| `netlist.json` | Machine-readable mapped design inspected by the gate |
| `synthesized.v` | Readable synthesized Verilog |
| `statistics.json` | Native Yosys statistics |
| `netlist_summary.json` | Checked hierarchy-expanded cell counts and structural summary |

The enclosing `toolchain/report.json` records status, source hashes, warnings, scope, and artifact hashes. A present tool failure fails signoff. An absent optional tool is explicitly `BLOCKED_TOOLING`; the regression count never treats it as a successful synthesis check.

To refresh the portable evidence after a passing signoff, run `python scripts/export_evidence.py --manifest build/runs/<run-id>/manifest.json`. The exporter verifies source and artifact hashes before updating the checked-in summary; raw logs remain under the ignored `build` directory.

## Interpretation

The checked generic netlist contains 22,382 leaf cells: 5,882 flip-flop-family cells and 16,500 combinational cells across eight reachable modules. Latches, residual memories/processes, blackboxes, and unresolved cell types each count zero. Native statistics and independently expanded netlist counts agree. The [checked-in signoff summary](assets/signoff_summary.json) identifies the tested source and includes the detailed cell-type histogram, scope, and artifact hashes.

Synthesis establishes that the RTL translates into a structurally checked generic netlist. The simulation regression provides functional evidence. Target FPGA/ASIC utilization, timing, area, and power require a selected device or cell library, clock/IO constraints, and an implementation flow. Post-synthesis equivalence is also outside the current checks.
