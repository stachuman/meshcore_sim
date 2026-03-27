# Simulation vs Real MeshCore: Feature Gap Analysis

> Generated 2026-03-23, updated 2026-03-26. Covers MeshCore v1.14.1 (upstream submodule).
> SimNode now inherits from **BaseChatMesh** (chat layer), gaining real ACK/retry,
> contact management, timeout calculation, and duty-cycle enforcement.

---

## 1. MeshCore Class Hierarchy

```
Dispatcher          (TX/RX queue, duty-cycle, CAD retry)
  -> Mesh           (routing, crypto, flood/direct, PATH exchange)
    -> BaseChatMesh (contacts, ACK/retry, channels, connections, REQ/RESPONSE)
      -> Firmware   (UI, BLE, Wi-Fi, storage)
```

SimNode now sits at the **BaseChatMesh** level. Real MeshCore ACK/retry,
contact management, timeout calculation, and path exchange code runs
natively. Dispatcher duty-cycle enforcement is active via realistic
SimRadio TX timing.

---

## 2. What SimNode Implements (BaseChatMesh Layer)

| Feature | Status | Notes |
|---------|--------|-------|
| Flood routing (ROUTE_TYPE_FLOOD) | Full | Path built as relays append hashes |
| Direct routing (ROUTE_TYPE_DIRECT) | Full | Path consumed hop-by-hop |
| Transport codes (ROUTE_TYPE_TRANSPORT_*) | Full | Decoded in packet.py |
| ADVERT creation & broadcast | Full | `createSelfAdvert()` + `sendFlood()` |
| ADVERT reception & contact discovery | Full | `onDiscoveredContact()`, BaseChatMesh contacts[] |
| TXT_MSG send (flood or direct) | Full | `sendMessage()` via BaseChatMesh, real ACK tracking |
| TXT_MSG receive | Full | `onMessageRecv()`, emits `recv_text` JSON |
| PATH return on flood TXT_MSG | Full | BaseChatMesh `onPeerDataRecv()` handles automatically |
| PATH reception | Full | BaseChatMesh `onPeerPathRecv()` stores path |
| **ACK tracking & timeout** | **Full** | `processAck()` matches CRC, `onSendTimeout()` retries |
| **Message retry** | **Full** | Up to 3 retries via `sendMessage()` with attempt counter |
| **ACK timeout calculation** | **Full** | `calcFloodTimeoutMillisFor()`, `calcDirectTimeoutMillisFor()` |
| **Duty-cycle enforcement** | **Full** | SimRadio TX timing enables Dispatcher budget tracking |
| **TX airtime simulation** | **Full** | `isSendComplete()` waits for real airtime duration |
| Contact management | Full | BaseChatMesh 32-slot contact array with ECDH secrets |
| Relay forwarding control | Full | `allowPacketForward()` controlled by `--relay` |
| Peer lookup by hash | Full | BaseChatMesh `searchPeersByHash()` |
| ECDH shared secret | Full | BaseChatMesh auto-computed on contact discovery |
| `getRetransmitDelay()` | **Real** | MeshCore's random jitter runs (not overridden) |
| `getDirectRetransmitDelay()` | **Real** | Matches companion_radio formula (factor 0.2 instead of 0.5) |
| Deduplication | Full | MeshCore internal PacketManager handles it |
| AES128-CTR + 2-byte MAC | Full | MeshCore internal crypto |
| Ed25519 signatures (adverts) | Full | MeshCore internal |
| autoTuneByNeighborCount() | Conditional | `#if __has_include(<helpers/DelayTuning.h>)` guard |

---

## 3. What SimNode Does NOT Implement

### 3.1 BaseChatMesh Features (Now Implemented vs Still Missing)

**Implemented** (SimNode now inherits BaseChatMesh):

