# Validation Report

## Result

The CPU passes the regression, lint, and generic synthesis checks described below. The checked-in [signoff summary](assets/signoff_summary.json) records the results, tool versions, and source hashes.

| Item | Result |
|---|---|
| Evidence summary | [signoff_summary.json](assets/signoff_summary.json) |
| Manifest status | `pass` |
| Source identity | SHA-256 hashes for all verification inputs |
| Tools | Icarus Verilog/compiler and vvp runtime 11.0 stable (`d3b0992`); Python 3.12.6 |
| Unit gates | 5/5 pass; Python gate contains 89/89 unit/negative tests |
| Directed RTL | 23/23 configurations across 17 assembly test IDs |
| Full-core warm reset | 1/1 protocol test; nine reset scenarios, 45 exact-checked restart events |
| Reset coverage negative | 1/1 expected failure; omitted required phase correctly rejected |
| Constrained-random RTL | 128/128: master seeds 0..31 across fast/dslow/islow/stress |
| Assembly-driven positive RTL total | 151/151, 34,284 simulated post-reset cycles; warm-reset cycles are separate |
| Mutations | 2/2 detected by the architectural checker |
| Total runner results | 160/160 pass; tooling checks are reported separately |
| Coverage gates | all 20 declared core aggregate bins and all 17 required reset bins hit |
| Mutation isolation | original-tree re-hash `pass` |
| Source integrity | all 54 inputs re-hashed at runner completion; exporter verifies source and artifact hashes before publication |
| Verilator lint | PASS via Ubuntu/WSL, Verilator 5.020; default fatal warnings, no warning suppression |
| Yosys synthesis | PASS via Ubuntu/WSL, Yosys 0.33; hierarchy/check assertions and validated generic netlist/statistics |

