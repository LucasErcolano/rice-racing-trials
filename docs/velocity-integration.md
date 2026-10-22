# Velocity: scope of a timing integration

Checked on **21 October 2026**, local time UTC-03. Code review on `feature/server-penalties-v2`, HEAD `03b4958`, with changes from other agents in progress. This document describes the observed state; it does not certify a field installation.

## Operator confirmation for the V2 tests

Velocity confirmed to the user CSP 2.11, authorization to install our Lua app, LAN connectivity between stations and permission to run our Rice server and leaderboard on the TV. V2 uses these resources and does NOT depend on integrating with or replacing the Velocity leaderboard. Not having identified an external API does not block publishing or testing. Exact compatibility of the client with that runtime: pending the first field session.

## Historical research on an external API (out of V2 scope)

**A direct timing API for the product the project calls "Velocity" is not confirmed. Any future external integration would need the exact vendor and version; it is neither a dependency nor a blocker for V2.**

The context received does not identify the manufacturer, official site, edition, version or manual of the installed product. Searching the repository and its available history found no reference that resolves that identity. Matches for `velocity` in telemetry vectors do not identify a vendor.

It is not appropriate to pick an API by name similarity, invent endpoints or confuse Assetto Corsa support with authorization to read or write this project's times. Nor was it confirmed that Velocity can import the final penalized Rice time, fetch each lap in real time, recover laps after an outage or work without Rice/CSP.

## Public research, primary sources only

Public pages of the vendors/operators themselves were inspected. Third-party results from general searches were not used as technical evidence. No vendor was contacted, no account was accessed, no credentials were used and the VM was not queried.

| Official source consulted | What it actually supports | What it does not show |
|---|---|---|
| Velocity Pro Sims, home page | Announces its own timing/scoring platform, participant registration and leaderboard.[1] | Does not identify the product installed at Rice and, on the page consulted, provides no API contract to integrate with it. |
| Velocity Sim Racing Lounge, 610 Challenge page | Describes its virtual racing experience and lap times.[2] | Does not establish that it is Rice's vendor and documents no extraction/import interface there. |
| Sim Racing Limited, VMS API Reference v0.1 | Documents lap queries and telemetry export, API key authentication and license-based permissions.[3] | **Does not establish that VMS is Velocity.** Its routes and credentials must not be carried over to an assumed Velocity API. |
| Sim Racing Limited, Downloads | Publishes Assetto Corsa support and collection of laps/sectors/telemetry/results in its own product.[4] | VMS compatibility with AC does not prove Velocity's identity, installation, license or compatibility with Rice. |

The Sim Racing Limited API documentation was retrieved from `/docs/`, which redirected to `/docs/v0.1/`. It is a contrasting source used to rule out a wrong attribution, **not an API chosen for implementation**.[3]

In particular, marketing for a timing platform is not a public API. Nor does the absence of documentation found prove that no private API, SDK, authorized export or different product with the same name exists.

### Search log

Queries run verbatim in this review:

```text
"Velocity" "Assetto Corsa" timing API
"Velocity" "sim racing" "API"
"Velocity" "timing software" "simulator"
"Velocity" "Assetto Corsa" "timing" software
site:velocityprosims.com "API" "timing"
site:velocitysimlounge.com "API"
"Velocity" "Assetto Corsa" "management software"
"Velocity" "lap timing" "API"
```

The general queries mostly returned uses of "velocity" as physical speed, other products and commercial pages. The query restricted to `velocityprosims.com` returned no results; the one for `velocitysimlounge.com` returned the venue's pages, not an identified API specification. No query established the vendor/version used by Rice. This is a limited survey of public content, not an exhaustive search or evidence of non-existence.

The configured web extraction failed because it is a search-only backend. The four official sources were retrieved with a public HTTP GET using `curl -L --fail`, and the HTML converted to text was read. No API examples or authenticated requests were run.

## What is needed to unblock the integration

These are design and acceptance requirements, **not capabilities attributed to Velocity**:

1. **Identity:** full commercial name, vendor and official site, edition/license and version/build. A screenshot of "About" without sensitive data, or a link to the exact product's manual, is enough. No passwords or tokens need to be shared.
2. **Official interface contract:** API/SDK/export supported for that version; data flow direction (read laps, write results or both); formats, units, IDs, permissions, limits, retention, pagination and historical recovery. Do not assume REST, webhooks, CSV or direct access to its database.
3. **Concrete compatibility:** original Assetto Corsa, mode used by the stations, FSAEJ track/layout and MFT02 car. Confirm the timing source and when it publishes a lap, and how it handles out lap, invalid lap, reset, pause and driver change.
4. **Identities and time:** map station, driver, session, track/layout, vehicle and stable lap; distinguish duration in milliseconds from timestamp and time zone. Handle assignment changes and avoid attributing old laps to the new driver.
5. **Scoring semantics:** keep the native raw time, native validity and Rice penalized time separate, with provenance. A native validity flag does not account for FSAE cones/off-course/slalom. An external lap without evidence does not become official automatically.
6. **Reliability and isolation:** define deduplication, ACK, retry, persistence, replay after an outage, conflicts and the authority of each source. If surviving a failure of the Rice Lua is required, capture must happen outside that same runtime, with its own supervision and storage. Another process alone does not remove the dependency on the game or the machine.
7. **Authorized follow-up validation:** run and document a clean lap, invalid lap, out lap, reset, driver change, disconnect/reconnect, restart, duplicates and historical replay. Verify the persisted record, not just the HTTP 200 or the HUD. Obtain authorized access over a secure channel only once a concrete interface exists.

