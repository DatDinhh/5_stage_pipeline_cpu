"""Encoders and a small two-pass assembler for the documented MIPS-like ISA.

The module uses only the Python standard library and rejects operands that do
not fit their architectural fields instead of silently truncating them.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Iterator, Mapping


MASK32 = 0xFFFF_FFFF

# Encoder constants are deliberately not imported by the ISS.  Hand-computed
# golden tests guard the boundary between the two independent implementations.
OP_R = 0x00
OP_J = 0x02
OP_JAL = 0x03
OP_BEQ = 0x04
OP_BNE = 0x05
OP_ADDI = 0x08
OP_ANDI = 0x0C
OP_ORI = 0x0D
OP_XORI = 0x0E
OP_LUI = 0x0F
OP_LB = 0x20
OP_LH = 0x21
OP_LW = 0x23
OP_LBU = 0x24
OP_LHU = 0x25
OP_SB = 0x28
OP_SH = 0x29
OP_SW = 0x2B

FN_SLL = 0x00
FN_SRL = 0x02
FN_SRA = 0x03
FN_ADD = 0x20
FN_ADDU = 0x21
FN_SUB = 0x22
FN_SUBU = 0x23
FN_AND = 0x24
FN_OR = 0x25
FN_XOR = 0x26
FN_NOR = 0x27
FN_SLT = 0x2A


class EncodingError(ValueError):
    """The source cannot be represented by the supported instruction subset."""


def _uint(name: str, value: int, bits: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise EncodingError(f"{name} must be an integer")
    if not 0 <= value < (1 << bits):
        raise EncodingError(f"{name}={value} does not fit unsigned {bits} bits")
    return value


def _reg(value: int) -> int:
    return _uint("register", value, 5)


def _signed_imm(value: int, bits: int = 16) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise EncodingError("immediate must be an integer")
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if not lo <= value <= hi:
        raise EncodingError(f"immediate={value} does not fit signed {bits} bits")
    return value & ((1 << bits) - 1)


def _unsigned_imm(value: int, bits: int = 16) -> int:
    return _uint("immediate", value, bits)


def encode_r(rs: int, rt: int, rd: int, shamt: int, funct: int) -> int:
    """Encode a raw R-format word after strict field validation."""

    return ((_reg(rs) << 21) | (_reg(rt) << 16) | (_reg(rd) << 11)
            | (_uint("shamt", shamt, 5) << 6) | _uint("funct", funct, 6))


def encode_i(opcode: int, rs: int, rt: int, immediate: int) -> int:
    """Encode a raw I-format word; ``immediate`` is an unsigned field value."""

    return ((_uint("opcode", opcode, 6) << 26) | (_reg(rs) << 21)
            | (_reg(rt) << 16) | _uint("immediate field", immediate, 16))


def encode_j(opcode: int, target: int, pc: int = 0) -> int:
    """Encode J/JAL from a byte target and the instruction's byte address."""

    _uint("pc", pc, 32)
    _uint("target", target, 32)
    if target & 3:
        raise EncodingError(f"jump target 0x{target:08x} is not word aligned")
    if ((pc + 4) & 0xF000_0000) != (target & 0xF000_0000):
        raise EncodingError(
            f"jump target 0x{target:08x} is outside the PC+4 region at 0x{pc:08x}")
    return (_uint("opcode", opcode, 6) << 26) | ((target >> 2) & 0x03FF_FFFF)


def _rr(funct: int, rd: int, rs: int, rt: int) -> int:
    return encode_r(rs, rt, rd, 0, funct)


def add(rd: int, rs: int, rt: int) -> int: return _rr(FN_ADD, rd, rs, rt)
def addu(rd: int, rs: int, rt: int) -> int: return _rr(FN_ADDU, rd, rs, rt)
def sub(rd: int, rs: int, rt: int) -> int: return _rr(FN_SUB, rd, rs, rt)
def subu(rd: int, rs: int, rt: int) -> int: return _rr(FN_SUBU, rd, rs, rt)
def and_(rd: int, rs: int, rt: int) -> int: return _rr(FN_AND, rd, rs, rt)
def or_(rd: int, rs: int, rt: int) -> int: return _rr(FN_OR, rd, rs, rt)
def xor_(rd: int, rs: int, rt: int) -> int: return _rr(FN_XOR, rd, rs, rt)
def nor(rd: int, rs: int, rt: int) -> int: return _rr(FN_NOR, rd, rs, rt)
def slt(rd: int, rs: int, rt: int) -> int: return _rr(FN_SLT, rd, rs, rt)
def sll(rd: int, rt: int, shamt: int) -> int: return encode_r(0, rt, rd, shamt, FN_SLL)
def srl(rd: int, rt: int, shamt: int) -> int: return encode_r(0, rt, rd, shamt, FN_SRL)
def sra(rd: int, rt: int, shamt: int) -> int: return encode_r(0, rt, rd, shamt, FN_SRA)


