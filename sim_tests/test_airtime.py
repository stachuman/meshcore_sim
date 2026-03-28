"""
test_airtime.py — Unit tests for orchestrator/airtime.py and
orchestrator/channel.py.

Airtime spot-checks are derived from the Semtech AN1200.13 formula.
No node_agent binary required.
"""

from __future__ import annotations

import unittest

from orchestrator.airtime import lora_airtime_ms
from orchestrator.channel import ChannelModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _approx(a: float, b: float, tol: float = 0.1) -> bool:
    """True if a and b agree to within *tol* ms."""
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# Airtime formula tests
# ---------------------------------------------------------------------------

class TestLoraAirtimeFormula(unittest.TestCase):

    # --- Symbol duration ---

    def test_sf9_bw125_symbol_duration(self):
        """SF9 / BW125 kHz → T_sym = 2^9/125 = 4.096 ms."""
        # t_preamble = (8+4.25)*4.096 = 50.176 ms
        # Verify by checking a zero-payload edge case doesn't raise
        result = lora_airtime_ms(sf=9, bw_hz=125_000, cr=1, payload_bytes=1)
        self.assertGreater(result, 50.0)   # at least preamble duration

    # --- Spot-check against published Semtech calculator values ---

    def test_sf9_bw125_cr45_10bytes(self):
        # t_sym=4.096ms; preamble=50.176ms; payload_syms=23 → 144.384 ms
        result = lora_airtime_ms(sf=9, bw_hz=125_000, cr=1, payload_bytes=10)
        self.assertTrue(_approx(result, 144.384), f"got {result:.3f} ms")

    def test_sf9_bw125_cr45_64bytes(self):
        # payload_syms=83 → preamble+payload = 50.176+339.968 = 390.144 ms
        result = lora_airtime_ms(sf=9, bw_hz=125_000, cr=1, payload_bytes=64)
        self.assertTrue(_approx(result, 390.144), f"got {result:.3f} ms")

    def test_sf7_bw125_cr45_20bytes(self):
        # t_sym=1.024ms; preamble=12.544ms; payload_syms=43 → 56.576 ms
        result = lora_airtime_ms(sf=7, bw_hz=125_000, cr=1, payload_bytes=20)
        self.assertTrue(_approx(result, 56.576), f"got {result:.3f} ms")

    def test_sf12_bw125_cr45_10bytes_ldr(self):
        # SF12/BW125 → t_sym=32.768ms ≥ 16ms → LDR enabled (de=1)
        # preamble=401.408ms; payload_syms=18 → total=991.232 ms
        result = lora_airtime_ms(sf=12, bw_hz=125_000, cr=1, payload_bytes=10)
        self.assertTrue(_approx(result, 991.232), f"got {result:.3f} ms")

    # --- Monotonicity ---

    def test_larger_payload_takes_longer(self):
        base = lora_airtime_ms(sf=9, bw_hz=125_000, cr=1, payload_bytes=20)
        bigger = lora_airtime_ms(sf=9, bw_hz=125_000, cr=1, payload_bytes=80)
        self.assertGreater(bigger, base)

    def test_higher_sf_takes_longer(self):
        low_sf  = lora_airtime_ms(sf=7,  bw_hz=125_000, cr=1, payload_bytes=32)
        high_sf = lora_airtime_ms(sf=12, bw_hz=125_000, cr=1, payload_bytes=32)
        self.assertGreater(high_sf, low_sf)

    def test_wider_bandwidth_is_faster(self):
        narrow = lora_airtime_ms(sf=9, bw_hz=125_000, cr=1, payload_bytes=32)
        wide   = lora_airtime_ms(sf=9, bw_hz=500_000, cr=1, payload_bytes=32)
        self.assertGreater(narrow, wide)

    def test_higher_cr_takes_longer(self):
        cr45 = lora_airtime_ms(sf=9, bw_hz=125_000, cr=1, payload_bytes=32)
        cr48 = lora_airtime_ms(sf=9, bw_hz=125_000, cr=4, payload_bytes=32)
        self.assertGreater(cr48, cr45)

    def test_result_is_positive(self):
        for sf in (7, 9, 12):
            with self.subTest(sf=sf):
                result = lora_airtime_ms(sf=sf, bw_hz=125_000, cr=1,
                                         payload_bytes=16)
                self.assertGreater(result, 0.0)


# ---------------------------------------------------------------------------
# ChannelModel — hard collision (no positions)
# ---------------------------------------------------------------------------

