# Bug Ledger

This ledger connects each design defect or verification gap to its cause, correction, and regression coverage. `verified_signoff` means the correction passes the declared simulation, lint, and generic synthesis checks. The [validation report](validation_report.md) and [signoff summary](assets/signoff_summary.json) identify the tested source and measured results.

## CPU-01 — replay from invalid consume/hold behavior

- **Root cause:** IF/ID retained old valid state after consumption and ID/EX could accept the same dynamic instruction again; downstream side effects were not consistently tied to a single valid/fire event.
- **Reproduction:** repeated register writes and duplicate stores appear when a consumed pipeline record remains valid. A self-increment chain exposes replay even when repeated constant writes hide it.
- **Repair:** `cpu_core.v` gives each boundary explicit valid ownership and consume/refill behavior. MEM/WB creates one event and one side-effect opportunity per valid record.
- **Regression:** `pipe_compat_original`, `pipe_replay_self_increment`, event/UID/order monitors, ISS trace count and full state. Replay test retires exactly six events, leaves `r4=3`, `MEM[0]=3`, and records one store.
- **Status:** `verified_signoff`.

## CPU-02 — older forwarding writer selected

- **Root cause:** independent forwarding conditions allowed MEM/WB to overwrite a matching, younger EX/MEM selection. Valid, r0, result readiness, load, and JAL cases were incomplete.
- **Repair:** `forwarding_unit.v` gives a valid nonzero EX/MEM match priority. An unready youngest producer blocks instead of falling back. Core forwarding exposes only ready ALU/JAL/CSR/completed-load values, adds WB-to-ID bypass, and captures a transient forwarded operand when a consumer is held behind a memory wait.
- **Regression:** forwarding and hazard unit tables; `raw_forwarding_priority`, `raw_load_store`, `control_jump_jal`; nonzero EX/WB/double-match/blocked bins. The isolated `wb_over_ex_forwarding` mutation is caught by the ISS at event 2 (expected 6, actual 4).
- **Status:** `verified_signoff`.

## CPU-03 — scoreboard timing, offset, and independence defects

- **Root cause:** the legacy scoreboard sampled newly scheduled MEM/WB state on the response edge, discarded the effective-address lane offset, had competing procedural state owners, and mirrored DUT-derived values rather than proving architectural computation.
- **Repair:** `scoreboard.v` uses a single state owner to correlate requests, responses, and retirements with retained full address, kind, data, strobes, and response. Registered retirement sampling removes the NBA race. `checker.py` and an independent ISS verify address, raw and extended load data, store source/aligned payload, next PC, and final full RAM/GPR state.
- **Regression:** checker negative mutations, `raw_load_store`, `memory_lanes`, memory protocol unit test, and every signoff RTL run. CPU-03 is explicitly linked in `testplan.json`.
- **Status:** `verified_signoff`.

## CPU-04 — prediction metadata belonged to another instruction

- **Root cause:** EX branch resolution compared against IF/ID prediction metadata, so age and identity were inconsistent.
- **Repair:** PC, instruction, prediction and UID travel together. Redirect is produced only on `idex_fire`; stale fetch ownership is drained; actual next PC is checked for every instruction; BPU state resets deterministically and BTB tags use the full PC.
- **Regression:** `control_branch_paths`, `control_jump_jal`, `trap_illegal_wrong_path`, predictor-on stress/random profiles, redirect/mispredict/fetch-discard coverage, and wrong-path state comparison.
- **Status:** `verified_signoff`.

## CPU-05 — memory request/completion lost ownership

- **Root cause:** EX/MEM could change while a request waited or a response remained outstanding, and the old outstanding record lacked destination, size, signedness, offset, store completion, and instruction identity.
- **Repair:** blocking EX/MEM retains immutable transaction and instruction metadata plus `mem_accepted` until the owned response fires. Requests cannot reissue; load extension and store retirement use the retained record. RAM/ROM each enforce one-slot capacity and stable response ownership.
- **Regression:** compatibility, raw load/store, memory lanes, trap/recovery, deterministic D-slow and stress runs, online transaction scoreboard, full ISS state, and memory-model unit/range tests.
- **Status:** `verified_signoff`.