def _i_signed(opcode: int, rt: int, rs: int, immediate: int) -> int:
    return encode_i(opcode, rs, rt, _signed_imm(immediate))


def _i_unsigned(opcode: int, rt: int, rs: int, immediate: int) -> int:
    return encode_i(opcode, rs, rt, _unsigned_imm(immediate))


def addi(rt: int, rs: int, immediate: int) -> int: return _i_signed(OP_ADDI, rt, rs, immediate)
def andi(rt: int, rs: int, immediate: int) -> int: return _i_unsigned(OP_ANDI, rt, rs, immediate)
def ori(rt: int, rs: int, immediate: int) -> int: return _i_unsigned(OP_ORI, rt, rs, immediate)
def xori(rt: int, rs: int, immediate: int) -> int: return _i_unsigned(OP_XORI, rt, rs, immediate)
def lui(rt: int, immediate: int) -> int: return _i_unsigned(OP_LUI, rt, 0, immediate)
def lb(rt: int, offset: int, base: int) -> int: return _i_signed(OP_LB, rt, base, offset)
def lbu(rt: int, offset: int, base: int) -> int: return _i_signed(OP_LBU, rt, base, offset)
def lh(rt: int, offset: int, base: int) -> int: return _i_signed(OP_LH, rt, base, offset)
def lhu(rt: int, offset: int, base: int) -> int: return _i_signed(OP_LHU, rt, base, offset)
def lw(rt: int, offset: int, base: int) -> int: return _i_signed(OP_LW, rt, base, offset)
def sb(rt: int, offset: int, base: int) -> int: return _i_signed(OP_SB, rt, base, offset)
def sh(rt: int, offset: int, base: int) -> int: return _i_signed(OP_SH, rt, base, offset)
def sw(rt: int, offset: int, base: int) -> int: return _i_signed(OP_SW, rt, base, offset)
def beq(rs: int, rt: int, word_offset: int) -> int: return encode_i(OP_BEQ, rs, rt, _signed_imm(word_offset))
def bne(rs: int, rt: int, word_offset: int) -> int: return encode_i(OP_BNE, rs, rt, _signed_imm(word_offset))
def j(target: int, pc: int = 0) -> int: return encode_j(OP_J, target, pc)
def jal(target: int, pc: int = 0) -> int: return encode_j(OP_JAL, target, pc)
def eret() -> int: return 0x6000_0000
def nop() -> int: return 0


@dataclass(frozen=True)
class ProgramImage:
    """Sparse byte-addressed instruction words returned by :func:`assemble`."""

    words: Mapping[int, int]
    labels: Mapping[str, int]
    entry: int = 0

    def dense_words(self, *, start: int | None = None,
                    end: int | None = None) -> list[int]:
        if not self.words:
            return []
        first = min(self.words) if start is None else start
        last_exclusive = max(self.words) + 4 if end is None else end
        if first & 3 or last_exclusive & 3 or last_exclusive < first:
            raise EncodingError("dense image bounds must be ordered and word aligned")
        return [self.words.get(addr, 0) & MASK32
                for addr in range(first, last_exclusive, 4)]

    def hex_lines(self, *, start: int | None = None,
                  end: int | None = None) -> str:
        return "".join(f"{word:08x}\n" for word in self.dense_words(start=start, end=end))


@dataclass(frozen=True)
class _Statement:
    address: int
    mnemonic: str
    operands: tuple[str, ...]
    line_number: int


_LABEL_RE = re.compile(r"^([A-Za-z_.$][\w.$]*):")
_MEM_RE = re.compile(r"^(.+)\(([^()]+)\)$")
_REG_ALIASES = {"zero": 0, "$zero": 0, "ra": 31, "$ra": 31}


def _source_lines(source: str | Iterable[str]) -> Iterator[tuple[int, str]]:
    lines = source.splitlines() if isinstance(source, str) else source
    for line_number, raw in enumerate(lines, 1):
        line = re.split(r"#|;|//", raw, maxsplit=1)[0].strip()
        if line:
            yield line_number, line


def _integer(token: str) -> int:
    text = token.strip().replace("_", "")
    try:
        return int(text, 0)
    except ValueError as exc:
        raise EncodingError(f"invalid integer {token!r}") from exc


def _register(token: str) -> int:
    text = token.strip().lower()
    if text in _REG_ALIASES:
        return _REG_ALIASES[text]
    match = re.fullmatch(r"\$?r?(\d+)", text)
    if not match:
        raise EncodingError(f"invalid register {token!r}")
    return _reg(int(match.group(1), 10))


def _operands(text: str) -> tuple[str, ...]:
    return () if not text.strip() else tuple(part.strip() for part in text.split(","))


def _expect(items: tuple[str, ...], count: int, mnemonic: str) -> None:
    if len(items) != count:
        raise EncodingError(f"{mnemonic} expects {count} operands, got {len(items)}")


def _target(token: str, labels: Mapping[str, int]) -> int:
    return labels[token] if token in labels else _integer(token)