class TestChannelModelHardCollision(unittest.TestCase):
    """Equal-SNR collisions (no capture effect).

    Uses CR 4/5 (cr=1, fec_symbols=0) so any payload overlap is fatal,
    matching the original binary-overlap behavior.
    """

    def _model(self, link_snr: dict) -> ChannelModel:
        return ChannelModel(link_snr=link_snr, sf=10, bw_hz=250_000, cr=1)

    # Helper: equal SNR → neither can capture → hard collision
    _EQUAL = {"A": {"R": -90.0}, "B": {"R": -90.0}}

    def test_no_collision_when_no_other_tx(self):
        m = self._model(self._EQUAL)
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        self.assertFalse(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))

    def test_no_collision_non_overlapping_windows(self):
        """B transmits after A's window ends → no collision."""
        m = self._model(self._EQUAL)
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.4, 0.7, tx_id=2)
        self.assertFalse(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))

    def test_collision_overlapping_windows(self):
        """A and B transmit simultaneously, both reach R → collision."""
        m = self._model(self._EQUAL)
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.1, 0.4, tx_id=2)
        self.assertTrue(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))

    def test_no_collision_interferer_cannot_reach_receiver(self):
        """B overlaps in time but cannot reach R → no collision."""
        m = self._model({"A": {"R": -90.0}, "B": {}})  # B cannot reach R
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.1, 0.4, tx_id=2)
        self.assertFalse(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))

    def test_expire_removes_old_records(self):
        m = self._model(self._EQUAL)
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.1, 0.4, tx_id=2)
        m.expire_before(0.5)   # both windows have ended
        self.assertFalse(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))

    def test_expire_keeps_active_records(self):
        m = self._model(self._EQUAL)
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.1, 0.4, tx_id=2)
        m.expire_before(0.2)   # B's window hasn't ended yet
        self.assertTrue(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))

    def test_touching_windows_not_collision(self):
        """Windows that touch but don't overlap: B starts exactly when A ends."""
        m = self._model(self._EQUAL)
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.3, 0.6, tx_id=2)   # start == A's end
        self.assertFalse(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))


# ---------------------------------------------------------------------------
# ChannelModel — capture effect (with positions)
# ---------------------------------------------------------------------------

class TestChannelModelCaptureEffect(unittest.TestCase):
    """
    Two nodes: A has strong SNR to R, B has weak SNR to R.
    With the capture effect enabled, A should survive B's interference.
    """

    # A has high SNR (30 dB), B has low SNR (-10 dB) → 40 dB difference >> 6 dB
    _SNR = {"A": {"R": 30.0}, "B": {"R": -10.0}}

    def _model(self, cap_db: float = 6.0) -> ChannelModel:
        return ChannelModel(
            link_snr=self._SNR, sf=10, bw_hz=250_000, cr=1,
            capture_threshold_db=cap_db,
        )

    def test_strong_primary_survives_weak_interferer(self):
        """A (30 dB) is much stronger than B (-10 dB) → capture."""
        m = self._model()
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.1, 0.4, tx_id=2)
        self.assertFalse(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))

    def test_weak_primary_lost_to_strong_interferer(self):
        """B (-10 dB) cannot capture against A (30 dB) → B is lost."""
        m = self._model()
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.1, 0.4, tx_id=2)
        self.assertTrue(m.is_lost("B", "R", 0.1, 0.4, tx_id=2))

    def test_equal_snr_hard_collision(self):
        """Two equal-SNR senders: neither captures → both lost."""
        m_hard = ChannelModel(
            link_snr={"A": {"R": -90.0}, "C": {"R": -90.0}},
            sf=10, bw_hz=250_000, cr=1,
        )
        m_hard.register_tx("A", 0.0, 0.3, tx_id=1)
        m_hard.register_tx("C", 0.1, 0.4, tx_id=2)
        self.assertTrue(m_hard.is_lost("A", "R", 0.0, 0.3, tx_id=1))


# ---------------------------------------------------------------------------
# ChannelModel — half-duplex (receiver busy during own TX)
# ---------------------------------------------------------------------------