## CPU-06 — multiple sequential owners of fetch state

- **Root cause:** `if_buf_valid` and related fetch state were written from multiple clocked processes, leaving response/consume/redirect/reset priority ambiguous.
- **Repair:** one fetch sequential owner orders launch, acceptance, response, same-edge turnover, stale marking, discard, redirect, halt, and reset for request/outstanding/buffer/PC/UID state.
- **Regression:** memory same-edge turnover unit test; `control_branch_paths` fast/stress; all four random timing profiles; active held-payload/order monitors. CPU-06 is explicitly linked in `testplan.json`.
- **Status:** `verified_signoff`.

## CPU-07 — timeout was reported as completion

- **Root cause:** the old top testbench waited a fixed time and called `$finish`; completion, exact event count, final state, active monitors, and drained ownership were not required.
- **Repair:** `tb_core.sv` requires an ISS-derived event count, completion PC, `core_idle`, scoreboard idle, and zero online errors. It emits complete trace/state/coverage artifacts. Extra events and timeout call `$fatal`. The runner additionally requires strict trace/ISS/full-state comparison and coverage gates; non-signoff suites are not labeled signoff, and partial seed/test selections are rejected for signoff.
- **Regression:** Python completion/missing/extra/truncation negatives, compatibility and replay cases, every runner phase, and the final 32-by-4 matrix. CPU-07 is explicitly linked in `testplan.json`.
- **Status:** `verified_signoff`.

## VRF-01 — speculative completion response stopped fetch before squash

- **Discovery:** the first repaired completion hook stopped when it saw the configured PC on an instruction response, even if an older unresolved jump later squashed that response.
- **Reproduction:** `control_branch_paths` timed out at 12/13 events with `completion_seen=1` and an idle core after a speculative fetch reached the completion address.
- **Repair:** the response hook refuses unsafe speculative completion behind unresolved buffered/IF-ID control, memory, or illegal work. Retirement of the completion instruction asserts a test-only halt that removes younger speculative work without changing the ISA.
- **Regression:** `control_branch_paths` fast/stress and final directed/random signoff.
- **Status:** `verified_signoff`.

## RTL-08 — forwarded operand disappeared during downstream hold

- **Discovery:** `raw_load_store` initially loaded from address `0x44` instead of `0x48` after a dependent base update was held behind a store.
- **Root cause:** ID/EX latched an old register-file operand; its WB forwarding opportunity disappeared before the held consumer finally fired.
- **Repair:** a held ID/EX record captures any ready EX/MEM or MEM/WB forwarded operand.
- **Regression:** `raw_load_store` fast and five-wait D-slow plus random wait profiles.
- **Status:** `verified_signoff`.

## RTL-09 — same-edge `instret` CSR was one instruction stale

- **Discovery:** the ISS expected 4 while RTL returned 3 for the CSR load at PC `0x10`.
- **Root cause:** combinational CSR read observed the old counter before the older MEM/WB retirement NBA update.
- **Repair:** `CSR_INSTRET` read data includes the valid older `retire_event` on that edge.
- **Regression:** `csr_read` and full signoff.
- **Status:** `verified_signoff`.

## RTL-10 — inactive store-data lanes leaked source bits

- **Discovery:** random seed 1 produced SB bus data `0xffffffbd`; the canonical bus payload was `0x000000bd` with strobe `0001`.
- **Root cause:** the full 32-bit source was shifted without masking it to byte/halfword width.
- **Repair:** SB and SH zero-mask to 8 and 16 bits before lane alignment.
- **Regression:** `memory_lanes`, random seeds 0..31 across four profiles, and the `wrong_byte_lane` mutation.
- **Status:** `verified_signoff`.

