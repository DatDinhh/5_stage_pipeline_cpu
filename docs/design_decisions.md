# Design decisions

These architectural and interface choices define a consistent contract across the RTL, assembler, instruction-set simulator, and tests. The [bug ledger](bug_ledger.md) records implementation defects and their regression checks.

## Architectural contract

- A 32-bit, single-issue, in-order IF/ID/EX/MEM/WB pipeline uses reset PC `0x00000000`, active-low reset, trap vector `0x00000080`, performance CSR page `0xfffff000`, and little-endian byte lanes.
- Control flow has no architectural delay slot, and JAL links to `PC + 4`. RTL, encoder, ISS, and tests use the same rule.
- GPRs, predictor state, pipeline state, and performance counters clear on every asserted reset. Simulation RAM/ROM contents are initialized at time zero and retained across a warm protocol reset; an in-flight bus transaction is aborted.
- Illegal instructions, misalignment, unmapped data accesses, and invalid/write CSR accesses are precise traps. The faulting instruction emits a trace event with a trap field but does not increment successful `instret`.
- EPC contains the faulting instruction address. ERET returns to that exact address; handlers that retry must first repair the cause.

## Pipeline transfer contract

Each boundary owns a `valid` bit and its complete payload. A stage transfers only on its `fire` event. If a stage fires without a same-edge replacement it becomes invalid; otherwise the replacement atomically refills it. A held stage keeps both `valid` and every payload bit stable. Redirect kills only younger IF request/buffer and IF/ID state; the resolving ID/EX instruction and older stages remain valid.

The MEM stage is blocking and holds at most one external transaction. ALU/control instructions may complete immediately from EX/MEM into MEM/WB. A load or store remains in EX/MEM while its request is presented, after acceptance, and until its response is consumed. MEM/WB is an unbackpressured retirement boundary and therefore produces at most one architectural event per cycle.

Conflict priority is reset, then an older resolving trap/control transfer, then ordinary consume/refill. An ID/EX load may advance while a dependent IF/ID instruction is held and a bubble is inserted. A held branch cannot redirect or train the predictor repeatedly because resolution effects are gated by the ID/EX fire event.

## Dependency contract

Decode explicitly reports `uses_rs` and `uses_rt`. Store base and store data are separate true dependencies, including SB and SH. Immediate shifts read only `rt`; LUI, J, JAL, and ERET read no GPR source.

The youngest matching producer always wins. EX/MEM is considered before MEM/WB. If that younger producer is not ready, the consumer stalls instead of taking an older MEM/WB value. EX/MEM load addresses are never forwarded as load results, and JAL forwards its link value. MEM/WB-to-ID has an explicit bypass in addition to EX-stage forwarding.

## Fetch and prediction contract

The instruction channel supports one accepted outstanding request plus one response buffer. Request address and prediction metadata remain immutable while `valid && !ready`. A redirect cannot cancel an already-presented request: it marks it stale, accepts/drains its eventual response, and discards it. A stale response is never relabeled with the redirect target.

Prediction metadata is captured with the accepted fetch and follows that dynamic instruction into EX. Actual next PC is compared for every instruction, which recovers taken/not-taken, wrong-target, and false-BTB-hit cases. The BPU is retained and reset deterministically; BTB identity compares the full instruction PC rather than a partial tag. Predictor updates and redirects occur only when the resolving instruction fires.

## Bus contract

The instruction and data interfaces are custom ready/valid buses, not AXI. Each channel allows one outstanding in-order transaction. A request payload is stable until handshake. A response appears no earlier than the cycle after request acceptance, remains stable under response backpressure, and is consumed only when both valid and ready are high. "Zero extra wait" means this one-cycle minimum response, not a combinational response.

The data request record retains full effective address, aligned store payload/strobes, size, signedness, destination, PC, instruction, and dynamic identity until completion. A store modifies simulation memory exactly once when its response is produced and retires when that response is owned and consumed.

## Verification completion

No HALT opcode is added. The generic testbench receives an external completion PC. It may stop issuing after a non-speculative response, but it does not trust a response that an older unresolved instruction could still squash. When the completion instruction itself retires, a test-only `verification_halt` hook discards younger speculative work. PASS then requires the complete ISS-derived trace length, drained pipeline and bus state, zero online errors, and independent comparison of the retirement trace plus final GPR/RAM state. Timeout, missing completion, and extra retirement are failures.