Reproduce from the project root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_tests.ps1 -Suite signoff
```

Each invocation writes a manifest and complete artifacts under `build/runs/<run-id>/`. Generated build artifacts are excluded from Git; the portable signoff summary retains the evidence needed to identify the tested source and inspect its results.

After a passing signoff, refresh the checked-in evidence with:

```powershell
python scripts/export_evidence.py --manifest build/runs/<run-id>/manifest.json
```

The exporter verifies source and artifact hashes and writes a portable summary without machine paths or calendar timestamps. Raw logs remain in the ignored build directory.

## Test method

The runner performs warning-gated fresh compiles in unique directories, executes online protocol/safety checking, requires exact completion and drain, and independently compares every assembly-driven trace event and the complete architectural state with the ISS. Each assembly run retains source, full program/data images, expected and actual trace/state, coverage, and simulation log; fresh compile logs are under the run's `compiled` directory. The warm-reset protocol test uses explicit HDL expectations across reset epochs with the same online monitors and scoreboard. Preexisting executables cannot satisfy a new run.

## Functional scope exercised

- Entire documented ALU/decode subset, immediate extension, wraparound, shifts 0/31, NOP, and write-to-r0 suppression.
- EX/MEM and MEM/WB forwarding, simultaneous youngest-writer matches on both sources, WB-to-ID bypass, held-consumer operand capture, load-use blocking, store address/data dependencies, and JAL link.
- LW/SW, four byte lanes, two halfword lanes, signed/unsigned extension, neighboring-byte preservation, mixed accesses, aligned bus payload/strobes, and load-to-r0.
- BEQ/BNE taken and not taken, forward/backward branches, J/JAL, redirect recovery, false/wrong speculation discard, and wrong-path side-effect suppression with prediction enabled.
- Misaligned load/store, illegal instruction, invalid CSR write, unmapped load, EPC/cause, trap vector, handler repair, exact-EPC ERET retry, CSR reads, and same-edge `instret` visibility.
- Request/response ownership, long/random waits, held responses, continuous valid, same-edge turnover, exact-once store creation, range fatal, and warm reset during a pending memory request.
- Full-core warm reset at nine instruction/load/store transaction boundaries; canceled ownership, zeroed GPRs, persistent RAM, no late retirement/store, reset-vector fetch, and exact recovery execution.
- Adversarial instruction/data request stalls, held memory responses, lane preservation, wrong-path stores, and a redirect while an instruction request is actually blocked.

## Measured aggregate coverage

| Bin | Count | Bin | Count |
|---|---:|---|---:|
| `fwd_ex_a` | 894 | `fwd_ex_b` | 328 |
| `fwd_wb_a` | 170 | `fwd_wb_b` | 235 |
| `fwd_double_a` | 144 | `fwd_double_b` | 82 |
| `fwd_blocked` | 605 | `load_hazard` | 147 |
| `wb_id_rs` | 253 | `wb_id_rt` | 161 |
| `exmem_wait` | 9,462 | `ibus_resp_hold` | 5,221 |
| `branch_taken` | 521 | `branch_not_taken` | 136 |
| `redirect` | 538 | `mispredict` | 532 |
| `fetch_discard` | 554 | `trap` | 6 |
| `store_retire` | 1,138 | `load_retire` | 1,230 |
| `write_r0` | 371 | `redirect_ibus_blocked` | 1 |
| `ibus_backpressure` | 326 | `dbus_backpressure` | 108 |
| `ibus_mem_resp_hold` | 5,351 | `dbus_mem_resp_hold` | 324 |
| `dbus_resp_hold` | 0 | | |

The three adversarial assembly tests contribute 326/108 blocked instruction/data request cycles, 253/324 held instruction/data responses at the memory-facing interface, and one `redirect_ibus_blocked` event. The instruction memory-facing aggregate also includes 5,098 natural holds from the other profiles, totaling 5,351. Core-facing and memory-facing response counters are distinct. `dbus_resp_hold` remains zero because this core remains ready for an owned data response; it is reported as an observation rather than a required gate. The shim stalls the memory-facing handshake without forcing any CPU outputs. Once exposed, a response stays valid and stable until accepted. Both shim owners must drain before completion.

## Full-core warm-reset evidence

`protocol/core_warm_reset/sim.log` records all nine scenarios. For each of instruction fetch, load, and store, reset is asserted with (1) a request blocked, (2) a request accepted while the slave waits to create its response, or (3) a response offered before its consuming edge. Every reset follows real earlier instruction retirement. Each phase counter is exactly one; each of the eight recovery counters is nine: ownership clear, GPRs clear, no ghost retirement, no ghost store, memory persistence, reset-vector fetch, exact restart completion, and prior warm activity.

The test checks all pipeline/bus/scoreboard owners, all 32 registers, and all 1,024 RAM words. After reset it keeps fetch disabled longer than the canceled memory latency to expose late effects, then runs five exact-checked instructions from PC zero and drains. The response-offered store scenario preserves the store already made visible by `rv_mem`; a pending store whose response was not created is canceled. Memory contents are never reinitialized across the warm-reset boundary.

This exercises a shared reset of CPU and slaves. An untagged slave that continues returning old responses after a CPU-only reset is outside this contract. The runner retains the protocol artifact separately from the ISS-based assembly traces, without weakening the architectural checker.

## Negative and mutation evidence

Python negative tests check rejection of wrong retirement data, next PC, load response, store address/data, missing/extra/duplicate records, non-monotonic cycle, duplicate UID, unknown/truncated fields, incorrect completion, and incomplete or corrupted final GPR/RAM state.

Signoff also runs `core_warm_reset_missing_phase` with `+OMIT_SCENARIO=0`. It must exit nonzero for the named missing-scenario coverage error, emit no success sentinel, leave `reset_ibus_request_pending=0`, and hit every other required reset bin. A crash, an unrelated failure, a missing artifact, or a zero exit cannot qualify. Its coverage is excluded from positive aggregates. Runner negatives additionally reject missing/zero protocol evidence, malformed negative contracts, failed or duplicate random results, and changed/added/removed source inputs.

The forwarding-priority mutation completed normally but failed comparison at event 2: expected `r2=0x00000006`, actual `0x00000004`. The byte-lane mutation completed normally but failed at event 3: expected `0xffffffff`, actual `0x0000007f`. Both are classified `TRACE_MISMATCH`; compile errors, runtime crashes, and missing artifacts are not counted as kills.

## Synthesis evidence

- Verilator 5.020 lint passed on the eight `rtl/core/*.v` sources with `cpu_core` as top and its default warning-fatal policy. The flow runs lint only. See `toolchain/verilator/run.log` within the generated run directory.
- Yosys 0.33 (`2584903a060`) synthesized the same eight RTL inputs and passed `hierarchy -check`, `synth -top cpu_core`, and `check -assert`. The run includes `netlist.json`, `synthesized.v`, `statistics.json`, `netlist_summary.json`, the generated script, logs, and recorded artifact hashes.
- The independent netlist inspector checks the complete 54-port/846-bit top interface and all reachable module instances. It rejects malformed or unrelated JSON, invalid interfaces, recursive or unresolved hierarchy, blackboxes, residual processes/memories, and latch-family cells. Its expanded primitive count matches native Yosys statistics. All generated artifact and script hashes were verified after signoff.

| Generic synthesized resource | Count |
|---|---:|
| Reachable modules, including top | 8 |
| Generic leaf cells | 22,382 |
| Flip-flop-family cells | 5,882 |
| Combinational cells | 16,500 |
| Latches / unresolved cells / blackboxes | 0 / 0 / 0 |
| Residual processes / memories | 0 / 0 |

These counts describe the default `cpu_core`, including trace/debug and live verification control ports, register file, BPU, and CSRs. External instruction/data RAM is excluded; `DMEM_BYTES=4096` is an address-range parameter. They are generic gate/flip-flop counts, not FPGA LUT utilization or ASIC area. The report retains nine warning occurrences across five unique messages, all reporting BPU/register-file array-to-register mapping; no diagnostic was hidden.

The runner executes Yosys in a fresh output directory and uses a fixed relative statistics filename to support Yosys 0.33's `tee -o` handling, including project paths with spaces. See [synthesis.md](synthesis.md) for reproduction and scope.

## Showcase evidence

[showcase.md](showcase.md) contains the pipeline diagram, a reproducible demonstration, and three case studies. `python scripts/build_showcase.py` performs fresh selected compilations and simulations, keeps the normal ISS/full-state checks, and generates two measured VCD comparisons against the existing isolated mutations.

The showcase suite passes three positive configurations and detects two injected faults, with all source inputs re-hashed. [Showcase metadata](assets/showcase_evidence.json) retains the source and mutation hashes, exact measured samples, trace/ISS rows, artifact hashes, and provenance. The [forwarding](assets/forwarding_waveform.svg) and [byte-lane](assets/byte_lane_waveform.svg) diagrams compare controlled fault injections with the corrected implementation using measured VCD samples.

## Limitations and not-run work

- No static timing, FPGA execution, formal proof, code/branch/toggle coverage, or power analysis was run.
- No target FPGA or ASIC library was selected, and no placement/routing or post-synthesis equivalence proof was performed.
- `.github/workflows/ci.yml` is a checked-in CI configuration, not evidence of a remote CI pass.
- The test-only completion hooks are outside the architectural ISA. There is no HALT instruction.
- Random generation is constrained and bounded; 32 seeds do not cover the complete state space.

## Source integrity

The manifest records matching `input_hashes` and `final_input_hashes` for all 54 RTL, simulation, Python verification, runner/showcase, program, and testplan inputs. `source_input_rehash.status` and `mutation_original_tree_rehash` both pass, with no changed, added, or removed inputs. Report Markdown and generated documentation assets are outside the simulation-input hash set.
