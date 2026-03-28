"""
channel.py — RF channel model for LoRa contention simulation.

Models simultaneous-transmission collisions at shared receivers using the
SNR values defined on topology edges.

Three-stage collision survival check (applied in order after temporal overlap
is detected):

1. **Capture effect** (default 6 dB threshold, Semtech AN1200.22):
   If the primary signal arrives sufficiently stronger than the interferer,
   the primary is decoded correctly.

2. **Preamble grace period** (LoRaSim model, Lancaster University):
   A LoRa receiver needs only 5 of the 8 preamble symbols to synchronize.
   If the interferer finishes before the primary's 5th preamble symbol, the
   primary survives.  Grace period = (preamble_symbols - 5) * T_sym.

3. **FEC overlap tolerance** (Hamming code analysis):
   CR 4/7 and 4/8 use Hamming codes that correct 1 bit per codeword.  With
   diagonal interleaving, a small overlap at the edge of the payload (~1-2
   symbol durations) can be corrected.  CR 4/5 and 4/6 have no correction
   capability, so any payload overlap is fatal.

Note: capture-effect comparison uses SNR differences, which are equivalent
to RSSI differences (noise floor cancels out at the same receiver).
"""

from __future__ import annotations

from typing import Optional


# FEC correction capability per coding-rate offset.
# cr=1 (4/5) and cr=2 (4/6): detection only, no correction.
# cr=3 (4/7): (7,4) Hamming, corrects 1 symbol per interleaving block.
# cr=4 (4/8): (8,4) extended Hamming, 1 correctable + probabilistic margin.
_FEC_SYMBOLS: dict[int, int] = {1: 0, 2: 0, 3: 1, 4: 2}