## VRF-02 — full-core warm-reset transaction phases lacked executable evidence

- **Gap:** memory-model reset tests did not establish full-core behavior when an instruction, load, or store transaction was interrupted at each ownership phase.
- **Closure:** `tb_core_reset.sv` performs nine warm resets after real architectural activity: request pending, accepted request awaiting response, and response offered, for instruction fetch, load, and store. The CPU and memory slaves share the reset. It checks cleared core/slave/scoreboard ownership, zero architectural registers, no ghost retirement or store, preserved RAM and store effects already made visible before reset, restart at PC zero, and exactly five checked restart events per scenario. Accepted stores still awaiting response creation are canceled.
- **Regression:** `core_warm_reset` passes all 17 required bins. Each of the nine phase bins is 1; ownership, register clearing, no-ghost retirement/store, memory persistence, restart vector/completion, and prior warm activity are each 9. `core_warm_reset_missing_phase` omits the pending instruction-request phase and is accepted only after the intended fatal coverage failure: that bin is 0, every other required bin is positive, and the positive completion marker is absent.
- **Status:** `verified_signoff` for the nine scenarios under the shared-reset bus contract.

## VRF-03 — request backpressure and held responses lacked full-core coverage gates

- **Gap:** the original memory timing profiles delayed responses but did not force both request interfaces to remain backpressured. Held-response and redirect/backpressure claims needed observed events with payload/ownership checks.
- **Closure:** `rv_bus_shim.sv` and the `adversarial` profile hold instruction/data requests and memory-side responses. Three ISS-checked full-core runs exercise dependent load/store traffic, byte/halfword lanes, and wrong-path control flow; their required bins must be nonzero. Payload stability, transaction ownership, architectural retirement, and complete final GPR/RAM state remain checked.
- **Regression:** the final three adversarial runs produce the following observed counts; these are cycle/event counters, not percentages.

| Test | I request backpressure | D request backpressure | I memory response held | D memory response held | Redirect while I request blocked |
| --- | ---: | ---: | ---: | ---: | ---: |
| `bus_load_store` | 83 | 28 | 57 | 84 | 0 |
| `bus_memory_lanes` | 113 | 56 | 142 | 168 | 0 |
| `bus_control_redirect` | 130 | 24 | 54 | 72 | 1 |

- **Boundary:** held-response evidence is measured at the memory-to-shim boundary. Core-side `dbus_resp_hold` remains 0 because this blocking core accepts its owned data response immediately. The signoff aggregate records `ibus_backpressure=326`, `dbus_backpressure=108`, `ibus_mem_resp_hold=5351`, `dbus_mem_resp_hold=324`, and `redirect_ibus_blocked=1`.
- **Status:** `verified_signoff` for the declared adversarial cases and required bins.

## RTL-11 — independent lint rejected implicit widths and omitted output pins

- **Discovery:** Verilator 5.020 initially rejected the core with eight default-fatal warnings: four BPU index truncations, one 33-bit data-memory limit comparison, and three omitted hazard-unit output connections.
- **Repair:** BPU modulo calculations use explicit 32-bit intermediates followed by sized index slices, retaining the modulo semantics. The end-of-access comparison explicitly extends `DMEM_BYTES` to 33 bits. The unused `pcWrite`, `IF_ID_Write`, and `controlBubble` outputs are explicitly left open at the hazard-unit instance.
- **Regression:** full Icarus signoff and Verilator 5.020 with `--lint-only --top-module cpu_core`, exit 0. Default fatal warnings remain enabled without suppression. The generated run retains diagnostics in `toolchain/verilator/run.log`.
- **Status:** `verified_signoff`.

## VRF-04 — fixed-matrix, protocol-evidence, and source-integrity guards