Current decision: keep Velocity as a pending integration. With identity and contract confirmed, evaluate an adapter; if only a documented export exists, evaluate deferred import. Do not promise bidirectional or real-time sync before those checks.

## Native Lua backup: real independence and limits

### Observed implementation

Local sources inspected: `client/native_laps.lua`, `client/journal.lua`, `client/RiceTrials.lua` and `client/tests/test_native_laps.py`. The history contains `b1ea32c`, which added the local native backup.

- `native_laps.lua` watches increments of `ac.getCar(0).lapCount`; it takes `previousLapTimeMs` and `isLastLapValid` when present, together with context IDs and capture time. The first reading and counter decreases are a reference, not new laps.
- It stores drafts in a journal **separate** from the main queue's: `native_laps.a.json` / `native_laps.b.json`, under the same state directory. It uses the same `journal.lua` module, A/B slots, sequence, checksum and confirmation read-back. There is a degraded fallback in `ac.storage` when file mode is unavailable; it does not carry the on-disk journal guarantee.
- In the observed code these are local drafts: they are not uploaded to `/api/laps` and `native_laps.lua` has no HTTP transport of its own. Matching with a route lap uses the internal session and closeness of raw time, with a 2000 ms tolerance; it is a local heuristic, not an unambiguous external identifier.
- It keeps up to 500 drafts; when trimming it prefers to drop a reconciled one and otherwise drops the oldest. It is not an unlimited historical archive.
- If the counter skips several laps, it captures the last available one and records how many were missed; it does not reconstruct the intermediate times. A missing time/field may stay empty. Whatever was not observed while the Lua was down is not magically recovered when it loads again.

### Independence matrix

| Failure or dependency | Scope of the observed backup |
|---|---|
| Geometric detector does not complete a lap | Can keep the native lap if AC increments the counter and the callback keeps running. |
| Main queue/journal | Its files and collection are separate. This does not isolate shared failures of library, directory, permissions, disk or process. |
| Network/Rice server down | Local capture does not require a network ACK, but it does not deliver that lap to the server on its own either. |
| Lua error | There is `pcall` around load/observation and some operations. There is no full isolation: before observing the lap, the same `script.update` runs other functions. An earlier error can prevent the observation. |
| Rice app disabled, not loaded, stuck, or CSP runtime stopped | Captures no new laps. It runs inside the same app, callback and runtime; it is not another process or another game. |
| AC stopped or hung, machine/disk down | Shares that failure domain. It is not an independent external timer. |
| Restart after a confirmed write | The design keeps the confirmed record; it does not guarantee rescuing a lap that was never saved. No guarantee against a power cut: there is no `fsync` in this implementation. |
| Penalties/evidence | Does not capture cone, off-course or slalom evidence on its own. It does not replace V2 analysis or enable official scoring. |

**Separating the queue or giving it its own submission improves logical decoupling and retries; it does not create a separate process, another game or another runtime.** Nor does it turn that flow into a Velocity integration. A server-side native receiver and queue need their own validation and are not certified here. The parallel submission integration must update the transport description above once it is reviewed and tested; the isolation limitation remains.

### Verification run

From `client/tests`, the following was run:

```text
/home/lucas/rr-eval/venvs/rice-tests/bin/python -m unittest test_native_laps -v
Ran 11 tests in 0.026s
OK
```

It covers simulated persistence across Lua runtimes, basic deduplication, counter reset, missing/invalid values, write failure and reconciliation. The integration test confirms that the observed code does not publish the native draft to `/api/laps`. These are Linux tests with Lupa and CSP/storage/network doubles, not an AC/Windows run or a Velocity test. No field test or test against the user's infrastructure was run in this research.

## Sources

[1] https://www.velocityprosims.com | Velocity Pro Sims: official site
[2] https://velocitysimlounge.com/blog/houston-610-sim-race | Velocity Sim Racing Lounge: 610 Challenge
[3] https://api.simracing.co.uk/docs/v0.1 | Sim Racing Limited VMS API Reference
[4] https://www.simracing.co.uk/downloads.php | Sim Racing Limited: Downloads