| Feature | MeshCore API | Status |
|---------|-------------|--------|
| ACK tracking & timeout | `processAck()`, `onSendTimeout()`, `txt_send_timeout` | **Full** — real BaseChatMesh ACK tracking runs |
| Message retry | `onSendTimeout()` triggers re-send | **Full** — up to 3 retries via `sendMessage()` |
| ACK timeout calculation | `calcFloodTimeoutMillisFor()`, `calcDirectTimeoutMillisFor()` | **Full** — airtime-proportional timeouts |
| Contact management | 32-slot contact array, ECDH secrets, path storage | **Full** — real BaseChatMesh contact book |
| Signed messages | `onSignedMessageRecv()` | **Full** — delegates to `onMessageRecv()` |
| CLI data | `onCommandDataRecv()` | **Full** — emits `recv_data` JSON |

**Still Missing:**

| Feature | MeshCore API | Impact on Simulation |
|---------|-------------|---------------------|
| **Extra ACK transmits** | `getExtraAckTransmitCount()` returns 0 by default | Real firmware may send redundant ACKs for reliability. |
| **Group channels** | `addChannel()`, `searchChannelsByHash()`, `sendGroupMessage()`, `onChannelMessageRecv()` | No GRP_TXT / GRP_DATA traffic. |
| **Keep-alive connections** | `startConnection()`, `checkConnections()`, `ConnectionInfo[16]` | No periodic pings to room servers or peers. |
| **REQ/RESPONSE** | `sendRequest()`, `onContactRequest()`, `onContactResponse()` | No command/control or sensor query traffic. |
| **ANON_REQ** | `sendAnonReq()`, `sendLogin()`, `onAnonDataRecv()` | No anonymous/ephemeral ECDH messages, no room login. |
| **Contact import/export** | `exportContact()`, `importContact()`, `shareContactZeroHop()` | No contact sharing between nodes. |
| **Blob KV storage** | `getBlobByKey()`, `putBlobByKey()` | No persistent advert cache. |
| **RTC bootstrap** | `bootstrapRTCfromContacts()` | Nodes share orchestrator epoch, no clock drift. |
| **Scoped flood** | `sendFloodScoped()` | No transport-code-scoped flooding (e.g., geographic limiting). |

### 3.2 Mesh Features Not Exercised

| Feature | MeshCore API | Notes |
|---------|-------------|-------|
| **TRACE packets** | `createTrace()`, `onTraceRecv()` | Collects per-hop SNR; never generated by SimNode or traffic. |
| **CONTROL packets** | `createControlData()`, `onControlDataRecv()` | Discovery/control protocol; not generated. |
| **RAW_CUSTOM packets** | `createRawData()`, `onRawDataRecv()` | Custom raw payloads; not generated. |
| **MULTIPART (fragmentation)** | `PAYLOAD_TYPE_MULTIPART`, `forwardMultipartDirect()` | Large message fragmentation; not generated. |
| **Multi-ACK** | `createMultiAck()`, `routeDirectRecvAcks()` | Reliability for direct-route ACKs; not exercised. |
| **filterRecvFloodPacket()** | Returns false by default | Could be used for privacy filtering; never overridden. |

### 3.3 Dispatcher Features

| Feature | MeshCore API | Status |
|---------|-------------|--------|
| **Duty-cycle enforcement** | `getDutyCycleWindowMs()`, `getRemainingTxBudget()`, `getAirtimeBudgetFactor()` | **Active** — SimRadio TX timing enables realistic budget tracking |
| **Half-duplex TX** | `isSendComplete()`, `isInRecvMode()` | **Full** — SimRadio blocks during TX airtime; orchestrator drops packets arriving at a transmitting receiver via `ChannelModel.is_receiver_busy()` |
| **CAD (Channel Activity Detection)** | `getCADFailRetryDelay()`, `getCADFailMaxDuration()` | Partial — Dispatcher code runs but SimRadio can't detect other nodes' transmissions |
| **RX delay** | `calcRxDelay()` | Default — not overridden |
| **Interference threshold** | `getInterferenceThreshold()` | SimRadio returns 0 (disabled) |
| **AGC reset** | `getAGCResetInterval()` | Not relevant for simulation |

