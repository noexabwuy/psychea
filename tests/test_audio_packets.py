# MicPackets: the 1-ms accounting both microphone streamers share.
from psychea.audio_packets import MicPackets


class Src:
    def __init__(self):
        self.asked = []

    def read(self, n):
        self.asked.append(n)
        return b"\x01\x00" * n


def test_the_fractional_rate_carries_over_between_packets():
    src = Src()
    p = MicPackets(src, 22050, False)
    sizes = [len(p.next()) // 2 for _ in range(1000)]
    assert sum(sizes) == 22050 and set(sizes) == {22, 23} and p.acc == 0


def test_a_high_rate_is_capped_to_one_packet():
    src = Src()
    assert len(MicPackets(src, 96000, False).next()) == 126
    assert src.asked == [63]


def test_extended_packets_carry_a_two_byte_header_and_one_sample_less():
    src = Src()
    pkt = MicPackets(src, 96000, True).next()
    assert pkt[:2] == b"\x00\x00" and len(pkt) == 2 + 124 and src.asked == [62]
