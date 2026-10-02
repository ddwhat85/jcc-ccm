# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Primary: JCC Solution staff** — control-room operators watching every customer's panels on an office monitor all day, and field technicians doing installation, periodic inspection, and repair in front of the panel on a phone (bright plant floors and dark electrical rooms alike). Office and field use are roughly equal (confirmed 2026-10-01).
- **Secondary: customer accounts** (manager / viewer) who see only their own panels, on their own screen **JCC GUARD** (`/guard`, `server/static/guard.html`): a premium instrument-cluster world (dark night + light day) built to make the customer feel the service is worth paying for, and shown to prospects as a sales demo with replayable "that night" stories (decided 2026-10-02; the earlier emoji/illustration plan is dropped). This dashboard (`index.html`) remains the staff tool; customer accounts that log in here are sent to `/guard`.

## Product Purpose

JCC-CCM monitors electrical and control panels through Turck IM18-CCM50 gateways running JCC's own firmware. It predicts fire precursors (H2/VOC/CO + temperature), contact overheating (current-vs-temperature residual), and condensation, drives vents, heaters, and fans on the edge without waiting for the server, and gives JCC staff one place to watch, inspect, and report. Success: staff catch a developing fault days before it becomes an incident and can prove it to the customer.

## Positioning

Built to replace UlalaLab Wim-X with JCC's own clean-room firmware and server. The decisive actions (venting, heating) run as verified rules on the gateway itself and are proven against an accident/trap scenario exam (tuning console). AI only explains and predicts; it never controls outputs.

## Operating Context

- Control room: fleet list of all panels sorted by risk, alarm bell, live log, AI questions, monthly customer reports.
- Field: node add (device discovery and manual sensor spec), commissioning wizard, periodic inspection log with photos and the customer's signature, printable reports.
- Data: sensor readings, alarms with cause and action notes, prediction state, remaining-life forecasts, inspection and commissioning records.

## Capabilities and Constraints

- Single-file dashboard (`server/static/index.html`) served by a Python stdlib server; a demo build (`web/`) runs without a server as a Claude artifact.
- Must work on desktop monitors and phones (375px) with no horizontal overflow.
- Korean UI throughout; numbers, units, and timestamps are read constantly.
- No external runtime dependencies besides optional fonts.

## Brand Commitments

- JCC Solution logo (`server/static/img/logo-light.png`, `logo-dark.png`) and its red stay (confirmed).
- Both dark and light themes must exist, with a toggle (confirmed).
- The user's quality bar: premium car instrument clusters, Apple/Linear-grade software, industrial control systems (Siemens/Schneider), fine watches and measuring instruments (confirmed).

## Evidence on Hand

- Real product facts: Turck IM18-CCM50, InfraSensing H2, Banner sensors (`firmware/jcc_ccm/discovery/profiles.py`).
- No customer testimonials, benchmarks, or deployments yet; do not fabricate them.

## Product Principles

1. Rules decide, AI explains: safety-relevant actions stay deterministic and verifiable.
2. Show the reason, not only the state: every status carries the data that justifies it.
3. One glance in the control room, one hand in the field.
4. Records are evidence: reports, inspections, and alarm notes are written so a customer can rely on them.

## Accessibility & Inclusion

Readable in bright and dark environments; status must never rely on color alone (text label accompanies every status color).