---

## 4. Orchestrator Capabilities & Gaps

### 4.1 Packet Routing (router.py)

**Implemented:**
- Topology-graph-based delivery (adjacency = reachability)
- Per-edge packet loss (probabilistic, memoryless)
- Per-edge propagation delay (fixed latency_ms)
- Per-edge SNR (static, with directional overrides; RSSI derived as SNR + noise_floor)
- Airtime gating (delivery delayed by on-air time)
- Collision detection via ChannelModel

**Missing:**
- No frequency/channel selection (single channel assumed)
- No CSMA/ALOHA backoff (nodes transmit the instant MeshCore emits TX)
- No hidden-terminal mitigation beyond spatial reachability
- No link-quality variation over time (no fading, no temporal correlation)
- No multi-path propagation, shadowing, or Doppler
- No backpressure from orchestrator to node (stdin is fire-and-forget)

### 4.2 RF / Physical Layer (channel.py, airtime.py)

**Implemented:**
- LoRa airtime per Semtech AN1200.13 (SF, BW, CR, preamble)
- Hard collision detection (any temporal overlap at same receiver)
- Capture effect with log-distance path loss (configurable threshold)

**Missing:**
- No SF orthogonality (real LoRa decodes SF7 and SF12 simultaneously)
- No BER/PER curve (binary: packet is perfect or lost)
- No cumulative SINR (only pairwise collision checks)
- No coding-gain modeling — see [CR analysis below](#cr-coding-gain-analysis)
- No timing-offset resilience (LoRa tolerates symbol-level offsets)
- No TX power variation (all nodes equal power)
- No antenna patterns (isotropic radiators)
- No Rayleigh/Rician fading
- No frequency hopping

### 4.3 Traffic Generation (traffic.py)

**Implemented:**
- Staggered advertisement floods (uniform random over window)
- Periodic re-advertisement
- Poisson-distributed endpoint-to-endpoint text messages
- Source/destination selection from endpoint list

**Missing:**
- No group messaging traffic (GRP_TXT)
- No room-server traffic (login, sync, room posts)
- No REQ/RESPONSE or ANON_REQ traffic
- No bursty or correlated patterns
- No traffic matrices (all endpoints equally likely)
- No multipart/file transfer traffic
- No TRACE or CONTROL traffic generation

### 4.4 Metrics (metrics.py) & Tracing (tracer.py)

**Implemented:**
- Per-node TX/RX counts, RSS sampling
- Message delivery rate and latency
- Collision / link-loss / adversarial counters
- Per-packet hop traces with airtime, size, route type
- Relay retransmit delay computation
- Flood propagation time
- Witness count (privacy exposure metric)

**Missing:**
- No per-packet-type TX/RX counters
- No hop-count distribution
- No queue depth / backlog tracking
- No jitter (latency variance)
- No throughput (bytes/sec)
- No MeshCore-internal retransmission counts
- No per-hop SNR recording (available in EdgeLink, not traced)
- No energy/power consumption model

### 4.5 Adversarial Modelling (adversarial.py)

**Implemented:**
- Packet drop (probabilistic, RX-side)
- Packet corruption (random bit-flip)
- Packet replay (delayed re-injection)

**Missing:**
- No TX-side attacks (fake adverts, Sybil identities, wormholes)
- No targeted attacks (cannot filter by destination, type, or content)
- No adaptive adversary (no state, no learning from observed traffic)
- No timing attacks or side-channel leakage
- No packet injection (adversary cannot create new packets)

### 4.6 Configuration (config.py, topology.py)

**Implemented:**
- Full topology JSON with nodes, edges, radio, simulation sections
- Per-edge asymmetric link parameters
- Per-node binary override, heap limit, adversarial config
- Lat/lon for capture-effect model and visualization

**Missing:**
- No per-node TX power
- No frequency/channel configuration
- No duty-cycle limits
- No battery/energy model
- No mobility (lat/lon are static for entire run)
- No node churn (no join/leave during simulation)
- No link-quality variation profiles

---

## 5. Priority Assessment for Research Goals

### High Priority (affects autotune & privacy research)

| Gap | Status | Notes |
|-----|--------|-------|
| **ACK + retry** | **DONE** | SimNode inherits BaseChatMesh; real `sendMessage()` + `processAck()` + `onSendTimeout()` with up to 3 retries. |
| **Duty-cycle enforcement** | **DONE** | SimRadio TX timing enables Dispatcher's built-in token-bucket duty-cycle budget tracking. |
| **CSMA/LBT** | **Partial** | Half-duplex fully enforced: node can't TX while already transmitting, and orchestrator drops packets arriving at a transmitting receiver (`ChannelModel.is_receiver_busy()`). True LBT (detecting *other* nodes' transmissions before initiating TX) not possible without orchestrator-level channel sensing. |