class ChannelModel:
    """
    Tracks active LoRa transmissions and detects collisions at receivers.

    Typical usage (called by PacketRouter)::

        channel.register_tx(sender, tx_start, tx_end, tx_id)
        # ... after propagation delay ...
        if channel.is_lost(sender, receiver, tx_start, tx_end, tx_id):
            return  # packet dropped due to collision
    """

    def __init__(
        self,
        link_snr: dict[str, dict[str, float]],
        sf: int,
        bw_hz: int,
        cr: int,
        link_latency_ms: dict[str, dict[str, float]] | None = None,
        capture_threshold_db: float = 6.0,
        preamble_symbols: int = 8,
    ) -> None:
        """
        Parameters
        ----------
        link_snr
            ``{sender_name: {receiver_name: snr_dB}}``.  Built from
            topology edges; directional overrides are already resolved.
            Serves double duty: key existence implies reachability, and the
            value is used for capture-effect power comparison (SNR differences
            are equivalent to RSSI differences at the same receiver).
        sf
            Spreading factor (7-12).
        bw_hz
            Bandwidth in Hz.
        cr
            Coding-rate offset (1=CR4/5 .. 4=CR4/8).  Determines FEC
            correction capability.
        link_latency_ms
            ``{sender_name: {receiver_name: latency_ms}}``.  Propagation
            delay per directed edge.  Used to shift TX windows to receiver-
            side arrival times before comparing for overlap.  If ``None``,
            all latencies default to 0 (sender-side = receiver-side).
        capture_threshold_db
            Minimum power advantage (dB) for the primary signal to survive a
            collision (default 6 dB, consistent with LoRa datasheets).
        preamble_symbols
            Number of preamble symbols (default 8).
        """
        self._link_snr = link_snr
        self._link_latency = link_latency_ms or {}
        self._cap_db    = capture_threshold_db

        # Overlap-aware collision parameters.
        self._sf = sf
        self._preamble_symbols = preamble_symbols
        self._t_sym = (2 ** sf) / bw_hz                      # seconds
        self._t_preamble = (preamble_symbols + 4.25) * self._t_sym
        self._fec_symbols = _FEC_SYMBOLS.get(cr, 0)

        # tx_id → (sender_name, tx_start, tx_end)
        self._active: dict[int, tuple[str, float, float]] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def register_tx(
        self, sender: str, tx_start: float, tx_end: float, tx_id: int
    ) -> None:
        """Record that *sender* is transmitting during ``[tx_start, tx_end]``."""
        self._active[tx_id] = (sender, tx_start, tx_end)

    def expire_before(self, t: float) -> None:
        """Discard TX records whose end time is earlier than *t*."""
        self._active = {k: v for k, v in self._active.items() if v[2] >= t}

    def is_receiver_busy(
        self, receiver: str, rx_start: float, rx_end: float
    ) -> Optional[tuple[int, float, float]]:
        """Check if *receiver* is transmitting during ``[rx_start, rx_end]``.

        LoRa is half-duplex: a node cannot receive while it is transmitting.

        Returns ``None`` if the receiver is free (falsy), or a tuple
        ``(busy_tx_id, busy_start, busy_end)`` if the receiver is busy
        (truthy).  Backward-compatible: ``None`` is falsy, tuples are truthy.
        """
        for tx_id, (sender, start, end) in self._active.items():
            if sender != receiver:
                continue
            # Temporal overlap
            if start < rx_end and end > rx_start:
                return (tx_id, start, end)
        return None

    def is_lost(
        self,
        primary_sender: str,
        receiver: str,
        tx_start: float,
        tx_end: float,
        tx_id: int,
    ) -> Optional[tuple[str, int, float]]:
        """Check if the packet is lost at *receiver* due to collision.

        Returns ``None`` if the packet survives (no collision), or a tuple
        ``(interferer_sender, interferer_tx_id, overlap_s)`` if the packet
        is lost.  ``None`` is falsy and tuples are truthy, so existing
        ``if is_lost(...)`` checks remain valid.

        A collision requires all three of:

        1. The interfering sender can reach *receiver* (has an edge with SNR).
        2. The interfering signal's **arrival window at the receiver** overlaps
           the primary signal's arrival window.  Arrival times are computed as
           ``tx_time + propagation_latency(sender → receiver)``.
        3. The primary signal is not sufficiently stronger than the interferer
           (capture effect: primary SNR - interferer SNR >= threshold).
        """
        primary_snr = self._link_snr.get(primary_sender, {}).get(receiver)

        # Shift primary TX window to arrival time at receiver.
        primary_lat_s = (self._link_latency
                         .get(primary_sender, {})
                         .get(receiver, 0.0)) / 1000.0
        rx_start = tx_start + primary_lat_s
        rx_end   = tx_end   + primary_lat_s

        for other_id, (sender, start, end) in self._active.items():
            if other_id == tx_id:
                continue  # skip self
            if sender == primary_sender:
                continue  # same node, different packet

            # Spatial reachability: can the interferer reach this receiver?
            interferer_snr = self._link_snr.get(sender, {}).get(receiver)
            if interferer_snr is None:
                continue

            # Shift interferer TX window to arrival time at receiver.
            int_lat_s = (self._link_latency
                         .get(sender, {})
                         .get(receiver, 0.0)) / 1000.0
            int_rx_start = start + int_lat_s
            int_rx_end   = end   + int_lat_s

            # Temporal overlap check (at the receiver)
            if int_rx_start >= rx_end or int_rx_end <= rx_start:
                continue

            # --- Stage 1: Capture effect ---
            # Primary survives if sufficiently stronger than interferer.
            if primary_snr is not None:
                if primary_snr - interferer_snr >= self._cap_db:
                    continue  # primary captures

            # --- Stages 2-3: Overlap-aware survival ---
            overlap_start = max(rx_start, int_rx_start)
            overlap_end = min(rx_end, int_rx_end)
            overlap_s = overlap_end - overlap_start

            # Stage 2: Preamble grace period (LoRaSim model).
            # The receiver needs 5 preamble symbols to sync.
            # If interference ends before the 5th preamble symbol,
            # the primary can still synchronize.
            grace_s = max(0.0, (self._preamble_symbols - 5) * self._t_sym)
            preamble_critical = rx_start + grace_s

            if int_rx_end <= preamble_critical:
                continue  # interferer only hit non-critical preamble

            # Stage 3: FEC overlap tolerance.
            # Small overlaps at the edges of the payload can be
            # corrected by Hamming FEC (CR 4/7 and 4/8 only).
            payload_start = rx_start + self._t_preamble
            fec_tolerance_s = self._fec_symbols * self._t_sym

            # Case A: Overlap entirely within payload, small enough for FEC.
            if overlap_start >= payload_start:
                if overlap_s <= fec_tolerance_s:
                    continue  # FEC corrects the corrupted symbols

            # Case B: Interferer starts after preamble critical point,
            # tail overlap is small enough for FEC.
            if overlap_start > preamble_critical:
                if overlap_s <= fec_tolerance_s:
                    continue  # FEC handles the tail corruption

            return (sender, other_id, overlap_s)  # collision — packet lost

        return None