- **Gap:** protocol cases and independent tools require explicit failure handling, complete fixed-suite accounting, and proof that source inputs remain unchanged during verification. A set of random seed/profile pairs can hide duplicate runs, while malformed negative-test configuration can accept an unintended failure.
- **Closure:** the runner rejects nonpassing records, missing/duplicate random pairs, incomplete/duplicate directed or protocol cases, and incomplete protocol-negative results. Negative plan entries require valid unique identifiers, nonempty failure markers, valid plusargs, and an omitted bin from the required-bin set. Protocol runs require a fresh successful compile, successful simulation, their completion marker, and all required coverage; the negative case requires the specific expected failure and complete remaining coverage.
- **Integrity and tooling:** final input re-hashing detects changed, added, or removed sources/configuration. Tool discovery records paths, versions, commands, and logs; a present tool failure is fatal, a missing tool remains `BLOCKED_TOOLING`, and fresh toolchain output directories prevent stale netlists from counting as new synthesis evidence.
- **Regression:** Python unit/negative tests, the fixed 32-by-4 random matrix, 23 directed RTL runs, the reset positive/negative pair, and both ISS-killed mutations. The runner re-hashes every declared input before completing signoff; the evidence exporter independently verifies source and artifact hashes before publishing the portable summary.
- **Status:** `verified_signoff`.

## VRF-05 — synthesis evidence required structural validation and portable artifact output

- **Gap:** a successful synthesis process and a nonempty output file do not establish that the artifact represents the expected CPU or that its resource totals are internally consistent.
- **Closure:** the synthesis flow runs `hierarchy -check`, `synth -top cpu_core`, and `check -assert`, and emits JSON/Verilog netlists and machine-readable statistics. `verification/synthesis.py` rejects malformed or unrelated JSON, duplicate keys, a missing or incorrect 54-port CPU contract, zero reachable leaf cells, recursive or unresolved hierarchy, reachable blackboxes, unknown/unmapped primitives, latch-family cells, and residual memories/processes. It expands repeated module instances when counting resources. The runner requires the expanded leaf-cell count to equal `statistics.json`'s design count, requires a nonempty generated Verilog netlist, preserves source/artifact hashes, and refuses preexisting output directories so stale files cannot satisfy a new run.
- **Compatibility repair:** Yosys 0.33's `tee` command treats quotes in its output filename literally. The flow runs from its fresh synthesis output directory and uses `tee -o statistics.json stat -json`; source and netlist paths retain their required quoting. This flow also passes with an output directory containing spaces.
- **Regression and artifacts:** Yosys 0.33 (git `2584903a060`) synthesis and Python negatives for malformed/missing artifacts and inconsistent counts. The generated `toolchain/yosys` directory contains the synthesis script, logs, JSON netlist, `synthesized.v`, `statistics.json`, and `netlist_summary.json`, with recorded hashes. Nine warning occurrences, representing five unique messages, report conversion of `bht`, `btb_tag`, `btb_target`, `btb_valid`, and `rf` arrays into registers. The generic flow retains these diagnostics and maps the arrays to registers and logic.
- **Observed resources:** 22,382 generic leaf cells, comprising 5,882 sequential and 16,500 combinational cells, across eight reachable module definitions and eight module instances including the top. The validated result has zero latch cells, residual memories/processes, reachable blackboxes, and unresolved cells. The design retains trace/debug outputs and live `fetch_enable`/`verification_halt` inputs; external instruction/data memories are excluded. `DMEM_BYTES=4096` describes the external data-address boundary, not an included 4 KiB RAM.
- **Status:** `verified_signoff` for generic synthesis and structural artifact checks.

## Evidence boundary

The ledger covers finite functional regressions and structural synthesis checks. Synthesis includes verification instrumentation and uses generic mapped cells. Target FPGA/ASIC implementation, timing, formal equivalence, and physical area/power require separate qualification. Remote CI results are available only after the checked-in workflow executes on the hosting service.