### Medium Priority (affects simulation realism)

| Gap | Why it matters |
|-----|---------------|
| **Group channels** | Room servers and group messaging are a major MeshCore use case. Privacy analysis of GRP_TXT is different from unicast. |
| **SF orthogonality** | Overstates collision rates in mixed-SF deployments. |
| **Contact slot pressure** | SimNode inherits BaseChatMesh's real 32-slot contact array. Slot eviction under high density is modelled correctly. |
| **TRACE packets** | Could be used for network diagnostics but also reveal topology; relevant for privacy analysis. |

### Lower Priority (nice-to-have)

| Gap | Why it matters |
|-----|---------------|
| Fading models | Static links overstate reliability of good links and understate recovery. |
| Mobility | Static topology limits scenario diversity. |
| Energy model | Irrelevant for protocol correctness but important for deployment planning. |
| REQ/RESPONSE, ANON_REQ | Specialized traffic types; not central to delay tuning or basic privacy. |

---

## 6. CR Coding-Gain Analysis

The simulator accounts for CR in **airtime** (higher CR = longer on-air time)
but not in **reception quality** (higher CR = better FEC).  This section
documents why that omission has minimal impact.

### LoRa FEC is Hamming-family coding on 4-bit nibbles

| CR | Code | Error correction? |
|----|------|-------------------|
| 4/5 | Single parity bit | No — detect 1-bit errors only |
| 4/6 | Punctured Hamming | No — detect up to 2-bit errors only |
| 4/7 | (7,4) Hamming | Yes — correct 1-bit per codeword |
| 4/8 | (8,4) Extended Hamming (SECDED) | Yes — correct 1-bit, detect 2-bit per codeword |

Source: Afisiadis, Burg & Balatsoukas-Stimming, "Coded LoRa Frame Error Rate
Analysis", IEEE ICC 2020 (arXiv:1911.10245); EPFL reverse-engineering report.

### Sensitivity is CR-independent

The LoRa receiver chain is: RF front-end -> **chirp demodulator** -> Gray
decoder -> de-interleaver -> **Hamming decoder**.  Sensitivity is set by the
demodulator threshold, which depends only on SF:

| SF | SNR_required (dB) |
|----|-------------------|
| 7  | -7.5  |
| 8  | -10.0 |
| 9  | -12.5 |
| 10 | -15.0 |
| 11 | -17.5 |
| 12 | -20.0 |

CR does not appear in the Semtech sensitivity formula:

```
Sensitivity (dBm) = -174 + 10*log10(BW) + NF + SNR_required
```

The SX1276 datasheet (Table 12) lists sensitivity at CR 4/5 only — no separate
columns for other CRs.  Semtech FAQ states: *"The FEC does not provide any
significant increase in sensitivity, but instead offers improved immunity to
partial loss of the packet information."*

Sources: Semtech SX1276 datasheet; AN1200.22; Semtech FAQ.

### Coding gain in AWGN: ~2 dB for CR 4/7 and 4/8

