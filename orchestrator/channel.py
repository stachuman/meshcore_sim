"""
channel.py — RF channel model for LoRa contention simulation.

Models simultaneous-transmission collisions at shared receivers.

Two modes are supported:

  Hard collision (positions=None):
      Any overlap between two transmissions whose senders can both reach the
      same receiver → both packets are lost at that receiver.

  Capture effect (positions provided, default capture_threshold_db=6.0):
      If the primary signal arrives at least ``capture_threshold_db`` stronger
      than any interferer, it is still decoded correctly.  Signal strength is
      derived from log-distance path loss using node lat/lon coordinates.
      This matches empirically measured LoRa behaviour in outdoor deployments.
"""

from __future__ import annotations

import math
from typing import Optional


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
        neighbors: dict[str, set[str]],
        positions: Optional[dict[str, tuple[float, float]]] = None,
        capture_threshold_db: float = 6.0,
        path_loss_exp: float = 3.0,
    ) -> None:
        """
        Parameters
        ----------
        neighbors
            ``{node_name: set_of_node_names_it_can_reach}``.  Used to decide
            which transmitters can interfere at a given receiver.
        positions
            ``{node_name: (lat_deg, lon_deg)}``.  When provided, relative
            received power is estimated via log-distance path loss, enabling
            the LoRa capture effect.  When ``None``, hard collision is used.
        capture_threshold_db
            Minimum power advantage (dB) for the primary signal to survive a
            collision (default 6 dB, consistent with LoRa datasheets).
        path_loss_exp
            Log-distance path-loss exponent *n* (default 3.0, typical outdoor
            suburban/urban).
        """
        self._neighbors   = neighbors
        self._positions   = positions
        self._cap_db      = capture_threshold_db
        self._n           = path_loss_exp

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
    ) -> bool:
        """Return ``True`` if *receiver* is transmitting during ``[rx_start, rx_end]``.

        LoRa is half-duplex: a node cannot receive while it is transmitting.
        """
        for _tx_id, (sender, start, end) in self._active.items():
            if sender != receiver:
                continue
            # Temporal overlap
            if start < rx_end and end > rx_start:
                return True
        return False

    def is_lost(
        self,
        primary_sender: str,
        receiver: str,
        tx_start: float,
        tx_end: float,
        tx_id: int,
    ) -> bool:
        """Return ``True`` if the packet is lost at *receiver* due to collision.

        A collision requires all three of:

        1. The interfering sender can reach *receiver* (is a neighbour).
        2. The interfering TX window overlaps ``[tx_start, tx_end]``.
        3. (When positions are available) the interferer's signal is not
           suppressed by the capture effect.
        """
        primary_rssi = self._rssi_relative(primary_sender, receiver)

        for other_id, (sender, start, end) in self._active.items():
            if other_id == tx_id:
                continue  # skip self
            if sender == primary_sender:
                continue  # same node, different packet

            # Temporal overlap check
            if start >= tx_end or end <= tx_start:
                continue

            # Spatial reachability check
            if receiver not in self._neighbors.get(sender, set()):
                continue

            # Capture effect: primary survives if it is sufficiently stronger
            if self._positions is not None:
                interferer_rssi = self._rssi_relative(sender, receiver)
                if primary_rssi - interferer_rssi >= self._cap_db:
                    continue  # primary captures

            return True  # collision — packet lost at this receiver

        return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _haversine_m(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """Great-circle distance in metres between two WGS-84 coordinates."""
        R   = 6_371_000.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = (math.sin(dphi / 2) ** 2
             + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
        return R * 2 * math.asin(math.sqrt(a))

    def _rssi_relative(self, sender: str, receiver: str) -> float:
        """Relative received power in dB (higher = stronger signal).

        Uses log-distance path loss with equal transmit power for all nodes.
        Returns 0.0 when position data is unavailable (hard-collision mode).
        """
        if self._positions is None:
            return 0.0
        pos_s = self._positions.get(sender)
        pos_r = self._positions.get(receiver)
        if pos_s is None or pos_r is None:
            return 0.0
        d = max(self._haversine_m(pos_s[0], pos_s[1], pos_r[0], pos_r[1]), 1.0)
        return -10.0 * self._n * math.log10(d)