class TestChannelModelHalfDuplex(unittest.TestCase):
    """LoRa is half-duplex: a node cannot receive while it is transmitting."""

    def _model(self, link_snr: dict) -> ChannelModel:
        return ChannelModel(link_snr=link_snr, sf=10, bw_hz=250_000, cr=1)

    _SNR = {"A": {"R": 10.0}, "R": {"A": 10.0}}

    def test_receiver_busy_during_own_tx(self):
        """R transmits [0.0, 0.5]; RX window [0.1, 0.4] fully inside → busy."""
        m = self._model(self._SNR)
        m.register_tx("R", 0.0, 0.5, tx_id=1)
        self.assertTrue(m.is_receiver_busy("R", 0.1, 0.4))

    def test_receiver_idle(self):
        """No active TX from R → not busy."""
        m = self._model({"A": {"R": -90.0}})
        self.assertFalse(m.is_receiver_busy("R", 0.1, 0.4))

    def test_partial_overlap_start(self):
        """R TX [0.0, 0.3]; RX window [0.2, 0.6] overlaps at the start."""
        m = self._model(self._SNR)
        m.register_tx("R", 0.0, 0.3, tx_id=1)
        self.assertTrue(m.is_receiver_busy("R", 0.2, 0.6))

    def test_partial_overlap_end(self):
        """R TX [0.4, 0.7]; RX window [0.2, 0.6] overlaps at the end."""
        m = self._model(self._SNR)
        m.register_tx("R", 0.4, 0.7, tx_id=1)
        self.assertTrue(m.is_receiver_busy("R", 0.2, 0.6))

    def test_no_overlap_touching(self):
        """R TX [0.0, 0.3]; RX window [0.3, 0.6] — touching but not overlapping."""
        m = self._model(self._SNR)
        m.register_tx("R", 0.0, 0.3, tx_id=1)
        self.assertFalse(m.is_receiver_busy("R", 0.3, 0.6))

    def test_other_node_tx_ignored(self):
        """A transmits but R does not → R is not busy."""
        m = self._model(self._SNR)
        m.register_tx("A", 0.0, 0.5, tx_id=1)
        self.assertFalse(m.is_receiver_busy("R", 0.1, 0.4))


# ---------------------------------------------------------------------------
# ChannelModel — propagation-aware collision detection
# ---------------------------------------------------------------------------

class TestChannelModelPropagation(unittest.TestCase):
    """Collision check accounts for propagation delay to the receiver.

    Signals from different senders arrive at the receiver shifted by their
    respective link latencies.  Overlap is checked at the receiver, not at
    the sender side.
    """

    # A and B both reach R with equal SNR (no capture effect).
    _SNR = {"A": {"R": 8.0}, "B": {"R": 8.0}}

    def test_sender_overlap_but_no_receiver_overlap(self):
        """A and B TX windows overlap sender-side, but different propagation
        delays separate them at R → no collision.

        A TX [0.0, 0.3], latency A→R = 0 ms  → arrives [0.0, 0.3]
        B TX [0.2, 0.5], latency B→R = 500 ms → arrives [0.7, 1.0]
        No overlap at R.
        """
        latency = {"A": {"R": 0.0}, "B": {"R": 500.0}}
        m = ChannelModel(link_snr=self._SNR, sf=10, bw_hz=250_000, cr=1, link_latency_ms=latency)
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.2, 0.5, tx_id=2)
        # Without propagation fix this would be True (sender windows overlap)
        self.assertFalse(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))

    def test_no_sender_overlap_but_receiver_overlap(self):
        """A and B TX windows do NOT overlap sender-side, but propagation
        delays bring them together at R → collision.

        A TX [0.0, 0.3], latency A→R = 500 ms → arrives [0.5, 0.8]
        B TX [0.5, 0.8], latency B→R = 0 ms   → arrives [0.5, 0.8]
        Full overlap at R.
        """
        latency = {"A": {"R": 500.0}, "B": {"R": 0.0}}
        m = ChannelModel(link_snr=self._SNR, sf=10, bw_hz=250_000, cr=1, link_latency_ms=latency)
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.5, 0.8, tx_id=2)
        # Without propagation fix this would be False (sender windows don't overlap)
        self.assertTrue(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))

    def test_equal_latency_same_as_zero(self):
        """When all latencies are equal, result matches zero-latency case."""
        latency = {"A": {"R": 100.0}, "B": {"R": 100.0}}
        m = ChannelModel(link_snr=self._SNR, sf=10, bw_hz=250_000, cr=1, link_latency_ms=latency)
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.1, 0.4, tx_id=2)
        # Both shift by 100ms → still overlap at receiver
        self.assertTrue(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))

    def test_no_latency_dict_defaults_to_zero(self):
        """When link_latency_ms is None, behaves as before (zero latency)."""
        m = ChannelModel(link_snr=self._SNR, sf=10, bw_hz=250_000, cr=1)
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.1, 0.4, tx_id=2)
        self.assertTrue(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))

    def test_partial_overlap_at_receiver(self):
        """Partial overlap at receiver due to asymmetric latency.

        A TX [0.0, 0.3], latency A→R = 200 ms → arrives [0.2, 0.5]
        B TX [0.1, 0.4], latency B→R = 0 ms   → arrives [0.1, 0.4]
        Overlap at R: [0.2, 0.4].
        """
        latency = {"A": {"R": 200.0}, "B": {"R": 0.0}}
        m = ChannelModel(link_snr=self._SNR, sf=10, bw_hz=250_000, cr=1, link_latency_ms=latency)
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.1, 0.4, tx_id=2)
        self.assertTrue(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))

    def test_touching_at_receiver_no_collision(self):
        """Signals touch but don't overlap at receiver.

        A TX [0.0, 0.3], latency A→R = 0 ms   → arrives [0.0, 0.3]
        B TX [0.0, 0.3], latency B→R = 300 ms  → arrives [0.3, 0.6]
        Touch at t=0.3 but no overlap.
        """
        latency = {"A": {"R": 0.0}, "B": {"R": 300.0}}
        m = ChannelModel(link_snr=self._SNR, sf=10, bw_hz=250_000, cr=1, link_latency_ms=latency)
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.0, 0.3, tx_id=2)
        self.assertFalse(m.is_lost("A", "R", 0.0, 0.3, tx_id=1))