The Afisiadis et al. paper derives closed-form FER approximations for coded
LoRa.  The (7,4) Hamming code provides approximately **2 dB coding gain** over
uncoded LoRa at FER 10^-3, consistent across all SF values.  CR 4/5 and 4/6
show **~0 dB gain** since they cannot correct errors.

Semtech AN1200.13 Figure 2 confirms this: PER curves for all four CRs are
very close together under thermal noise.

### Significant benefit only in fading/interference

The diagonal interleaver spreads codeword bits across multiple chirp symbols.
Combined with Hamming, this creates a code-diversity effect that is much more
powerful in **fading channels** than in AWGN:

- Elshabrawy & Robert (IEEE ICCE-Berlin 2019) found 7-8 dB BICM improvement
  in Rayleigh fading at CR 4/7.
- Thomas Telkamp (The Things Network) found a "very narrow" margin where
  CR 4/8 survived interference that CR 4/5 did not.

### Impact on the simulator

| Aspect | Impact | Reasoning |
|--------|--------|-----------|
| Capture-effect threshold | **None** | Sensitivity is CR-independent; the 6 dB threshold does not need CR adjustment |
| Binary reception model | **Minimal (~2 dB)** | The coding gain shifts the FER waterfall, but we don't model BER/PER curves |
| Static link quality | **None** | CR's main benefit is in fading channels; our links have fixed parameters |
| Airtime cost of higher CR | **Fully modelled** | Semtech AN1200.13 formula includes CR in payload symbol count |

**Conclusion:** The current approach of ignoring CR for reception quality is
justified.  The ~2 dB coding gain from CR 4/7 and 4/8 is real but only
affects post-demodulation bit errors, which the simulator does not model.
If BER/PER curves or fading models are added in the future, CR should be
incorporated into those models.

---

## 7. Wire Protocol Reference (node_agent)

### Orchestrator -> node_agent (stdin)

| Message | Fields | Purpose |
|---------|--------|---------|
| `time` | `epoch: int` | Sync node clock |
| `rx` | `hex: str, snr: float, rssi: float` | Deliver received packet (rssi = snr + noise_floor) |
| `send_text` | `dest: str, text: str` | Trigger text message send |
| `advert` | `name: str` | Trigger advertisement broadcast |
| `quit` | (none) | Graceful shutdown |

### node_agent -> Orchestrator (stdout)

| Message | Fields | Purpose |
|---------|--------|---------|
| `ready` | `pub: str, is_relay: bool, role: str` | Node initialized |
| `tx` | `hex: str` | Packet transmitted (triggers routing) |
| `advert` | `pub: str, name: str` | Learned peer from advert |
| `recv_text` | `name: str, text: str` | Received text message |
| `room_post` | `name: str, from: str, text: str` | Room message received |
| `ack` | `crc: str` | ACK received |
| `log` | `msg: str` | Internal log line |

---

## 8. Packet Type Reference

| Value | Name | Layer | Simulated? |
|-------|------|-------|------------|
| 0x00 | REQ | BaseChatMesh | No |
| 0x01 | RESPONSE | BaseChatMesh | No |
| 0x02 | TXT_MSG | Mesh | Yes |
| 0x03 | ACK | BaseChatMesh | Yes (full tracking + retry) |
| 0x04 | ADVERT | Mesh | Yes |
| 0x05 | GRP_TXT | BaseChatMesh | No |
| 0x06 | GRP_DATA | BaseChatMesh | No |
| 0x07 | ANON_REQ | Mesh | No |
| 0x08 | PATH | Mesh | Yes |
| 0x09 | TRACE | Mesh | No |
| 0x0A | MULTIPART | Mesh | No |
| 0x0B | CONTROL | Mesh | No |
| 0x0F | RAW_CUSTOM | Mesh | No |

| Route Type | Value | Simulated? |
|------------|-------|------------|
| TRANSPORT_FLOOD | 0x00 | Yes (decoded) |
| FLOOD | 0x01 | Yes |
| DIRECT | 0x02 | Yes |
| TRANSPORT_DIRECT | 0x03 | Yes (decoded) |
