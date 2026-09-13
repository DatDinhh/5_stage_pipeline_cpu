import dataclasses
from io import StringIO
import unittest

from verification import encoding as enc
from verification.checker import (
    CompletionError,
    StateFormatError,
    StateMismatch,
    TraceFormatError,
    TraceIntegrityError,
    TraceMismatch,
    TraceRecord,
    compare_trace,
    parse_state_text,
    parse_trace_text,
    state_text,
    trace_csv,
    verify_run,
)
from verification.iss import ISS


PROGRAM = [enc.addi(1, 0, 0x34), enc.sw(1, 0, 0), enc.lbu(2, 0, 0)]


def golden_run(extra_nop=False):
    words = PROGRAM + ([enc.nop()] if extra_nop else [])
    model = ISS(words, ram_size=16)
    events = model.run(len(words))
    records = [TraceRecord.from_event(event, cycle=10 + 3 * index,
                                      uid=100 + index)
               for index, event in enumerate(events)]
    return model, records


class ParserAndHappyPathTests(unittest.TestCase):
    def test_parse_and_verify_complete_run(self):
        final_model, records = golden_run()
        trace = trace_csv(records)
        state = state_text(final_model, cycle_count=99)
        parsed = parse_trace_text(trace)
        self.assertEqual(parsed, records)
        result = verify_run(
            ISS(PROGRAM, ram_size=16), StringIO(trace), StringIO(state),
            expected_event_count=3, completion_pc=8)
        self.assertEqual(result.event_count, 3)
        self.assertEqual(result.retire_count, 3)
        self.assertEqual(result.cycle_count, 99)

    def test_hex_parser_accepts_optional_prefix(self):
        _, records = golden_run()
        lines = trace_csv(records).splitlines()
        columns = lines[1].split(",")
        columns[6] = "0x" + columns[6]
        lines[1] = ",".join(columns)
        self.assertEqual(parse_trace_text("\n".join(lines) + "\n")[0].pc, 0)

    def test_state_parser_requires_complete_unique_records_and_end(self):
        final_model, _ = golden_run()
        good = state_text(final_model)
        parsed = parse_state_text(good)
        self.assertEqual(len(parsed.registers), 32)
        self.assertEqual(len(parsed.memory), 4)
        self.assertEqual(parsed.completion_seen, 1)
        with self.assertRaises(StateFormatError):
            parse_state_text(good.rsplit("END", 1)[0])
        with self.assertRaises(StateFormatError):
            parse_state_text(good + "REG,0,00000000\n")
        with self.assertRaises(StateFormatError):
            parse_state_text(good.replace("REG,1", "REG,0", 1))
        with self.assertRaises(StateFormatError):
            parse_state_text(good.replace("REG,1,00000034", "REG,1,xxxxxxxx"))


