import unittest

from verification import encoding as enc
from verification.iss import (
    CAUSE_CSR_ERROR,
    CAUSE_ILLEGAL_INSTRUCTION,
    CAUSE_MISALIGNED_LOAD,
    CAUSE_MISALIGNED_STORE,
    CAUSE_UNMAPPED_LOAD,
    CAUSE_UNMAPPED_STORE,
    CSR_BASE,
    CSRValueUnavailable,
    ISS,
    ISSExecutionError,
    TRAP_VECTOR,
)


def run_one(word, *, registers=None, memory=None, pc=0, ram_size=4096):
    model = ISS({pc: word}, entry=pc, initial_memory=memory, ram_size=ram_size)
    for register, value in (registers or {}).items():
        model.set_reg(register, value)
    return model, model.step()


class CompatibilityProgramTests(unittest.TestCase):
    def test_original_nine_word_rom(self):
        words = [
            0x20010005, 0x2002000A, 0x00221820,
            0xAC030000, 0x8C040000, 0x20840001,
            0xAC040004, 0x8C050004, 0x10A0FFFB,
        ]
        model = ISS(words)
        events = model.run(9)
        self.assertEqual(model.state.gpr[1:6], [5, 10, 15, 16, 16])
        self.assertEqual(model.memory.load_u(0, 4), 15)
        self.assertEqual(model.memory.load_u(4, 4), 16)
        self.assertEqual(events[-1].pc, 0x20)
        self.assertEqual(events[-1].next_pc, 0x24)  # BEQ is not taken.
        self.assertEqual(model.state.retire_count, 9)
        self.assertEqual([event.event_order for event in events], list(range(9)))

    def test_run_until_external_completion_pc(self):
        model = ISS([enc.addi(1, 0, 1), enc.addi(1, 1, 1), enc.nop()])
        events = model.run_until_pc(8, max_events=10)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[-1].pc, 8)
        with self.assertRaisesRegex(ISSExecutionError, "was not reached"):
            ISS([enc.nop()]).run_until_pc(0x40, max_events=2)


class ArithmeticTests(unittest.TestCase):
    def test_wrap_signed_compare_and_logic(self):
        cases = [
            (enc.add(3, 1, 2), {1: 0xFFFF_FFFF, 2: 1}, 0x0000_0000),
            (enc.addu(3, 1, 2), {1: 0xFFFF_FFFF, 2: 2}, 0x0000_0001),
            (enc.sub(3, 1, 2), {1: 0, 2: 1}, 0xFFFF_FFFF),
            (enc.subu(3, 1, 2), {1: 1, 2: 2}, 0xFFFF_FFFF),
            (enc.and_(3, 1, 2), {1: 0xA5A5_F0F0, 2: 0x0FF0_FF00}, 0x05A0_F000),
            (enc.or_(3, 1, 2), {1: 0xA500_0000, 2: 0x005A_00FF}, 0xA55A_00FF),
            (enc.xor_(3, 1, 2), {1: 0xFFFF_0000, 2: 0x0F0F_0F0F}, 0xF0F0_0F0F),
            (enc.nor(3, 1, 2), {1: 0xFFFF_0000, 2: 0x0000_00FF}, 0x0000_FF00),
            (enc.slt(3, 1, 2), {1: 0xFFFF_FFFF, 2: 1}, 1),
            (enc.slt(3, 1, 2), {1: 0x7FFF_FFFF, 2: 0xFFFF_FFFF}, 0),
        ]
        for word, registers, expected in cases:
            with self.subTest(word=f"{word:08x}"):
                model, event = run_one(word, registers=registers)
                self.assertEqual(event.rd_data, expected)
                self.assertEqual(model.state.gpr[3], expected)

    def test_shifts_zero_and_thirty_one(self):
        cases = [
            (enc.sll(3, 2, 0), 0x8000_0001, 0x8000_0001),
            (enc.sll(3, 2, 31), 1, 0x8000_0000),
            (enc.srl(3, 2, 31), 0x8000_0000, 1),
            (enc.sra(3, 2, 31), 0x8000_0000, 0xFFFF_FFFF),
            (enc.sra(3, 2, 0), 0x8000_0000, 0x8000_0000),
        ]
        for word, operand, expected in cases:
            with self.subTest(word=f"{word:08x}"):
                model, event = run_one(word, registers={2: operand})
                self.assertEqual(event.rd_data, expected)
                self.assertEqual(model.state.gpr[3], expected)

    def test_immediate_extension(self):
        cases = [
            (enc.addi(2, 1, -1), 0, 0xFFFF_FFFF),
            (enc.andi(2, 1, 0x8001), 0xFFFF_FFFF, 0x0000_8001),
            (enc.ori(2, 1, 0x8001), 0x1234_0000, 0x1234_8001),
            (enc.xori(2, 1, 0xFFFF), 0xAAAA_5555, 0xAAAA_AAAA),
            (enc.lui(2, 0x8001), 0xDEAD_BEEF, 0x8001_0000),
        ]
        for word, source, expected in cases:
            with self.subTest(word=f"{word:08x}"):
                model, event = run_one(word, registers={1: source})
                self.assertEqual(event.rd_data, expected)
                self.assertEqual(model.state.gpr[2], expected)

    def test_nop_and_write_r0_retire_without_gpr_write(self):
        model, event = run_one(enc.sll(0, 1, 3), registers={1: 7})
        self.assertEqual(event.trap, 0)
        self.assertEqual(event.rd_we, 0)
        self.assertEqual(model.state.gpr[0], 0)
        self.assertEqual(model.state.retire_count, 1)


