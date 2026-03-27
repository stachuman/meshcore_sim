"""
test_topology.py — Unit tests for orchestrator.topology

No binary or network access required.
"""

from __future__ import annotations

import unittest

from orchestrator.config import DirectionalOverrides, EdgeConfig, NodeConfig, SimulationConfig, TopologyConfig
from orchestrator.topology import Topology
from sim_tests.helpers import linear_three_config


# ---------------------------------------------------------------------------
# Linear three-node chain  (alice -- relay1 -- bob)
# ---------------------------------------------------------------------------

class TestTopologyLinearThree(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.topo = Topology(linear_three_config())

    def test_all_names(self):
        self.assertEqual(set(self.topo.all_names()), {"alice", "relay1", "bob"})

    def test_endpoint_names(self):
        self.assertEqual(set(self.topo.endpoint_names()), {"alice", "bob"})

    def test_relay_names(self):
        self.assertEqual(self.topo.relay_names(), ["relay1"])

    def test_node_config_lookup_endpoint(self):
        self.assertFalse(self.topo.node_config("alice").relay)

    def test_node_config_lookup_relay(self):
        self.assertTrue(self.topo.node_config("relay1").relay)

    def test_node_config_unknown_raises(self):
        with self.assertRaises(KeyError):
            self.topo.node_config("nobody")


class TestTopologyAdjacencyLinearThree(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.topo = Topology(linear_three_config())

    def test_alice_has_one_neighbour(self):
        self.assertEqual(len(self.topo.neighbours("alice")), 1)

    def test_alice_neighbour_is_relay1(self):
        self.assertEqual(self.topo.neighbours("alice")[0].other, "relay1")

    def test_relay1_has_two_neighbours(self):
        self.assertEqual(len(self.topo.neighbours("relay1")), 2)

    def test_relay1_neighbour_names(self):
        names = {lnk.other for lnk in self.topo.neighbours("relay1")}
        self.assertEqual(names, {"alice", "bob"})

    def test_bob_has_one_neighbour(self):
        self.assertEqual(len(self.topo.neighbours("bob")), 1)

    def test_bob_neighbour_is_relay1(self):
        self.assertEqual(self.topo.neighbours("bob")[0].other, "relay1")

    def test_edge_link_loss_preserved(self):
        link = self.topo.neighbours("alice")[0]
        self.assertAlmostEqual(link.loss, 0.05)

    def test_edge_link_latency_preserved(self):
        link = self.topo.neighbours("alice")[0]
        self.assertAlmostEqual(link.latency_ms, 20.0)

    def test_edge_link_snr_preserved(self):
        link = self.topo.neighbours("alice")[0]
        self.assertAlmostEqual(link.snr, 8.0)

    def test_edge_is_bidirectional_same_loss(self):
        # alice→relay1 and relay1→alice should carry the same edge parameters
        link_alice  = self.topo.neighbours("alice")[0]          # alice → relay1
        relay_links = {lnk.other: lnk for lnk in self.topo.neighbours("relay1")}
        link_relay  = relay_links["alice"]                       # relay1 → alice
        self.assertAlmostEqual(link_alice.loss,       link_relay.loss)
        self.assertAlmostEqual(link_alice.latency_ms, link_relay.latency_ms)

    def test_unknown_node_neighbours_empty(self):
        self.assertEqual(self.topo.neighbours("nobody"), [])


# ---------------------------------------------------------------------------
# Single isolated node (no edges)
# ---------------------------------------------------------------------------

class TestTopologyIsolatedNode(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cfg = TopologyConfig(
            nodes=[NodeConfig(name="lone", relay=False)],
            edges=[],
            simulation=SimulationConfig(),
        )
        cls.topo = Topology(cfg)

    def test_no_neighbours(self):
        self.assertEqual(self.topo.neighbours("lone"), [])

    def test_is_endpoint(self):
        self.assertEqual(self.topo.endpoint_names(), ["lone"])

    def test_relay_names_empty(self):
        self.assertEqual(self.topo.relay_names(), [])


# ---------------------------------------------------------------------------
# All-relay topology
# ---------------------------------------------------------------------------

class TestTopologyAllRelays(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cfg = TopologyConfig(
            nodes=[
                NodeConfig(name="r1", relay=True),
                NodeConfig(name="r2", relay=True),
            ],
            edges=[EdgeConfig(a="r1", b="r2")],
            simulation=SimulationConfig(),
        )
        cls.topo = Topology(cfg)

    def test_endpoint_names_empty(self):
        self.assertEqual(self.topo.endpoint_names(), [])

    def test_relay_names_both(self):
        self.assertEqual(set(self.topo.relay_names()), {"r1", "r2"})

    def test_r1_has_one_neighbour(self):
        self.assertEqual(len(self.topo.neighbours("r1")), 1)
        self.assertEqual(self.topo.neighbours("r1")[0].other, "r2")


# ---------------------------------------------------------------------------
# Star topology (hub + four spokes)
# ---------------------------------------------------------------------------

class TestTopologyStarFive(unittest.TestCase):
    """Verifies that multi-edge construction keeps per-edge parameters distinct."""

    @classmethod
    def setUpClass(cls):
        cfg = TopologyConfig(
            nodes=[
                NodeConfig(name="hub",   relay=True),
                NodeConfig(name="n1",    relay=False),
                NodeConfig(name="n2",    relay=False),
                NodeConfig(name="n3",    relay=False),
                NodeConfig(name="n4",    relay=False),
            ],
            edges=[
                EdgeConfig(a="n1", b="hub", loss=0.1,  latency_ms=10.0, snr=9.0),
                EdgeConfig(a="n2", b="hub", loss=0.2,  latency_ms=20.0, snr=7.0),
                EdgeConfig(a="n3", b="hub", loss=0.3,  latency_ms=30.0, snr=5.0),
                EdgeConfig(a="n4", b="hub", loss=0.05, latency_ms=5.0,  snr=12.0),
            ],
            simulation=SimulationConfig(),
        )
        cls.topo = Topology(cfg)

    def test_hub_has_four_neighbours(self):
        self.assertEqual(len(self.topo.neighbours("hub")), 4)

    def test_spoke_has_one_neighbour(self):
        for name in ["n1", "n2", "n3", "n4"]:
            self.assertEqual(len(self.topo.neighbours(name)), 1, msg=f"node {name}")

    def test_distinct_edge_losses_preserved(self):
        by_other = {lnk.other: lnk for lnk in self.topo.neighbours("hub")}
        self.assertAlmostEqual(by_other["n1"].loss, 0.1)
        self.assertAlmostEqual(by_other["n2"].loss, 0.2)
        self.assertAlmostEqual(by_other["n3"].loss, 0.3)
        self.assertAlmostEqual(by_other["n4"].loss, 0.05)

    def test_distinct_edge_latencies_preserved(self):
        by_other = {lnk.other: lnk for lnk in self.topo.neighbours("hub")}
        self.assertAlmostEqual(by_other["n1"].latency_ms, 10.0)
        self.assertAlmostEqual(by_other["n4"].latency_ms, 5.0)

    def test_endpoint_count(self):
        self.assertEqual(len(self.topo.endpoint_names()), 4)


# ---------------------------------------------------------------------------
# Asymmetric edges — per-direction override resolution
# ---------------------------------------------------------------------------

class TestTopologyAsymmetricEdge(unittest.TestCase):
    """
    Verifies that directional overrides are applied to the correct EdgeLink
    and that unoverridden fields fall back to the symmetric base value.

    Setup: tx → rx edge with:
      symmetric base: loss=0.1, latency_ms=20, snr=8
      a_to_b (tx→rx): snr=14             (only RF quality overridden)
      b_to_a (rx→tx): loss=1.0           (one-way: total loss in return direction)
    """

    @classmethod
    def setUpClass(cls):
        cfg = TopologyConfig(
            nodes=[
                NodeConfig(name="tx", relay=False),
                NodeConfig(name="rx", relay=False),
            ],
            edges=[
                EdgeConfig(
                    a="tx", b="rx",
                    loss=0.1, latency_ms=20.0, snr=8.0,
                    a_to_b=DirectionalOverrides(snr=14.0),
                    b_to_a=DirectionalOverrides(loss=1.0),
                )
            ],
            simulation=SimulationConfig(),
        )
        cls.topo = Topology(cfg)
        cls.tx_link = cls.topo.neighbours("tx")[0]   # tx → rx
        cls.rx_link = cls.topo.neighbours("rx")[0]   # rx → tx

    # -- forward direction (tx → rx): SNR overridden --

    def test_forward_snr_overridden(self):
        self.assertAlmostEqual(self.tx_link.snr, 14.0)

    def test_forward_loss_inherits_base(self):
        self.assertAlmostEqual(self.tx_link.loss, 0.1)

    def test_forward_latency_inherits_base(self):
        self.assertAlmostEqual(self.tx_link.latency_ms, 20.0)

    # -- reverse direction (rx → tx): loss overridden, rest inherits base --

    def test_reverse_loss_is_one(self):
        self.assertAlmostEqual(self.rx_link.loss, 1.0)

    def test_reverse_snr_inherits_base(self):
        self.assertAlmostEqual(self.rx_link.snr, 8.0)

    def test_reverse_latency_inherits_base(self):
        self.assertAlmostEqual(self.rx_link.latency_ms, 20.0)

    # -- directions are independent --

    def test_forward_and_reverse_differ(self):
        self.assertNotAlmostEqual(self.tx_link.snr,  self.rx_link.snr)
        self.assertNotAlmostEqual(self.tx_link.loss, self.rx_link.loss)


class TestTopologySymmetricRegressionAfterChange(unittest.TestCase):
    """
    Proves that a plain EdgeConfig (no directional overrides) still
    produces identical parameters in both directions.
    """

    @classmethod
    def setUpClass(cls):
        cfg = TopologyConfig(
            nodes=[NodeConfig(name="p"), NodeConfig(name="q")],
            edges=[EdgeConfig(a="p", b="q", loss=0.2, latency_ms=30.0,
                              snr=7.0)],
            simulation=SimulationConfig(),
        )
        cls.topo = Topology(cfg)

    def test_both_directions_have_same_loss(self):
        p_link = self.topo.neighbours("p")[0]
        q_link = self.topo.neighbours("q")[0]
        self.assertAlmostEqual(p_link.loss, q_link.loss)
        self.assertAlmostEqual(p_link.loss, 0.2)

    def test_both_directions_have_same_latency(self):
        p_link = self.topo.neighbours("p")[0]
        q_link = self.topo.neighbours("q")[0]
        self.assertAlmostEqual(p_link.latency_ms, q_link.latency_ms)

    def test_both_directions_have_same_snr(self):
        p_link = self.topo.neighbours("p")[0]
        q_link = self.topo.neighbours("q")[0]
        self.assertAlmostEqual(p_link.snr, q_link.snr)


if __name__ == "__main__":
    unittest.main()