class TraceNegativeMutationTests(unittest.TestCase):
    def setUp(self):
        self.final_model, self.records = golden_run()

    def fresh(self):
        return ISS(PROGRAM, ram_size=16)

    def assert_field_mismatch(self, index, **changes):
        mutated = list(self.records)
        mutated[index] = dataclasses.replace(mutated[index], **changes)
        with self.assertRaises(TraceMismatch) as caught:
            compare_trace(self.fresh(), mutated, expected_event_count=3)
        for field in changes:
            self.assertIn(field, str(caught.exception))

    def test_mutated_retire_result(self):
        self.assert_field_mismatch(0, rd_data=self.records[0].rd_data ^ 1)

    def test_mutated_next_pc(self):
        self.assert_field_mismatch(0, next_pc=0x40)

    def test_mutated_store_address_and_data(self):
        self.assert_field_mismatch(1, mem_addr=4)
        self.assert_field_mismatch(1, store_data=0x35)
        self.assert_field_mismatch(1, bus_wdata=0x35)

    def test_mutated_load_response(self):
        self.assert_field_mismatch(2, load_raw=0x35)
        self.assert_field_mismatch(2, load_value=0x35)

    def test_missing_duplicate_and_extra_records(self):
        with self.assertRaises(TraceIntegrityError):
            compare_trace(self.fresh(), [self.records[0], self.records[2]],
                          expected_event_count=3)
        with self.assertRaises(TraceIntegrityError):
            compare_trace(self.fresh(), [self.records[0], self.records[0],
                                         self.records[1]], expected_event_count=3)
        _, four_records = golden_run(extra_nop=True)
        with self.assertRaises(CompletionError):
            compare_trace(ISS(PROGRAM + [0], ram_size=16), four_records,
                          expected_event_count=3)

    def test_last_record_missing_is_completion_failure(self):
        with self.assertRaises(CompletionError):
            compare_trace(self.fresh(), self.records[:-1], expected_event_count=3)

    def test_non_monotonic_cycle_and_duplicate_uid(self):
        records = list(self.records)
        records[1] = dataclasses.replace(records[1], cycle=records[0].cycle)
        with self.assertRaises(TraceIntegrityError):
            compare_trace(self.fresh(), records)
        records = list(self.records)
        records[1] = dataclasses.replace(records[1], uid=records[0].uid)
        with self.assertRaises(TraceIntegrityError):
            compare_trace(self.fresh(), records)

    def test_epoch_is_checked_against_the_reference_reset_epoch(self):
        records = [dataclasses.replace(record, epoch=1) for record in self.records]
        with self.assertRaises(TraceMismatch) as caught:
            compare_trace(self.fresh(), records)
        self.assertIn("epoch", str(caught.exception))

    def test_epoch_transition_resets_core_state_and_retains_ram(self):
        generating = ISS([enc.addi(1, 1, 1)], ram_size=16,
                         initial_memory={0: 0x12345678})
        before_reset = TraceRecord.from_event(generating.step(), cycle=5, uid=0)
        generating.reset()
        after_reset = TraceRecord.from_event(generating.step(), cycle=3, uid=0)
        checking = ISS([enc.addi(1, 1, 1)], ram_size=16,
                       initial_memory={0: 0x12345678})
        compare_trace(checking, [before_reset, after_reset], expected_event_count=2)
        self.assertEqual(checking.state.epoch, 1)
        self.assertEqual(checking.state.gpr[1], 1)
        self.assertEqual(checking.memory.load_u(0, 4), 0x12345678)

    def test_truncated_unknown_and_wrong_header_trace(self):
        text = trace_csv(self.records)
        lines = text.splitlines()
        lines[-1] = ",".join(lines[-1].split(",")[:-1])
        with self.assertRaises(TraceFormatError):
            parse_trace_text("\n".join(lines) + "\n")
        with self.assertRaises(TraceFormatError):
            parse_trace_text(text.replace("20010034", "xxxxxxxx", 1))
        with self.assertRaises(TraceFormatError):
            parse_trace_text(text.replace("epoch,event_order", "epoch,bad_order", 1))


class StateAndCompletionNegativeTests(unittest.TestCase):
    def setUp(self):
        self.final_model, self.records = golden_run()
        self.trace = trace_csv(self.records)
        self.state = state_text(self.final_model, cycle_count=99)

    def verify(self, state):
        return verify_run(
            ISS(PROGRAM, ram_size=16), StringIO(self.trace), StringIO(state),
            expected_event_count=3, completion_pc=8)

    def test_final_register_and_memory_mutations(self):
        with self.assertRaises(StateMismatch):
            self.verify(self.state.replace("REG,2,00000034", "REG,2,00000035"))
        with self.assertRaises(StateMismatch):
            self.verify(self.state.replace("MEM,00000000,00000034",
                                           "MEM,00000000,00000035"))

    def test_missing_memory_word_is_not_a_partial_pass(self):
        lines = [line for line in self.state.splitlines()
                 if not line.startswith("MEM,0000000c,")]
        with self.assertRaises(StateFormatError):
            self.verify("\n".join(lines) + "\n")

    def test_missing_completion_wrong_count_and_wrong_completion_pc(self):
        with self.assertRaises(CompletionError):
            self.verify(self.state.replace("END,3,1,99", "END,3,0,99"))
        with self.assertRaises(CompletionError):
            self.verify(self.state.replace("END,3,1,99", "END,2,1,99"))
        with self.assertRaises(CompletionError):
            verify_run(
                ISS(PROGRAM, ram_size=16), StringIO(self.trace),
                StringIO(self.state), expected_event_count=3, completion_pc=4)


if __name__ == "__main__":
    unittest.main()