class MemoryTests(unittest.TestCase):
    WORD = 0x80FF_7F01

    def test_byte_lanes_and_signedness_use_full_raw_word(self):
        signed = [0x0000_0001, 0x0000_007F, 0xFFFF_FFFF, 0xFFFF_FF80]
        unsigned = [0x01, 0x7F, 0xFF, 0x80]
        for offset in range(4):
            for encoder, expected in ((enc.lb, signed[offset]),
                                      (enc.lbu, unsigned[offset])):
                with self.subTest(offset=offset, encoder=encoder.__name__):
                    model, event = run_one(
                        encoder(2, offset, 0), memory={0: self.WORD})
                    self.assertEqual(event.load_raw, self.WORD)
                    self.assertEqual(event.load_value, expected)
                    self.assertEqual(event.rd_data, expected)
                    self.assertEqual(event.mem_addr, offset)
                    self.assertEqual(event.mem_size, 1)

    def test_halfword_lanes_and_signedness(self):
        cases = [
            (enc.lh, 0, 0x0000_7F01),
            (enc.lhu, 0, 0x0000_7F01),
            (enc.lh, 2, 0xFFFF_80FF),
            (enc.lhu, 2, 0x0000_80FF),
        ]
        for encoder, offset, expected in cases:
            with self.subTest(encoder=encoder.__name__, offset=offset):
                model, event = run_one(
                    encoder(2, offset, 0), memory={0: self.WORD})
                self.assertEqual(event.load_raw, self.WORD)
                self.assertEqual(event.load_value, expected)
                self.assertEqual(event.mem_size, 2)

    def test_store_lane_merge_and_trace_payload(self):
        model, byte_event = run_one(
            enc.sb(2, 1, 0), registers={2: 0xA1B2_C3D4},
            memory={0: 0x1122_3344})
        self.assertEqual(model.memory.load_u(0, 4), 0x1122_D444)
        self.assertEqual(byte_event.store_data, 0xA1B2_C3D4)
        self.assertEqual(byte_event.bus_wdata, 0x0000_D400)
        self.assertEqual(byte_event.wstrb, 0x2)

        model, half_event = run_one(
            enc.sh(2, 2, 0), registers={2: 0xA1B2_C3D4},
            memory={0: 0x1122_3344})
        self.assertEqual(model.memory.load_u(0, 4), 0xC3D4_3344)
        self.assertEqual(half_event.bus_wdata, 0xC3D4_0000)
        self.assertEqual(half_event.wstrb, 0xC)

        model, word_event = run_one(
            enc.sw(2, 0, 0), registers={2: 0xA1B2_C3D4},
            memory={0: 0x1122_3344})
        self.assertEqual(model.memory.load_u(0, 4), 0xA1B2_C3D4)
        self.assertEqual(word_event.bus_wdata, 0xA1B2_C3D4)
        self.assertEqual(word_event.wstrb, 0xF)

    def test_load_to_r0_still_performs_memory_operation(self):
        model, event = run_one(enc.lw(0, 0, 0), memory={0: 0xDEAD_BEEF})
        self.assertEqual(event.rd_we, 0)
        self.assertEqual(event.mem_valid, 1)
        self.assertEqual(event.load_raw, 0xDEAD_BEEF)
        self.assertEqual(event.load_value, 0xDEAD_BEEF)
        self.assertEqual(model.state.gpr[0], 0)


