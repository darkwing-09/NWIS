# NWIS Implementation Blockers Log

This document records architectural and external dependencies that are formally **BLOCKED** per Part 24 (Operating Rule 19) of the NWIS Master Technical Implementation Specification.

---

## 1. Module 15 — Production eRTMAC Adapter

- **Module**: Module 15 (eRTMAC Integration)
- **Component**: `services/ertmac/production_adapter.py::ProductionERTMACAdapter`
- **Status**: **BLOCKED**
- **Missing Dependency**: Confirmed eRTMAC streaming API/WITSML protocol specification from Oil India Limited (OIL).
- **Reason**: The exact wire protocol (Kafka topic format, WITSML store endpoint, MQTT broker, or proprietary OIL TCP feed) and authentication schema for live rig streaming have not been finalized by OIL IT/operations.
- **Contract Boundary**: The interface `ERTMACAdapter` (`services/ertmac/adapter.py`) defines the exact asynchronous protocol (`get_active_well_context`, `subscribe_to_well_events`).
- **Mitigation / Active Path**: `SimulatedERTMACAdapter` (`services/ertmac/simulator.py`) is fully functional and serves all lower environments (dev, test, staging, live demo) with slider depth overrides and automated tick generation. No workaround architecture is introduced.
- **Unblocking Action**: Obtain OIL eRTMAC protocol interface specification and staging test endpoint credentials; implement network client in `ProductionERTMACAdapter`.

---

## 2. Module 22 — Risk Stage 2/3 Model Active-Mode Training

- **Module**: Module 22 / Module 13.2 / 13.3 (Statistical Risk Engine & Model Registry)
- **Status**: **BLOCKED** (by design in Phase 3 MVP)
- **Missing Dependency**: Production labeled operational data (minimum threshold: ≥50–100 real labeled events in `risk_assessments` and historical drilling reports).
- **Reason**: Training ML models without adequate ground truth creates hallucinated risk scores and poses safety risks on active rigs.
- **Mitigation / Active Path**: Rule-based Stage 1 engine (`services/risk/rule_based.py`) provides deterministic risk assessments. Model registry and drift monitor scaffolds exist in inactive shadow evaluation mode only.
- **Unblocking Action**: Accumulate ≥50–100 verified historical well events in production; execute shadow-mode evaluation with signed-off evaluation report before promoting any model to active mode.