def _memory_operand(token: str) -> tuple[int, int]:
    match = _MEM_RE.fullmatch(token.replace(" ", ""))
    if not match:
        raise EncodingError(f"invalid memory operand {token!r}; expected offset(base)")
    return _integer(match.group(1)), _register(match.group(2))


def _encode_statement(stmt: _Statement, labels: Mapping[str, int]) -> int:
    m, a = stmt.mnemonic, stmt.operands
    if m == ".word":
        _expect(a, 1, m)
        return _uint(".word value", _target(a[0], labels), 32)
    if m == "nop":
        _expect(a, 0, m)
        return nop()
    if m == "eret":
        _expect(a, 0, m)
        return eret()

    rr = {"add": add, "addu": addu, "sub": sub, "subu": subu,
          "and": and_, "or": or_, "xor": xor_, "nor": nor, "slt": slt}
    if m in rr:
        _expect(a, 3, m)
        return rr[m](_register(a[0]), _register(a[1]), _register(a[2]))

    shifts = {"sll": sll, "srl": srl, "sra": sra}
    if m in shifts:
        _expect(a, 3, m)
        return shifts[m](_register(a[0]), _register(a[1]), _integer(a[2]))

    immediates = {"addi": addi, "andi": andi, "ori": ori, "xori": xori}
    if m in immediates:
        _expect(a, 3, m)
        return immediates[m](_register(a[0]), _register(a[1]), _integer(a[2]))
    if m == "lui":
        _expect(a, 2, m)
        return lui(_register(a[0]), _integer(a[1]))

    memory = {"lb": lb, "lbu": lbu, "lh": lh, "lhu": lhu, "lw": lw,
              "sb": sb, "sh": sh, "sw": sw}
    if m in memory:
        _expect(a, 2, m)
        offset, base = _memory_operand(a[1])
        return memory[m](_register(a[0]), offset, base)

    if m in {"beq", "bne"}:
        _expect(a, 3, m)
        if a[2] in labels:
            delta = labels[a[2]] - (stmt.address + 4)
            if delta & 3:
                raise EncodingError(f"branch target {a[2]!r} is not word aligned")
            offset = delta // 4
        else:
            offset = _integer(a[2])
        return (beq if m == "beq" else bne)(
            _register(a[0]), _register(a[1]), offset)

    if m in {"j", "jal"}:
        _expect(a, 1, m)
        return (j if m == "j" else jal)(_target(a[0], labels), stmt.address)
    raise EncodingError(f"unsupported mnemonic {m!r}")


def assemble(source: str | Iterable[str], *, base_address: int = 0) -> ProgramImage:
    """Assemble the subset plus labels, ``.org``, and ``.word``.

    Numeric branch operands are signed word offsets; labels are made relative to
    PC+4. Numeric jump operands are absolute byte targets.
    """

    _uint("base_address", base_address, 32)
    if base_address & 3:
        raise EncodingError("base_address must be word aligned")
    pc = base_address
    labels: dict[str, int] = {}
    statements: list[_Statement] = []

    for line_number, line in _source_lines(source):
        while True:
            match = _LABEL_RE.match(line)
            if not match:
                break
            name = match.group(1)
            if name in labels:
                raise EncodingError(f"line {line_number}: duplicate label {name!r}")
            labels[name] = pc
            line = line[match.end():].strip()
            if not line:
                break
        if not line:
            continue
        pieces = line.split(None, 1)
        mnemonic = pieces[0].lower()
        operand_text = pieces[1] if len(pieces) == 2 else ""
        if mnemonic == ".org":
            try:
                new_pc = _integer(operand_text)
            except EncodingError as exc:
                raise EncodingError(f"line {line_number}: {exc}") from exc
            _uint(".org address", new_pc, 32)
            if new_pc & 3:
                raise EncodingError(f"line {line_number}: .org must be word aligned")
            pc = new_pc
            continue
        statements.append(_Statement(pc, mnemonic, _operands(operand_text), line_number))
        if pc > MASK32 - 4:
            raise EncodingError(f"line {line_number}: program counter overflows")
        pc += 4

    words: dict[int, int] = {}
    for stmt in statements:
        if stmt.address in words:
            raise EncodingError(
                f"line {stmt.line_number}: address 0x{stmt.address:08x} already occupied")
        try:
            words[stmt.address] = _encode_statement(stmt, labels) & MASK32
        except EncodingError as exc:
            raise EncodingError(f"line {stmt.line_number}: {exc}") from exc
    return ProgramImage(words=words, labels=labels, entry=base_address)


__all__ = [
    "EncodingError", "ProgramImage", "assemble", "encode_r", "encode_i", "encode_j",
    "add", "addu", "sub", "subu", "and_", "or_", "xor_", "nor", "slt",
    "sll", "srl", "sra", "addi", "andi", "ori", "xori", "lui",
    "lb", "lbu", "lh", "lhu", "lw", "sb", "sh", "sw",
    "beq", "bne", "j", "jal", "eret", "nop",
]
