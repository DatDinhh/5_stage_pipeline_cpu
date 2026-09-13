# Supported MIPS-like ISA subset

This project implements the 32-bit MIPS-like instruction subset defined below. Instruction and data addresses are byte addresses, registers and arithmetic results are 32 bits, and arithmetic wraps modulo 2^32 without an overflow exception. Full MIPS compatibility is outside this subset.

## Common rules

- Register `r0` always reads as zero and discards writes.
- `PC` names the address of the instruction itself. The sequential successor is `PC + 4`.
- There is no architectural delay slot. A taken control transfer squashes all younger instructions.
- `JAL` writes `PC + 4` to `r31`.
- Jump targets are `{(PC + 4)[31:28], instr[25:0], 2'b00}`.
- Branch targets are `PC + 4 + sign_extend(imm16 << 2)`.
- Memory is byte-addressed and little-endian.
- `0x00000000` is the canonical NOP (`SLL r0,r0,0`).
- Any unsupported opcode, unsupported R-type function, or malformed custom `ERET` traps and has no register or memory side effect.

## Encodings and operands

| Instruction | Opcode | Function | Operands read | Result / behavior |
|---|---:|---:|---|---|
| ADD, ADDU | `0x00` | `0x20`, `0x21` | `rs`, `rt` | `rd = rs + rt` |
| SUB, SUBU | `0x00` | `0x22`, `0x23` | `rs`, `rt` | `rd = rs - rt` |
| AND, OR, XOR, NOR | `0x00` | `0x24`..`0x27` | `rs`, `rt` | bitwise result in `rd` |
| SLT | `0x00` | `0x2a` | `rs`, `rt` | signed comparison, result 0 or 1 |
| SLL, SRL, SRA | `0x00` | `0x00`, `0x02`, `0x03` | `rt` | shift by encoded `shamt` 0..31 |
| ADDI | `0x08` | - | `rs` | sign-extended immediate add to `rt` |
| ANDI, ORI, XORI | `0x0c`..`0x0e` | - | `rs` | zero-extended immediate operation |
| LUI | `0x0f` | - | none | `rt = imm16 << 16` |
| LB, LBU | `0x20`, `0x24` | - | `rs` | signed/unsigned byte load to `rt` |
| LH, LHU | `0x21`, `0x25` | - | `rs` | signed/unsigned halfword load to `rt` |
| LW | `0x23` | - | `rs` | word load to `rt` |
| SB, SH, SW | `0x28`, `0x29`, `0x2b` | - | `rs`, `rt` | byte/halfword/word store |
| BEQ, BNE | `0x04`, `0x05` | - | `rs`, `rt` | PC-relative conditional branch |
| J, JAL | `0x02`, `0x03` | - | none | absolute-region jump; JAL also links |
| ERET | `0x18` | - | none | encoding exactly `0x60000000`; resume at EPC |

Immediate arithmetic and address calculation use sign extension except for ANDI, ORI, XORI, and LUI. `SLT` and `SRA` interpret operands as signed two's-complement values; all other operations use the same raw 32-bit patterns regardless of signedness.

## Memory, CSR, alignment, and traps

- External data RAM occupies `0x00000000` through `0x00000fff` in the default core configuration.
- Performance CSRs occupy `0xfffff000` through `0xfffff0ff`; the implemented read-only word addresses are cycle low/high (`+0x00`, `+0x04`), successful instruction count (`+0x08`), stall cycles (`+0x0c`), redirect count (`+0x10`), EPC (`+0xf0`), and cause (`+0xf4`).
- CSR accesses are aligned `LW` reads only. Stores, subword reads, and unimplemented CSR offsets trap.
- Word accesses require `addr[1:0] == 0`; halfword accesses require `addr[0] == 0`; byte accesses have no alignment restriction.
- An illegal, misaligned, disallowed CSR, or unmapped data access writes the faulting instruction PC to EPC, writes the documented cause, redirects to `0x00000080`, has no GPR/store side effect, and is not counted by `instret`.
- `ERET` returns to EPC. Software must repair the cause if it intends to retry the faulting instruction.

Trap causes used by this project are: 1 misaligned load, 2 misaligned store, 3 unmapped load, 4 unmapped store, 5 CSR permission/address error, and 6 illegal instruction.

## Contract consistency

The RTL, assembler, ISS, and directed tests use this same architectural contract, including no-delay-slot control flow, `JAL = PC + 4`, strict halfword alignment, exact ERET encoding, and the trap cause assignments above. The [design decisions](design_decisions.md) describe pipeline and bus behavior that supports these semantics.