# ---------------------------------------------------------------------------
# ChannelModel — overlap-aware collision survival
# ---------------------------------------------------------------------------

class TestCollisionOverlapSurvival(unittest.TestCase):
    """Tests for the physics-accurate overlap-aware collision model.

    Uses SF8 / BW 62500 Hz / CR 4/8 (EU Narrow) defaults:
      T_sym     = 2^8 / 62500 = 4.096 ms  = 0.004096 s
      T_preamble = (8 + 4.25) * T_sym = 50.176 ms = 0.050176 s
      Grace     = (8 - 5) * T_sym = 12.288 ms = 0.012288 s
      FEC syms  = 2 (CR 4/8)
      FEC tol   = 2 * T_sym = 8.192 ms = 0.008192 s
    """

    _T_SYM = 256 / 62500       # 0.004096 s
    _GRACE = 3 * _T_SYM        # 0.012288 s  (preamble_symbols=8, need 5)
    _T_PREAMBLE = 12.25 * _T_SYM  # 0.050176 s
    _FEC_TOL = 2 * _T_SYM      # 0.008192 s  (CR 4/8 → fec_symbols=2)

    # Equal SNR → no capture effect; collision must be resolved by overlap logic.
    _EQUAL = {"A": {"R": 8.0}, "B": {"R": 8.0}}

    def _model(self, cr: int = 4) -> ChannelModel:
        return ChannelModel(
            link_snr=self._EQUAL,
            sf=8, bw_hz=62500, cr=cr, preamble_symbols=8,
        )

    def test_interferer_ends_in_preamble_grace(self):
        """Interferer ends within the first 3 non-critical preamble symbols.

        A TX [0.0, 0.5].  B TX [0.0, grace-epsilon].
        B's signal ends before A's 5th preamble symbol → A survives.
        """
        m = self._model()
        a_end = 0.5
        b_end = self._GRACE - 0.001   # just inside grace period
        m.register_tx("A", 0.0, a_end, tx_id=1)
        m.register_tx("B", 0.0, b_end, tx_id=2)
        self.assertFalse(m.is_lost("A", "R", 0.0, a_end, tx_id=1))

    def test_interferer_hits_critical_preamble(self):
        """Interferer extends past the preamble grace period → lost.

        B's signal lasts well past the 5th preamble symbol.
        """
        m = self._model()
        a_end = 0.5
        b_end = self._GRACE + 0.1     # well past grace period
        m.register_tx("A", 0.0, a_end, tx_id=1)
        m.register_tx("B", 0.0, b_end, tx_id=2)
        self.assertTrue(m.is_lost("A", "R", 0.0, a_end, tx_id=1))

    def test_small_payload_overlap_fec_corrects(self):
        """Interferer starts in payload, overlap < FEC tolerance → survives.

        A TX [0.0, 0.5].  B starts after A's preamble, overlaps for
        less than fec_symbols * T_sym.
        """
        m = self._model()
        a_end = 0.5
        # B starts well after A's preamble, tiny overlap
        b_start = a_end - self._FEC_TOL + 0.001  # overlap = FEC_TOL - 0.001
        b_end = b_start + 0.3
        m.register_tx("A", 0.0, a_end, tx_id=1)
        m.register_tx("B", b_start, b_end, tx_id=2)
        self.assertFalse(m.is_lost("A", "R", 0.0, a_end, tx_id=1))

    def test_large_payload_overlap_fec_fails(self):
        """Interferer overlap > FEC tolerance → lost.

        Large overlap in the payload that exceeds Hamming correction.
        """
        m = self._model()
        a_end = 0.5
        # B starts mid-payload, large overlap
        b_start = 0.2
        b_end = 0.7
        m.register_tx("A", 0.0, a_end, tx_id=1)
        m.register_tx("B", b_start, b_end, tx_id=2)
        self.assertTrue(m.is_lost("A", "R", 0.0, a_end, tx_id=1))

    def test_tail_overlap_fec_corrects(self):
        """Late interferer, small tail overlap → FEC saves the packet.

        A TX [0.0, 0.5].  B starts just before A ends, overlap < FEC tolerance.
        B starts after the preamble critical point.
        """
        m = self._model()
        a_end = 0.5
        # B starts late, overlap is tiny
        b_start = a_end - self._FEC_TOL + 0.001
        b_end = b_start + 0.4
        m.register_tx("A", 0.0, a_end, tx_id=1)
        m.register_tx("B", b_start, b_end, tx_id=2)
        self.assertFalse(m.is_lost("A", "R", 0.0, a_end, tx_id=1))

    def test_full_overlap_always_lost(self):
        """Full temporal overlap, equal SNR → always lost."""
        m = self._model()
        m.register_tx("A", 0.0, 0.5, tx_id=1)
        m.register_tx("B", 0.0, 0.5, tx_id=2)
        self.assertTrue(m.is_lost("A", "R", 0.0, 0.5, tx_id=1))

    def test_no_fec_correction_at_low_cr(self):
        """CR 4/5 (cr=1) has fec_symbols=0; even tiny payload overlap is fatal.

        Same scenario as test_tail_overlap_fec_corrects but with CR 4/5.
        """
        m = self._model(cr=1)
        a_end = 0.5
        # Overlap is tiny but FEC can't help at CR 4/5
        b_start = a_end - 0.005
        b_end = b_start + 0.3
        m.register_tx("A", 0.0, a_end, tx_id=1)
        m.register_tx("B", b_start, b_end, tx_id=2)
        self.assertTrue(m.is_lost("A", "R", 0.0, a_end, tx_id=1))

    def test_cr45_no_fec_any_overlap_lost(self):
        """CR 4/5 has fec_symbols=0; any overlap past preamble grace → lost.

        B overlaps well into A's payload — no FEC correction available.
        """
        m = ChannelModel(
            link_snr=self._EQUAL, sf=8, bw_hz=62500, cr=1,
        )
        m.register_tx("A", 0.0, 0.5, tx_id=1)
        # B overlaps from 0.1 to 0.4 — way past preamble grace
        m.register_tx("B", 0.1, 0.4, tx_id=2)
        self.assertTrue(m.is_lost("A", "R", 0.0, 0.5, tx_id=1))


