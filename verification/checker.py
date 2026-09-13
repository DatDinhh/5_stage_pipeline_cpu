"""Strict offline checker for retirement CSV and final architectural state.

The parser is deliberately intolerant of unknown values, malformed/truncated
records, and bookkeeping gaps.  The comparator advances an independent ISS and
reports the first architectural divergence.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, fields
from io import StringIO, TextIOBase
from pathlib import Path
from typing import Iterable, Sequence

from .iss import Event, ISS, MASK32


TRACE_FIELDS = (
    "epoch", "event_order", "retire_order", "cycle", "uid", "trap",
    "pc", "instr", "next_pc", "rd_we", "rd", "rd_data",
    "mem_valid", "mem_write", "mem_addr", "mem_size", "store_data",
    "bus_wdata", "wstrb", "load_raw", "load_value", "cause", "epc",
)

_HEX32_FIELDS = frozenset({
    "pc", "instr", "next_pc", "rd_data", "mem_addr", "store_data",
    "bus_wdata", "load_raw", "load_value", "cause", "epc",
})
_BOOL_FIELDS = frozenset({"trap", "rd_we", "mem_valid", "mem_write"})
_ARCH_FIELDS = (
    "epoch", "event_order", "retire_order", "trap",
    "pc", "instr", "next_pc", "rd_we", "rd", "rd_data",
    "mem_valid", "mem_write", "mem_addr", "mem_size", "store_data",
    "bus_wdata", "wstrb", "load_raw", "load_value", "cause", "epc",
)


class VerificationError(AssertionError):
    """Base class with a stable category used by negative tests and runners."""

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        super().__init__(f"{category}: {message}")


class TraceFormatError(VerificationError):
    def __init__(self, message: str) -> None:
        super().__init__("TRACE_FORMAT", message)


class TraceIntegrityError(VerificationError):
    def __init__(self, message: str) -> None:
        super().__init__("TRACE_INTEGRITY", message)


class TraceMismatch(VerificationError):
    def __init__(self, message: str) -> None:
        super().__init__("TRACE_MISMATCH", message)


class StateFormatError(VerificationError):
    def __init__(self, message: str) -> None:
        super().__init__("STATE_FORMAT", message)


class StateMismatch(VerificationError):
    def __init__(self, message: str) -> None:
        super().__init__("STATE_MISMATCH", message)


class CompletionError(VerificationError):
    def __init__(self, message: str) -> None:
        super().__init__("COMPLETION", message)


@dataclass(frozen=True)
class TraceRecord:
    epoch: int
    event_order: int
    retire_order: int
    cycle: int
    uid: int
    trap: int
    pc: int
    instr: int
    next_pc: int
    rd_we: int
    rd: int
    rd_data: int
    mem_valid: int
    mem_write: int
    mem_addr: int
    mem_size: int
    store_data: int
    bus_wdata: int
    wstrb: int
    load_raw: int
    load_value: int
    cause: int
    epc: int

    @classmethod
    def from_event(cls, event: Event, *, cycle: int | None = None,
                   uid: int | None = None) -> "TraceRecord":
        values = {item.name: getattr(event, item.name) for item in fields(cls)}
        if cycle is not None:
            values["cycle"] = cycle
        if uid is not None:
            values["uid"] = uid
        return cls(**values)


@dataclass(frozen=True)
class StateDump:
    registers: dict[int, int]
    memory: dict[int, int]
    event_count: int
    completion_seen: int
    cycle_count: int


@dataclass(frozen=True)
class VerificationResult:
    event_count: int
    retire_count: int
    final_pc: int
    cycle_count: int


def _open_text(source: str | Path | TextIOBase) -> tuple[TextIOBase, bool]:
    if hasattr(source, "read"):
        return source, False  # type: ignore[return-value]
    return Path(source).open("r", encoding="utf-8", newline=""), True


def _reject_unknown(text: str, field: str, line_number: int) -> None:
    candidate = text.lower()
    if candidate.startswith("0x"):
        candidate = candidate[2:]
    if not candidate or any(char in candidate for char in ("x", "z", "?")):
        raise TraceFormatError(
            f"line {line_number}: field {field} contains an empty/unknown value {text!r}")


def _decimal(text: str, field: str, line_number: int) -> int:
    _reject_unknown(text, field, line_number)
    try:
        value = int(text, 10)
    except ValueError as exc:
        raise TraceFormatError(
            f"line {line_number}: field {field} is not decimal: {text!r}") from exc
    if value < 0:
        raise TraceFormatError(f"line {line_number}: field {field} is negative")
    return value


def _hex(text: str, field: str, line_number: int) -> int:
    _reject_unknown(text, field, line_number)
    normalized = text[2:] if text.lower().startswith("0x") else text
    try:
        value = int(normalized, 16)
    except ValueError as exc:
        raise TraceFormatError(
            f"line {line_number}: field {field} is not hexadecimal: {text!r}") from exc
    if not 0 <= value <= MASK32:
        raise TraceFormatError(f"line {line_number}: field {field} exceeds 32 bits")
    return value


def _state_decimal(text: str, field: str, line_number: int) -> int:
    try:
        return _decimal(text, field, line_number)
    except TraceFormatError as exc:
        raise StateFormatError(str(exc).split(": ", 1)[-1]) from exc


def _state_hex(text: str, field: str, line_number: int) -> int:
    try:
        return _hex(text, field, line_number)
    except TraceFormatError as exc:
        raise StateFormatError(str(exc).split(": ", 1)[-1]) from exc


def _record_from_row(row: dict[str, str | None], line_number: int) -> TraceRecord:
    if None in row or any(value is None for value in row.values()):
        raise TraceFormatError(f"line {line_number}: wrong number of CSV columns")
    parsed: dict[str, int] = {}
    for name in TRACE_FIELDS:
        token = row[name].strip()  # type: ignore[union-attr]
        if name in _HEX32_FIELDS or name == "wstrb":
            parsed[name] = _hex(token, name, line_number)
        else:
            parsed[name] = _decimal(token, name, line_number)

    for name in _BOOL_FIELDS:
        if parsed[name] not in (0, 1):
            raise TraceFormatError(f"line {line_number}: {name} must be 0 or 1")
    if parsed["rd"] > 31:
        raise TraceFormatError(f"line {line_number}: rd must be 0..31")
    if parsed["mem_size"] not in (0, 1, 2, 4):
        raise TraceFormatError(f"line {line_number}: mem_size must be 0, 1, 2, or 4")
    if parsed["wstrb"] > 0xF:
        raise TraceFormatError(f"line {line_number}: wstrb exceeds four lanes")
    for name in ("epoch",):
        if parsed[name] > MASK32:
            raise TraceFormatError(f"line {line_number}: {name} exceeds 32 bits")
    for name in ("event_order", "retire_order", "cycle", "uid"):
        if parsed[name] >= (1 << 64):
            raise TraceFormatError(f"line {line_number}: {name} exceeds 64 bits")

    if not parsed["mem_valid"]:
        if parsed["mem_write"] or parsed["mem_size"] or parsed["wstrb"]:
            raise TraceFormatError(
                f"line {line_number}: inactive memory event has active control fields")
    elif parsed["mem_size"] not in (1, 2, 4):
        raise TraceFormatError(f"line {line_number}: active memory event has size zero")
    if parsed["mem_write"] and parsed["wstrb"] == 0:
        raise TraceFormatError(f"line {line_number}: store has zero strobe")
    if parsed["mem_valid"] and not parsed["mem_write"] and parsed["wstrb"]:
        raise TraceFormatError(f"line {line_number}: load has nonzero strobe")
    if parsed["trap"] and (parsed["rd_we"] or parsed["mem_valid"]):
        raise TraceFormatError(f"line {line_number}: trap has an architectural side effect")
    return TraceRecord(**parsed)


def parse_trace(source: str | Path | TextIOBase) -> list[TraceRecord]:
    stream, close = _open_text(source)
    try:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise TraceFormatError("trace is empty or missing its header")
        header = tuple(item.strip() for item in reader.fieldnames)
        if header != TRACE_FIELDS:
            raise TraceFormatError(
                f"header mismatch: expected {','.join(TRACE_FIELDS)}, got {','.join(header)}")
        records = [_record_from_row(row, line_number)
                   for line_number, row in enumerate(reader, 2)]
    finally:
        if close:
            stream.close()
    validate_trace_integrity(records)
    return records


def parse_trace_text(text: str) -> list[TraceRecord]:
    return parse_trace(StringIO(text))


def validate_trace_integrity(records: Sequence[TraceRecord]) -> None:
    expected_event = 0
    expected_retire = 0
    current_epoch = records[0].epoch if records else 0
    seen_uids: set[tuple[int, int]] = set()
    prior_cycle = -1
    for index, record in enumerate(records):
        if record.epoch != current_epoch:
            if record.epoch != current_epoch + 1:
                raise TraceIntegrityError(
                    f"event {index}: epoch jumps {current_epoch}->{record.epoch}")
            current_epoch = record.epoch
            expected_event = 0
            expected_retire = 0
            prior_cycle = -1
        if record.event_order != expected_event:
            raise TraceIntegrityError(
                f"event {index}: event_order={record.event_order}, expected {expected_event}")
        if record.retire_order != expected_retire:
            raise TraceIntegrityError(
                f"event {index}: retire_order={record.retire_order}, expected {expected_retire}")
        if record.cycle <= prior_cycle:
            raise TraceIntegrityError(
                f"event {index}: cycle {record.cycle} is not strictly increasing")
        uid_key = (record.epoch, record.uid)
        if uid_key in seen_uids:
            raise TraceIntegrityError(f"event {index}: duplicate dynamic uid {record.uid}")
        seen_uids.add(uid_key)
        expected_event += 1
        if not record.trap:
            expected_retire += 1
        prior_cycle = record.cycle


def compare_trace(iss: ISS, records: Sequence[TraceRecord], *,
                  expected_event_count: int | None = None,
                  completion_pc: int | None = None) -> list[Event]:
    """Advance ``iss`` independently and compare every architectural field."""

    validate_trace_integrity(records)
    if expected_event_count is not None and len(records) != expected_event_count:
        relation = "missing" if len(records) < expected_event_count else "extra"
        raise CompletionError(
            f"{relation} events: trace has {len(records)}, expected {expected_event_count}")

    expected_events: list[Event] = []
    for index, actual in enumerate(records):
        if actual.epoch != iss.state.epoch:
            if index > 0 and actual.epoch == iss.state.epoch + 1:
                iss.reset()
            else:
                raise TraceMismatch(
                    f"event {index}: epoch expected/actual {iss.state.epoch} vs "
                    f"{actual.epoch}")
        expected = iss.step()
        expected_events.append(expected)
        for name in _ARCH_FIELDS:
            got, want = getattr(actual, name), getattr(expected, name)
            if got != want:
                formatting = (f"0x{want:08x} vs 0x{got:08x}"
                              if name in _HEX32_FIELDS else f"{want} vs {got}")
                raise TraceMismatch(
                    f"event {index} pc=0x{expected.pc:08x}: {name} expected/actual "
                    f"{formatting}")
    if completion_pc is not None:
        if not records:
            raise CompletionError("completion PC requested but trace has no events")
        if records[-1].pc != (completion_pc & MASK32):
            raise CompletionError(
                f"last PC is 0x{records[-1].pc:08x}, expected completion "
                f"0x{completion_pc & MASK32:08x}")
    return expected_events


def parse_state(source: str | Path | TextIOBase) -> StateDump:
    stream, close = _open_text(source)
    registers: dict[int, int] = {}
    memory: dict[int, int] = {}
    end_values: tuple[int, int, int] | None = None
    try:
        for line_number, raw in enumerate(stream, 1):
            line = raw.strip()
            if not line:
                continue
            if end_values is not None:
                raise StateFormatError(f"line {line_number}: content follows END marker")
            parts = [item.strip() for item in line.split(",")]
            kind = parts[0]
            if kind == "REG":
                if len(parts) != 3:
                    raise StateFormatError(f"line {line_number}: malformed REG record")
                index = _state_decimal(parts[1], "register index", line_number)
                value = _state_hex(parts[2], "register value", line_number)
                if index > 31 or index in registers:
                    raise StateFormatError(
                        f"line {line_number}: invalid/duplicate register {index}")
                registers[index] = value
            elif kind == "MEM":
                if len(parts) != 3:
                    raise StateFormatError(f"line {line_number}: malformed MEM record")
                address = _state_hex(parts[1], "memory address", line_number)
                value = _state_hex(parts[2], "memory value", line_number)
                if address & 3 or address in memory:
                    raise StateFormatError(
                        f"line {line_number}: unaligned/duplicate memory address 0x{address:08x}")
                memory[address] = value
            elif kind == "END":
                if len(parts) != 4:
                    raise StateFormatError(f"line {line_number}: malformed END record")
                labels = ("event_count", "completion_seen", "cycle_count")
                end_values = tuple(
                    _state_decimal(parts[i], labels[i - 1], line_number)
                    for i in range(1, 4))  # type: ignore[assignment]
            else:
                raise StateFormatError(f"line {line_number}: unknown state record {kind!r}")
    finally:
        if close:
            stream.close()
    if end_values is None:
        raise StateFormatError("missing END marker (state dump may be truncated)")
    return StateDump(registers, memory, *end_values)


def parse_state_text(text: str) -> StateDump:
    return parse_state(StringIO(text))


def compare_state(iss: ISS, dump: StateDump, *, require_full_ram: bool = True) -> None:
    expected_registers = {index: value & MASK32
                          for index, value in enumerate(iss.state.gpr)}
    if set(dump.registers) != set(expected_registers):
        missing = sorted(set(expected_registers) - set(dump.registers))
        extra = sorted(set(dump.registers) - set(expected_registers))
        raise StateFormatError(f"register dump not complete; missing={missing}, extra={extra}")
    for index, expected in expected_registers.items():
        actual = dump.registers[index]
        if actual != expected:
            raise StateMismatch(
                f"r{index}: expected 0x{expected:08x}, actual 0x{actual:08x}")

    expected_memory = iss.memory.dump_words()
    if require_full_ram and set(dump.memory) != set(expected_memory):
        missing = sorted(set(expected_memory) - set(dump.memory))
        extra = sorted(set(dump.memory) - set(expected_memory))
        show_missing = [f"0x{x:08x}" for x in missing[:8]]
        show_extra = [f"0x{x:08x}" for x in extra[:8]]
        raise StateFormatError(
            f"RAM dump not complete; missing={show_missing}, extra={show_extra}")
    for address in sorted(set(dump.memory) & set(expected_memory)):
        actual, expected = dump.memory[address], expected_memory[address]
        if actual != expected:
            raise StateMismatch(
                f"MEM[0x{address:08x}]: expected 0x{expected:08x}, "
                f"actual 0x{actual:08x}")


def verify_run(iss: ISS, trace_source: str | Path | TextIOBase,
               state_source: str | Path | TextIOBase, *, expected_event_count: int,
               completion_pc: int | None = None,
               require_full_ram: bool = True) -> VerificationResult:
    records = parse_trace(trace_source)
    compare_trace(iss, records, expected_event_count=expected_event_count,
                  completion_pc=completion_pc)
    dump = parse_state(state_source)
    if dump.event_count != len(records):
        raise CompletionError(
            f"END event_count={dump.event_count}, trace rows={len(records)}")
    if dump.completion_seen != 1:
        raise CompletionError(f"completion_seen={dump.completion_seen}, expected 1")
    compare_state(iss, dump, require_full_ram=require_full_ram)
    return VerificationResult(
        event_count=len(records), retire_count=iss.state.retire_count,
        final_pc=iss.state.pc, cycle_count=dump.cycle_count)


def trace_csv(records: Iterable[TraceRecord]) -> str:
    """Serialize records in exactly the format emitted by ``tb_core``."""

    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(TRACE_FIELDS)
    for record in records:
        row: list[str] = []
        for name in TRACE_FIELDS:
            value = getattr(record, name)
            if name in _HEX32_FIELDS:
                row.append(f"{value & MASK32:08x}")
            elif name == "wstrb":
                row.append(f"{value:x}")
            else:
                row.append(str(value))
        writer.writerow(row)
    return output.getvalue()


def state_text(iss: ISS, *, event_count: int | None = None,
               completion_seen: int = 1, cycle_count: int = 0) -> str:
    """Create the canonical full-RAM state format for tests/expected artifacts."""

    count = iss.state.event_count if event_count is None else event_count
    lines = [f"REG,{index},{value & MASK32:08x}"
             for index, value in enumerate(iss.state.gpr)]
    lines.extend(f"MEM,{address:08x},{value:08x}"
                 for address, value in iss.memory.dump_words().items())
    lines.append(f"END,{count},{completion_seen},{cycle_count}")
    return "\n".join(lines) + "\n"


__all__ = [
    "TRACE_FIELDS", "TraceRecord", "StateDump", "VerificationResult",
    "VerificationError", "TraceFormatError", "TraceIntegrityError",
    "TraceMismatch", "StateFormatError", "StateMismatch", "CompletionError",
    "parse_trace", "parse_trace_text", "validate_trace_integrity",
    "compare_trace", "parse_state", "parse_state_text", "compare_state",
    "verify_run", "trace_csv", "state_text",
]
