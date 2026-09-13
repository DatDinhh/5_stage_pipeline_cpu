"""Independent architectural reference model for the documented ISA subset.

The ISS is intentionally instruction-accurate rather than cycle-accurate.  It
owns its program, PC, GPRs, CSRs, and sparse byte memory.  An RTL trace never
chooses the model's control-flow path or expected operands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence


MASK32 = 0xFFFF_FFFF
TRAP_VECTOR = 0x0000_0080
DEFAULT_RAM_BASE = 0x0000_0000
DEFAULT_RAM_SIZE = 4096
CSR_BASE = 0xFFFF_F000
CSR_PAGE_MASK = 0xFFFF_FF00
CSR_CYCLE_LO = CSR_BASE + 0x00
CSR_CYCLE_HI = CSR_BASE + 0x04
CSR_INSTRET = CSR_BASE + 0x08
CSR_STALL = CSR_BASE + 0x0C
CSR_REDIRECT = CSR_BASE + 0x10
CSR_EPC = CSR_BASE + 0xF0
CSR_CAUSE = CSR_BASE + 0xF4
VALID_CSRS = frozenset({
    CSR_CYCLE_LO, CSR_CYCLE_HI, CSR_INSTRET, CSR_STALL,
    CSR_REDIRECT, CSR_EPC, CSR_CAUSE,
})

CAUSE_MISALIGNED_LOAD = 1
CAUSE_MISALIGNED_STORE = 2
CAUSE_UNMAPPED_LOAD = 3
CAUSE_UNMAPPED_STORE = 4
CAUSE_CSR_ERROR = 5
CAUSE_ILLEGAL_INSTRUCTION = 6


class ISSExecutionError(RuntimeError):
    """The reference model cannot continue under the supplied test contract."""


class CSRValueUnavailable(ISSExecutionError):
    """A timing-dependent CSR was read without an independent timing provider."""


def u32(value: int) -> int:
    return value & MASK32


def s32(value: int) -> int:
    value &= MASK32
    return value - (1 << 32) if value & 0x8000_0000 else value


def sext16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x1_0000 if value & 0x8000 else value


@dataclass
class SparseMemory:
    """Bounded little-endian RAM storing only bytes that differ from zero."""

    base: int = DEFAULT_RAM_BASE
    size: int = DEFAULT_RAM_SIZE
    _bytes: dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.base < 0 or self.base > MASK32:
            raise ValueError("RAM base must be a 32-bit unsigned address")
        if self.size <= 0 or self.base + self.size > (1 << 32):
            raise ValueError("RAM range must be non-empty and fit the address space")

    def contains(self, address: int, size: int = 1) -> bool:
        return (size > 0 and self.base <= address
                and address + size <= self.base + self.size)

    def load_u(self, address: int, size: int) -> int:
        if not self.contains(address, size):
            raise ISSExecutionError(
                f"memory read 0x{address:08x}+{size} is outside RAM")
        value = 0
        for lane in range(size):
            value |= self._bytes.get(address + lane, 0) << (8 * lane)
        return value

    def store(self, address: int, size: int, value: int) -> None:
        if not self.contains(address, size):
            raise ISSExecutionError(
                f"memory write 0x{address:08x}+{size} is outside RAM")
        for lane in range(size):
            byte = (value >> (8 * lane)) & 0xFF
            byte_address = address + lane
            if byte:
                self._bytes[byte_address] = byte
            else:
                self._bytes.pop(byte_address, None)

    def load_s(self, address: int, size: int) -> int:
        value = self.load_u(address, size)
        sign_bit = 1 << (size * 8 - 1)
        if value & sign_bit:
            value -= 1 << (size * 8)
        return u32(value)

    def initialize_bytes(self, values: Mapping[int, int]) -> None:
        for address, value in values.items():
            if not 0 <= value <= 0xFF:
                raise ValueError(f"initial byte at 0x{address:08x} is not 8-bit")
            if not self.contains(address):
                raise ValueError(f"initial byte address 0x{address:08x} is outside RAM")
            if value:
                self._bytes[address] = value
            else:
                self._bytes.pop(address, None)

    def initialize_words(self, values: Mapping[int, int]) -> None:
        for address, value in values.items():
            if address & 3:
                raise ValueError(f"initial word address 0x{address:08x} is unaligned")
            self.store(address, 4, u32(value))

    def dump_words(self) -> dict[int, int]:
        return {address: self.load_u(address, 4)
                for address in range(self.base, self.base + self.size, 4)}

    def copy(self) -> "SparseMemory":
        return SparseMemory(self.base, self.size, dict(self._bytes))


@dataclass(frozen=True)
class Event:
    """One successful retirement or one precise trap event."""

    epoch: int
    event_order: int
    retire_order: int
    cycle: int
    uid: int
    trap: int
    pc: int
    instr: int
    next_pc: int
    rd_we: int = 0
    rd: int = 0
    rd_data: int = 0
    mem_valid: int = 0
    mem_write: int = 0
    mem_addr: int = 0
    mem_size: int = 0
    store_data: int = 0
    bus_wdata: int = 0
    wstrb: int = 0
    load_raw: int = 0
    load_value: int = 0
    cause: int = 0
    epc: int = 0


@dataclass
class ISSState:
    pc: int = 0
    gpr: list[int] = field(default_factory=lambda: [0] * 32)
    epc: int = 0
    cause: int = 0
    epoch: int = 0
    event_count: int = 0
    retire_count: int = 0


CSRReader = Callable[[int, ISSState], int]


class ISS:
    """Instruction-accurate reference model.

    ``program`` may be a byte-addressed mapping, a sequence beginning at
    ``entry``, or an encoder ``ProgramImage`` (recognized by its ``words``
    attribute without importing encoder constants). ``initial_memory`` contains
    little-endian words keyed by byte address.
    """

    def __init__(
        self,
        program: Mapping[int, int] | Sequence[int] | object,
        *,
        entry: int = 0,
        ram_base: int = DEFAULT_RAM_BASE,
        ram_size: int = DEFAULT_RAM_SIZE,
        initial_memory: Mapping[int, int] | None = None,
        initial_bytes: Mapping[int, int] | None = None,
        csr_reader: CSRReader | None = None,
    ) -> None:
        if hasattr(program, "words"):
            program = getattr(program, "words")
        if isinstance(program, Mapping):
            self.program = {u32(int(addr)): u32(int(word))
                            for addr, word in program.items()}
        elif isinstance(program, Sequence) and not isinstance(program, (str, bytes, bytearray)):
            self.program = {u32(entry + 4 * index): u32(int(word))
                            for index, word in enumerate(program)}
        else:
            raise TypeError("program must be an address mapping, word sequence, or ProgramImage")
        for address in self.program:
            if address & 3:
                raise ValueError(f"instruction address 0x{address:08x} is unaligned")
        self.entry = u32(entry)
        if self.entry & 3:
            raise ValueError("entry must be word aligned")
        self.memory = SparseMemory(ram_base, ram_size)
        if initial_memory:
            self.memory.initialize_words(initial_memory)
        if initial_bytes:
            self.memory.initialize_bytes(initial_bytes)
        self.state = ISSState(pc=self.entry)
        self.csr_reader = csr_reader

    def reset(self) -> None:
        """Reset architectural core state while retaining external RAM contents."""

        self.state = ISSState(pc=self.entry, epoch=self.state.epoch + 1)

    def set_reg(self, register: int, value: int) -> None:
        if not 0 <= register < 32:
            raise ValueError("register index must be 0..31")
        if register:
            self.state.gpr[register] = u32(value)
        self.state.gpr[0] = 0

    def _instruction(self, pc: int) -> int:
        # Simulation ROM is zero-filled; gaps therefore contain canonical NOPs.
        return self.program.get(pc, 0)

    def _base_event(self, pc: int, instr: int, next_pc: int, **updates: int) -> Event:
        values = dict(
            epoch=self.state.epoch,
            event_order=self.state.event_count,
            retire_order=self.state.retire_count,
            cycle=0,
            uid=self.state.event_count,
            trap=0,
            pc=u32(pc),
            instr=u32(instr),
            next_pc=u32(next_pc),
        )
        values.update(updates)
        return Event(**values)

    def _trap(self, pc: int, instr: int, cause: int) -> Event:
        event = self._base_event(
            pc, instr, TRAP_VECTOR, trap=1, cause=cause, epc=u32(pc))
        self.state.epc = u32(pc)
        self.state.cause = cause
        self.state.pc = TRAP_VECTOR
        self.state.event_count += 1
        self.state.gpr[0] = 0
        return event

    def _retire(self, event: Event) -> Event:
        if event.rd_we:
            self.state.gpr[event.rd] = u32(event.rd_data)
        self.state.gpr[0] = 0
        self.state.pc = u32(event.next_pc)
        self.state.event_count += 1
        self.state.retire_count += 1
        return event

    def _write_event(self, pc: int, instr: int, next_pc: int,
                     rd: int, value: int, **updates: int) -> Event:
        # rd_we describes an architectural state update. Instructions targeting
        # r0 still retire and remain visible through pc/instr, but do not write.
        if rd == 0:
            return self._base_event(pc, instr, next_pc, rd_we=0, rd=0,
                                    rd_data=0, **updates)
        return self._base_event(pc, instr, next_pc, rd_we=1, rd=rd,
                                rd_data=u32(value), **updates)

    def _read_csr(self, address: int) -> int:
        if address == CSR_INSTRET:
            return u32(self.state.retire_count)
        if address == CSR_EPC:
            return self.state.epc
        if address == CSR_CAUSE:
            return self.state.cause
        if self.csr_reader is None:
            raise CSRValueUnavailable(
                f"CSR 0x{address:08x} requires an independent timing provider")
        return u32(self.csr_reader(address, self.state))

    @staticmethod
    def _bus_store(address: int, size: int, value: int) -> tuple[int, int]:
        lane = address & 3
        if size == 1:
            return u32((value & 0xFF) << (8 * lane)), 1 << lane
        if size == 2:
            return u32((value & 0xFFFF) << (8 * lane)), 0x3 << lane
        return u32(value), 0xF

    def _memory_step(self, pc: int, instr: int, opcode: int,
                     rs: int, rt: int, immediate: int) -> Event:
        is_store = opcode in (0x28, 0x29, 0x2B)
        size = ({0x20: 1, 0x24: 1, 0x28: 1,
                 0x21: 2, 0x25: 2, 0x29: 2,
                 0x23: 4, 0x2B: 4})[opcode]
        address = u32(self.state.gpr[rs] + sext16(immediate))
        next_pc = u32(pc + 4)
        in_csr_page = (address & CSR_PAGE_MASK) == CSR_BASE

        if in_csr_page:
            if is_store or opcode != 0x23 or address not in VALID_CSRS:
                return self._trap(pc, instr, CAUSE_CSR_ERROR)
            value = self._read_csr(address)
            event = self._write_event(
                pc, instr, next_pc, rt, value,
                mem_valid=1, mem_write=0, mem_addr=address, mem_size=4,
                load_raw=value, load_value=value)
            return self._retire(event)

        if size > 1 and address & (size - 1):
            return self._trap(
                pc, instr,
                CAUSE_MISALIGNED_STORE if is_store else CAUSE_MISALIGNED_LOAD)
        if not self.memory.contains(address, size):
            return self._trap(
                pc, instr,
                CAUSE_UNMAPPED_STORE if is_store else CAUSE_UNMAPPED_LOAD)

        if is_store:
            source = self.state.gpr[rt]
            bus_wdata, wstrb = self._bus_store(address, size, source)
            self.memory.store(address, size, source)
            return self._retire(self._base_event(
                pc, instr, next_pc,
                mem_valid=1, mem_write=1, mem_addr=address, mem_size=size,
                store_data=u32(source), bus_wdata=bus_wdata, wstrb=wstrb))

        # The architectural trace records the full aligned 32-bit bus response
        # separately from the selected/extended load result.
        raw = self.memory.load_u(address & ~0x3, 4)
        lane_value = self.memory.load_u(address, size)
        signed = opcode in (0x20, 0x21)
        value = self.memory.load_s(address, size) if signed else lane_value
        return self._retire(self._write_event(
            pc, instr, next_pc, rt, value,
            mem_valid=1, mem_write=0, mem_addr=address, mem_size=size,
            load_raw=raw, load_value=u32(value)))

    def step(self) -> Event:
        """Execute one expected dynamic instruction or precise trap."""

        pc = self.state.pc
        instr = self._instruction(pc)
        opcode = (instr >> 26) & 0x3F
        rs = (instr >> 21) & 0x1F
        rt = (instr >> 16) & 0x1F
        rd = (instr >> 11) & 0x1F
        shamt = (instr >> 6) & 0x1F
        funct = instr & 0x3F
        immediate = instr & 0xFFFF
        next_pc = u32(pc + 4)
        a, b = self.state.gpr[rs], self.state.gpr[rt]

        if opcode == 0x00:
            if funct in (0x20, 0x21):
                value = u32(a + b)
            elif funct in (0x22, 0x23):
                value = u32(a - b)
            elif funct == 0x24:
                value = a & b
            elif funct == 0x25:
                value = a | b
            elif funct == 0x26:
                value = a ^ b
            elif funct == 0x27:
                value = u32(~(a | b))
            elif funct == 0x2A:
                value = int(s32(a) < s32(b))
            elif funct == 0x00:
                value = u32(b << shamt)
            elif funct == 0x02:
                value = b >> shamt
            elif funct == 0x03:
                value = u32(s32(b) >> shamt)
            else:
                return self._trap(pc, instr, CAUSE_ILLEGAL_INSTRUCTION)
            return self._retire(self._write_event(pc, instr, next_pc, rd, value))

        if opcode == 0x08:
            return self._retire(self._write_event(
                pc, instr, next_pc, rt, u32(a + sext16(immediate))))
        if opcode == 0x0C:
            return self._retire(self._write_event(pc, instr, next_pc, rt, a & immediate))
        if opcode == 0x0D:
            return self._retire(self._write_event(pc, instr, next_pc, rt, a | immediate))
        if opcode == 0x0E:
            return self._retire(self._write_event(pc, instr, next_pc, rt, a ^ immediate))
        if opcode == 0x0F:
            return self._retire(self._write_event(pc, instr, next_pc, rt, immediate << 16))

        if opcode in (0x20, 0x21, 0x23, 0x24, 0x25, 0x28, 0x29, 0x2B):
            return self._memory_step(pc, instr, opcode, rs, rt, immediate)

        if opcode in (0x04, 0x05):
            equal = a == b
            taken = equal if opcode == 0x04 else not equal
            target = u32(next_pc + (sext16(immediate) << 2)) if taken else next_pc
            return self._retire(self._base_event(pc, instr, target))

        if opcode in (0x02, 0x03):
            target = ((next_pc & 0xF000_0000) | ((instr & 0x03FF_FFFF) << 2))
            if opcode == 0x03:
                return self._retire(self._write_event(pc, instr, target, 31, next_pc))
            return self._retire(self._base_event(pc, instr, target))

        if instr == 0x6000_0000:
            return self._retire(self._base_event(pc, instr, self.state.epc))
        # The whole 0x18 major opcode was incidental in the old decoder.  Only
        # the exact documented word is ERET.
        return self._trap(pc, instr, CAUSE_ILLEGAL_INSTRUCTION)

    def run(self, count: int) -> list[Event]:
        if count < 0:
            raise ValueError("count must be non-negative")
        return [self.step() for _ in range(count)]

    def run_until_pc(self, completion_pc: int, *, max_events: int,
                     occurrence: int = 1) -> list[Event]:
        """Run through the requested dynamic occurrence of ``completion_pc``.

        Completion is based on the independently predicted event PC, making the
        resulting event count suitable for the testbench's external completion
        protocol without inventing a HALT instruction.
        """

        if max_events <= 0:
            raise ValueError("max_events must be positive")
        if occurrence <= 0:
            raise ValueError("occurrence must be positive")
        target = u32(completion_pc)
        events: list[Event] = []
        hits = 0
        for _ in range(max_events):
            event = self.step()
            events.append(event)
            if event.pc == target:
                hits += 1
                if hits == occurrence:
                    return events
        raise ISSExecutionError(
            f"completion PC 0x{target:08x} occurrence {occurrence} was not "
            f"reached within {max_events} events")


__all__ = [
    "ISS", "ISSState", "Event", "SparseMemory", "ISSExecutionError",
    "CSRValueUnavailable", "u32", "s32", "sext16", "TRAP_VECTOR",
    "DEFAULT_RAM_BASE", "DEFAULT_RAM_SIZE", "CSR_BASE", "CSR_CYCLE_LO",
    "CSR_CYCLE_HI", "CSR_INSTRET", "CSR_STALL", "CSR_REDIRECT", "CSR_EPC",
    "CSR_CAUSE", "CAUSE_MISALIGNED_LOAD", "CAUSE_MISALIGNED_STORE",
    "CAUSE_UNMAPPED_LOAD", "CAUSE_UNMAPPED_STORE", "CAUSE_CSR_ERROR",
    "CAUSE_ILLEGAL_INSTRUCTION",
]