class ControlAndTrapTests(unittest.TestCase):
    def test_branches_forward_and_backward(self):
        model, taken = run_one(enc.beq(1, 2, 2), registers={1: 7, 2: 7})
        self.assertEqual(taken.next_pc, 12)
        model, not_taken = run_one(enc.bne(1, 2, -1), registers={1: 7, 2: 7})
        self.assertEqual(not_taken.next_pc, 4)
        model, backward = run_one(enc.bne(1, 2, -2), registers={1: 7, 2: 8}, pc=8)
        self.assertEqual(backward.next_pc, 4)

    def test_jump_and_jal_no_delay_slot(self):
        model, event = run_one(enc.j(0x40, 0))
        self.assertEqual(event.next_pc, 0x40)
        self.assertEqual(event.rd_we, 0)
        model, event = run_one(enc.jal(0x40, 0))
        self.assertEqual(event.next_pc, 0x40)
        self.assertEqual((event.rd_we, event.rd, event.rd_data), (1, 31, 4))
        self.assertEqual(model.state.gpr[31], 4)

    def test_alignment_mapping_csr_and_illegal_causes(self):
        cases = [
            (enc.lw(2, 1, 0), {}, CAUSE_MISALIGNED_LOAD),
            (enc.sh(2, 1, 0), {2: 1}, CAUSE_MISALIGNED_STORE),
            (enc.lw(2, 0, 1), {1: 4096}, CAUSE_UNMAPPED_LOAD),
            (enc.sw(2, 0, 1), {1: 4096, 2: 1}, CAUSE_UNMAPPED_STORE),
            (enc.sw(2, 0, 1), {1: CSR_BASE, 2: 1}, CAUSE_CSR_ERROR),
            (enc.lb(2, 0, 1), {1: CSR_BASE}, CAUSE_CSR_ERROR),
            (enc.lw(2, 0x14, 1), {1: CSR_BASE}, CAUSE_CSR_ERROR),
            (0xFC00_0000, {}, CAUSE_ILLEGAL_INSTRUCTION),
            (enc.encode_r(1, 2, 3, 0, 0x3F), {}, CAUSE_ILLEGAL_INSTRUCTION),
            (0x6000_0001, {}, CAUSE_ILLEGAL_INSTRUCTION),
        ]
        for word, registers, cause in cases:
            with self.subTest(word=f"{word:08x}", cause=cause):
                model, event = run_one(word, registers=registers)
                self.assertEqual(event.trap, 1)
                self.assertEqual(event.cause, cause)
                self.assertEqual(event.epc, 0)
                self.assertEqual(event.next_pc, TRAP_VECTOR)
                self.assertEqual(event.rd_we, 0)
                self.assertEqual(event.mem_valid, 0)
                self.assertEqual(model.state.retire_count, 0)
                self.assertEqual(model.state.event_count, 1)

    def test_trap_handler_repairs_then_eret_retries_exact_epc(self):
        program = {
            0x00: enc.lw(2, 0, 1),
            0x80: enc.addi(1, 0, 0),
            0x84: enc.eret(),
        }
        model = ISS(program, initial_memory={0: 0xAABB_CCDD})
        model.set_reg(1, 1)
        events = model.run(4)
        self.assertEqual(events[0].cause, CAUSE_MISALIGNED_LOAD)
        self.assertEqual(events[0].retire_order, 0)
        self.assertEqual(events[1].pc, 0x80)
        self.assertEqual(events[1].retire_order, 0)
        self.assertEqual(events[2].pc, 0x84)
        self.assertEqual(events[2].next_pc, 0)
        self.assertEqual(events[2].retire_order, 1)
        self.assertEqual(events[3].pc, 0)
        self.assertEqual(events[3].load_value, 0xAABB_CCDD)
        self.assertEqual(model.state.gpr[2], 0xAABB_CCDD)
        self.assertEqual(model.state.event_count, 4)
        self.assertEqual(model.state.retire_count, 3)

    def test_read_only_csr_values_and_timing_provider_requirement(self):
        model = ISS([enc.nop(), enc.lw(2, 8, 1)])
        model.set_reg(1, CSR_BASE)
        model.run(2)
        self.assertEqual(model.state.gpr[2], 1)  # prior successful retire count

        timing_model = ISS([enc.lw(2, 0, 1)])
        timing_model.set_reg(1, CSR_BASE)
        with self.assertRaises(CSRValueUnavailable):
            timing_model.step()

    def test_reset_clears_core_state_but_retains_external_ram(self):
        model = ISS([enc.addi(1, 0, 7)], initial_memory={0: 0x1234_5678})
        model.step()
        model.state.epc = 12
        model.state.cause = 6
        model.reset()
        self.assertEqual(model.state.epoch, 1)
        self.assertEqual(model.state.pc, 0)
        self.assertEqual(model.state.gpr, [0] * 32)
        self.assertEqual((model.state.epc, model.state.cause), (0, 0))
        self.assertEqual(model.memory.load_u(0, 4), 0x1234_5678)


if __name__ == "__main__":
    unittest.main()
