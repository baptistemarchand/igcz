"""Unit tests. Run with: python3 -m unittest -v"""

import unittest

import arith
from bits import BitReader, BitWriter, read_column, write_column
from codec import decode_number, encode_number
from igcz import UnsupportedFlight, decode, encode

HEADER = "AXSR\nHFDTE130621\nI023638TAS3940SIU"


def flight(*lines, eol="\n", final_eol=True):
    text = eol.join(lines)
    return (text + eol if final_eol else text).encode("latin-1")


class CodecTest(unittest.TestCase):
    def test_round_trip(self):
        for n in [0, 1, -1, 63, -64, 64, -65, 127, 128, 10**9, -(10**9)]:
            data = encode_number(n)
            self.assertEqual(decode_number(data, 0), (n, len(data)), n)

    def test_small_numbers_take_one_byte(self):
        for n in range(-64, 64):
            self.assertEqual(len(encode_number(n)), 1, n)


class BitsTest(unittest.TestCase):
    def test_columns_round_trip(self):
        cases = [
            [],
            [0],
            [5] * 40,  # whole blocks of exact guesses
            [2_749_164, 2_749_168, 2_749_171, -5_000_000, 0, 3],  # huge jumps use the escape
            [(i * 7919) % 1000 - 500 for i in range(1000)],
        ]
        for values in cases:
            for slope in (True, False):
                bw = BitWriter()
                write_column(bw, values, slope)
                bw.write(1, 1)  # a marker right after, to check the reader stops at the right bit
                br = BitReader(bw.finish(), 0)
                self.assertEqual(read_column(br, len(values), slope), values)
                self.assertEqual(br.read(1), 1)

    def test_steady_flight_costs_well_under_a_byte_per_number(self):
        bw = BitWriter()
        write_column(bw, list(range(0, 3000, 3)), slope=True)
        self.assertLess(bw.bit_length() / 1000, 1)


class ArithTest(unittest.TestCase):
    CASES = [
        [],
        [0],
        [5] * 200,
        [2_749_164, 2_749_168, 2_749_171, -5_000_000, 0, 3, 2**40, -(2**40)],
        [(i * 7919) % 1000 - 500 for i in range(3000)],
    ]

    def test_columns_round_trip(self):
        enc = arith.RangeEncoder()
        for values in self.CASES:
            for slope in (True, False):
                arith.write_column(enc, values, slope)
        enc.direct(0b1011, 4)  # a marker right after, to check the decoder stays in step
        data = enc.finish()

        dec = arith.RangeDecoder(data, 0)
        for values in self.CASES:
            for slope in (True, False):
                self.assertEqual(arith.read_column(dec, len(values), slope), values)
        self.assertEqual(dec.direct(4), 0b1011)

    def test_carry_heavy_stream(self):
        # Answers that go against the learned odds push carries through runs of 0xFF bytes.
        probs = [arith.PROB_INIT]
        answers = [(i * 2654435761 >> 7) % 97 == 0 for i in range(50_000)] + [True] * 3000
        enc = arith.RangeEncoder()
        for a in answers:
            enc.bit(probs, 0, a)
        dec = arith.RangeDecoder(enc.finish(), 0)
        probs = [arith.PROB_INIT]
        self.assertEqual([bool(dec.bit(probs, 0)) for _ in answers], answers)

    def test_exact_guesses_are_nearly_free(self):
        enc = arith.RangeEncoder()
        arith.write_column(enc, list(range(0, 30_000, 3)), slope=True)
        self.assertLess(len(enc.finish()), 100)  # 10,000 numbers in under 100 bytes


class FormatTest(unittest.TestCase):
    def assertRoundTrip(self, raw):
        self.assertEqual(decode(encode(raw)), raw)

    def test_basic_flight(self):
        self.assertRoundTrip(flight(
            HEADER,
            "B1159204549164N00614910EA011700130802507",
            "B1159214549168N00614909EA011680130602607",
        ))

    def test_crlf_and_no_final_newline(self):
        self.assertRoundTrip(flight(
            HEADER,
            "B1159204549164N00614910EA011700130802507",
            eol="\r\n",
            final_eol=False,
        ))

    def test_south_west_and_invalid_fix(self):
        self.assertRoundTrip(flight(
            HEADER,
            "B1159203349164S07014910WV011700130802507",
            "B1159213349168S07014909WA011680130602607",
        ))

    def test_lines_between_fixes_stay_in_place(self):
        self.assertRoundTrip(flight(
            HEADER,
            "B1159204549164N00614910EA011700130802507",
            "E115920PEV",
            "LXSRsome comment",
            "B1159214549168N00614909EA011680130602607",
            "GABCDEF",
        ))

    def test_malformed_b_lines_are_kept_as_text(self):
        self.assertRoundTrip(flight(
            HEADER,
            "B1159204549164N00614910EA011700130802507",
            "B11592",
            "B1159214569999N00614909EA011680130602607",  # 69.999 minutes, not rebuildable
            "B1159224549168N00614909EX011680130602607",  # unknown validity char
        ))

    def test_non_digit_extensions(self):
        self.assertRoundTrip(flight(
            HEADER,
            "B1159204549164N00614910EA0117001308ab507",
            "B1159214549168N00614909EA011680130602607",
        ))

    def test_extensions_of_different_lengths(self):
        self.assertRoundTrip(flight(
            HEADER,
            "B1159204549164N00614910EA011700130802507",
            "B1159214549168N00614909EA0116801306",
        ))

    def test_no_fixes(self):
        self.assertRoundTrip(flight(HEADER))
        self.assertRoundTrip(b"")

    def test_non_ascii_header(self):
        self.assertRoundTrip(flight("HFPLTPILOT:Jérôme", "B1159204549164N00614910EA0117001308"))

    def test_small_step_back_in_time_is_kept(self):
        self.assertRoundTrip(flight(
            HEADER,
            "B1159204549164N00614910EA011700130802507",
            "B1159084549168N00614909EA011680130602607",
        ))

    def test_midnight_rollover_is_refused(self):
        with self.assertRaises(UnsupportedFlight):
            encode(flight(
                "B2359594549164N00614910EA0117001308",
                "B0000004549168N00614909EA0116801306",
            ))

    def test_negative_altitudes(self):
        self.assertRoundTrip(flight(
            HEADER,
            "B1159202109191S05516887EA-0001-000302507",
            "B1159212109191S05516884EA00002-000102607",
        ))


if __name__ == "__main__":
    unittest.main()