# ---------------------------------------------------------------------------
# ChannelModel — interferer info in is_lost() return value
# ---------------------------------------------------------------------------

class TestChannelModelInterfererInfo(unittest.TestCase):
    """is_lost() returns None on no collision, or a tuple with interferer details."""

    _EQUAL = {"A": {"R": -90.0}, "B": {"R": -90.0}}

    def _model(self) -> ChannelModel:
        return ChannelModel(link_snr=self._EQUAL, sf=10, bw_hz=250_000, cr=1)

    def test_returns_none_when_no_collision(self):
        """No interferer → None (falsy)."""
        m = self._model()
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        result = m.is_lost("A", "R", 0.0, 0.3, tx_id=1)
        self.assertIsNone(result)

    def test_returns_tuple_on_collision(self):
        """Overlapping TX → tuple (interferer_sender, interferer_tx_id, overlap_s)."""
        m = self._model()
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.1, 0.4, tx_id=2)
        result = m.is_lost("A", "R", 0.0, 0.3, tx_id=1)
        self.assertIsNotNone(result)
        interferer, int_tx_id, overlap = result
        self.assertEqual(interferer, "B")
        self.assertEqual(int_tx_id, 2)

    def test_overlap_duration_is_correct(self):
        """Overlap of A=[0.0,0.3] and B=[0.1,0.4] at R is 0.2 s."""
        m = self._model()
        m.register_tx("A", 0.0, 0.3, tx_id=1)
        m.register_tx("B", 0.1, 0.4, tx_id=2)
        result = m.is_lost("A", "R", 0.0, 0.3, tx_id=1)
        self.assertIsNotNone(result)
        _, _, overlap = result
        self.assertAlmostEqual(overlap, 0.2, places=5)


if __name__ == "__main__":
    unittest.main()
