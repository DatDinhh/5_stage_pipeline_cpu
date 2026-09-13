# Microarchitecture

The [architecture guide](architecture.md) illustrates the system boundary, pipeline/control paths, and verification flow. This document defines the corresponding stage ownership and transfer rules.

## Scope

The implementation is a 32-bit, single-issue, in-order IF/ID/EX/MEM/WB pipeline for the MIPS-like subset in [isa_subset.md](isa_subset.md). It has no architectural delay slot. Reset PC is `0x00000000`, the trap vector is `0x00000080`, JAL links `PC + 4`, and data memory is byte-addressed little-endian. Instruction and data ports are custom ready/valid interfaces, not AXI; each has at most one accepted transaction outstanding.

## Stage ownership and transfer

Each boundary owns a `valid` bit and the complete payload for one dynamic instruction. Payload is meaningful only while valid. A held stage keeps valid and payload stable. A stage that fires without replacement becomes invalid; a fire with same-edge replacement atomically refills it.

| Owner | Retained payload | Fire or consume condition |
|---|---|---|
| IF response buffer | instruction, actual PC, prediction taken/target, UID | IF/ID has space and no redirect resolves |
| IF/ID | instruction, PC, prediction metadata, UID | ID/EX has space, no load-use interlock, no redirect |
| ID/EX | decoded controls, sources/use bits, immediate, identity, prediction | EX/MEM can accept and the youngest matching producer is ready |
| EX/MEM | identity, result/destination, complete memory or trap metadata | trap, CSR read, non-memory completion, or owned data response |
| MEM/WB | complete retirement/trap record | drains on the following clock |

```text
exmem_fire       = EX_MEM_valid and exmem_complete
exmem_can_accept = not EX_MEM_valid or exmem_fire
idex_fire        = ID_EX_valid and exmem_can_accept and not forward_blocked
```

This consume/refill discipline prevents replay and repeated retirement. MEM/WB is unbackpressured. EX/MEM blocks younger work while an external transaction is incomplete.

## Fetch ownership and redirects

The front end has presented-request (`if_req_*`), accepted-request (`if_outstanding_*`), and returned-response (`if_buf_*`) ownership records. Presented address, prediction, stale flag, and UID remain stable under backpressure. Responses use the metadata captured with the accepted request.

The memory model may consume an old response and accept the next request on one edge. The front end then atomically replaces outstanding metadata; if the slave does not accept the offered turnover request, the core latches it and holds it stable.

A redirect clears younger buffered and IF/ID state, marks presented or accepted requests stale, drains and discards stale responses, and launches the target only after stale ownership is resolved. A response coincident with a redirect is discarded. Prediction metadata follows its instruction into ID/EX. Actual-next comparison for every instruction also recovers false BTB hits on non-branches. The BPU resets to weak-not-taken with invalid BTB entries, uses full-PC tags, and trains only when a branch fires.

## Dependencies and forwarding

Decode reports true `usesRs` and `usesRt`: SB/SH/SW use both base and data, shifts use only `rt`, and LUI/J/JAL/ERET use no GPR source. A valid, nonzero EX/MEM producer wins over MEM/WB. An unready youngest match blocks instead of falling back to an older value. Only ready ALU, JAL, CSR, and completed-load values forward; a load address is never treated as load data.

Explicit MEM/WB-to-ID bypass removes dependence on register-file scheduling. If a consumer is held behind a downstream memory operation, any transient forwarded operand is captured into ID/EX so it cannot disappear when the producer leaves WB. Load-use handling advances the load, holds IF/ID, bubbles ID/EX, and then waits for arbitrary response latency.

## Execute, control flow, and traps

Control flow resolves only from ID/EX on `idex_fire`. Priority is trap vector, ERET EPC, J/JAL target, branch result, then `PC + 4`. A redirect preserves the resolver and older work while killing younger state.

Illegal encoding, word/halfword misalignment, unmapped RAM, and invalid CSR access create precise trap events: EPC is the faulting PC, cause is defined by the ISA document, GPR and memory effects are suppressed, and control redirects to `0x80`. Only exact word `0x60000000` is ERET.

## Blocking data-memory ownership

EX/MEM retains instruction identity, full effective address, destination, kind, size, signedness, store source data, aligned bus data/strobes, and acceptance state. Bus payload is driven only from this record. Request acceptance prevents reissue, and the record cannot be replaced until its owned response fires. Loads select and extend lanes using retained metadata; stores retire after response consumption. Valid CSR word loads complete internally and never enter external RAM; invalid CSR operations trap.

`rv_mem.v` and `rv_rom.v` each have one response slot. A response stays valid and stable while held. Zero inserted waits arm a registered response on request acceptance; nonzero waits use a width-safe counter. A consumed response may be replaced by a new request on that same edge. Store strobes are applied exactly once when the response is created, and memory contents survive a warm protocol reset while pending protocol ownership is aborted.

## Retirement, trace, counters, and completion

GPR write enable is valid-qualified, non-trapping, and suppresses `r0`. Every valid MEM/WB record creates one registered identity/next-PC/GPR/memory/trap event. Event order includes traps; retirement order and `instret` count only successful instructions. Discarded-fetch UID gaps are allowed, while committed UIDs strictly increase. The `instret` CSR includes an older instruction retiring on the same edge as the CSR read.

`core_idle` requires every fetch and pipeline owner to be empty. The testbench supplies a completion PC; a speculative response cannot stop fetching while an older unresolved IF/ID or fetch-buffer instruction could squash it. After the completion instruction retires, the test-only `verification_halt` input discards only younger speculative work. PASS still requires the ISS-derived event count, completion event, drained core and scoreboard, zero online errors, and matching full architectural state.

## Simultaneous-event priority

| Coincident events | Outcome |
|---|---|
| Reset and activity | reset wins; protocol, pipeline, predictor, GPRs, and counters clear; initialized ROM/RAM contents persist |
| EX/MEM completion plus ID/EX fire | older record enters MEM/WB; ID/EX refills EX/MEM |
| MEM/WB retirement plus EX/MEM completion | old MEM/WB commits/traces; completion refills MEM/WB |
| Load advance plus dependent IF/ID | load advances; IF/ID holds; ID/EX bubbles |
| Redirect plus younger refill | redirect wins; younger valids clear |
| Redirect plus instruction response | response is discarded/staled; target wins |
| Old instruction response plus next request | response uses old metadata; accepted next request becomes owner |
| Trap plus decoded transfer | trap wins and all faulting/younger effects are suppressed |
| Verification halt plus younger pipeline state | already-retired completion remains; younger state is cleared and stale fetch response is drained |

These rules describe the implemented RTL. Verification evidence is recorded in [verification_plan.md](verification_plan.md) and [validation_report.md](validation_report.md).
