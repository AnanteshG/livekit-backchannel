# Backchannel Lab

An independent, cancellable listening-acknowledgement engine on **LiveKit Agents**, built for the Blue Machines SDE-1 assignment. Python owns the engine, voice worker, replay runner, metrics and web server. A small vanilla JavaScript dashboard compares paired runs and connects a microphone to LiveKit.

**Current evidence:** real prerecorded audio has passed through LiveKit Cloud, Deepgram STT, OpenAI LLM and OpenAI TTS. The receiver observed both normal responses and independent acknowledgements. The local dashboard, 32 automated tests, eight WAV fixtures and 40 simulated pairs also work. See `results/analysis.md` for the paired measurements and limitations. No public live deployment is claimed; the web server and worker run locally.

## Quick start

Python 3.11+ (tested with 3.12). From the repository root:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q
python -m backchannel.benchmark --repeats 5
python -m uvicorn backchannel.web:app --host 127.0.0.1 --port 8765 --no-proxy-headers
```

On macOS/Linux activate with `source .venv/bin/activate`; all other Python commands are the same. Open **http://127.0.0.1:8765**. No API key is required for the dashboard, fixture audio, or engine simulation. Keep commands in the repository root; the web assets and fixtures are intentionally kept next to the Python package.

`requirements-lock.txt` records the tested Python 3.12 environment, including transitive versions. To reproduce it, install that file before the editable project: `python -m pip install -r requirements-lock.txt`, then `python -m pip install -e . --no-deps`. Platform-specific wheels still come from the package index.

## Live microphone demo

Copy `.env.example` to `.env` and fill these values locally. Never paste secrets into a GitHub issue or commit them.

| Variable | Purpose |
| --- | --- |
| `LIVEKIT_URL` | LiveKit Cloud project WebSocket URL, or your own reachable server |
| `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Room tokens and agent dispatch |
| `DEEPGRAM_API_KEY` | Streaming speech recognition |
| `OPENAI_API_KEY` | Response LLM and response TTS |
| `DEMO_ACCESS_KEY` | Required when the dashboard is accessed beyond loopback |

Defaults: Deepgram `nova-3`, OpenAI `gpt-4o-mini`, `gpt-4o-mini-tts`, voice `alloy`. Model/voice overrides are in `.env.example`; keep them identical in both experiment arms. Both arms disable preemptive generation, use the same fixed endpointing and interruption settings, and start with identical instructions and empty conversation history.

Windows audio compatibility: room input is explicitly 16 kHz to match Deepgram and Silero, and response output remains OpenAI's native 24 kHz. Cloud session recording is disabled; our own event telemetry remains enabled. This avoids the extra SDK Soxr conversions implicated by a native `fft4g_cache.h` / `FFT_LEN == -1` assertion observed on Windows. Do not patch the LiveKit DLL or ignore native assertions. Stop the crashed worker and restart after updating. The included full benchmark predates this workaround and is labelled accordingly; its numbers must not be treated as a benchmark of the revised audio configuration.

In another terminal with the virtual environment active:

```powershell
python -m backchannel.worker download-files
python -m backchannel.worker dev
```

Keep the web server running, open **Live conversation**, enable or disable acknowledgements, and select **Start conversation**. Allow microphone access and use headphones. Wait for the agent-ready event before speaking. The acknowledgement switch also works mid-session and cancels pending audio when disabled. Ending the call releases the microphone and disconnects the room. Do not use the Agents `console` command for this demo: this integration intentionally needs a room and a separate backchannel track.

The server mints a short-lived, room-scoped token and dispatches the named `backchannel-lab` worker. Secrets remain server-side. The browser reports missing credentials, microphone errors and worker readiness timeout. Merely configuring credentials is not treated as evidence that a worker is running.

## Architecture

```text
Microphone / replay WAV --> LiveKit room --> AgentSession --> STT --> LLM --> TTS
                                                |                         |
                                      public states + nodes         response track
                                                |
                                      BackchannelEngine
                                      (one cancellable task)
                                                |
                                      cached PCM --> separate RTC audio track

Both tracks --> browser / replay receiver
Public node events --> bounded telemetry queue --> timestamped experiment timeline
```

The engine never calls `session.say()`, inserts a chat message, or acquires the normal response queue. It publishes a separate `backchannel` track through public `rtc.AudioSource`, `capture_frame`, `clear_queue`, and `wait_for_playout` APIs. It therefore cannot intentionally queue a response behind an acknowledgement. It can still consume CPU, network and listener attention: those costs need real measurement.

The adapter uses public `Agent.stt_node`, `llm_node`, `tts_node`, `on_user_turn_completed`, and session state events. No LiveKit source modifications, monkey patches, or private APIs are used in production code. The engine itself has no LiveKit dependency and is tested with controlled clocks and audio providers.

## Policy and cancellation

The default policy requires all of the following:

- The user has been continuously speaking for at least **2.4 seconds**.
- A transcript with at least **5 words** arrived within the last **2 seconds**.
- The agent is neither preparing nor speaking a normal response.
- At least **4.5 seconds** passed since the last acknowledgement attempt.
- Fewer than **3 acknowledgements** have played in this conversational turn.
- The estimated end-of-turn risk is below **0.55**.

Risk is a **heuristic, not a calibrated probability**: sentence-ending punctuation yields 0.85, otherwise 0.1; explicitly low STT confidence yields 1.0. Unknown confidence is not treated as reliable noise evidence. External risk signals can be supplied through `risk()`, but this submission does not claim to run a semantic EOT predictor. LiveKit VAD governs speech state; a 120ms silence threshold detects pauses while a separate 500ms endpointing delay helps avoid ending a turn on every pause. These settings are conservative starting points, not tuned optimal values.

A fixed 20Hz policy ticker creates at most **one** audio task. Transcript storms only update state. On speech stop, high EOT risk, normal response creation, disable, or shutdown, the engine increments a generation token, cancels the task, and clears queued PCM synchronously. After any audio-preparation await it rechecks the token and current state. Even a provider that catches cancellation cannot play stale audio. A predecessor must finish before another acknowledgement can start. A callback handles cancellation before the coroutine enters its `try/finally`.

Preparation has a 650ms timeout. Exceptions are recorded and isolated from the normal response. Attempts, including failures, consume cooldown to prevent retry storms. Production uses a pre-generated `mm-hmm` clip: no extra paid TTS call is placed on the hot path. Trade-offs are voice mismatch, repetition and less expressive language. Generated TTS is represented by the injectable async provider and slow/failing-provider tests; it is not a measured bonus comparison.

Clearing local PCM cannot retract packets already transmitted or audio buffered at the listener. The short queue and 20ms frames limit producer look-ahead, but do not promise zero audible cancellation tail. EOT collisions and response overlap are measured separately.

## Repeatable audio scenarios

Eight owned synthetic-speech fixtures are committed under `scenarios/audio/`. `manifest.json` stores SHA256, clip boundaries, speech segments and generation settings. A checksum mismatch aborts a run. Recordings are PCM16 mono at 16kHz, with leading and trailing silence. Both modes receive byte-identical audio.

| Scenario | Purpose |
| --- | --- |
| Short answer | No acknowledgement on a brief response |
| Long monologue | Acknowledgements during sustained speech |
| Approaching EOT | Suppress acknowledgements on completion signals |
| Mid-sentence pause | Cancel on pause without assuming the whole turn ended |
| Fast speaker | Dense speech and frequent transcript updates |
| Noisy audio | Seeded noise; conservative low-confidence suppression in simulation |
| Multiple opportunities | Cooldown and per-turn cap |
| Stop at decision | Abrupt clip cutoff around the policy eligibility boundary |

The Windows-only regeneration command uses the installed `System.Speech` voice:

```powershell
.\scripts\generate_audio.ps1
python scripts/prepare_audio.py
```

Other platforms replay the committed WAVs directly. Regeneration on a different Windows voice may change the hashes; keep a single manifest/audio set for an experiment. These synthetic voices do not establish natural human conversational quality. The abrupt-stop **simulation** aligns the event deliberately; real STT/VAD timing may move the decision, so inspect the measured run rather than assuming the race occurred.

## Two deliberately separate benchmark modes

### Engine simulation (included results)

```powershell
python -m backchannel.benchmark --repeats 5
```

Runs the real engine with a virtual clock and scripted transcript, speech-state, confidence and EOT signals derived from fixture boundaries. It verifies the WAV hashes but does **not** feed PCM through STT, LiveKit, an LLM, or TTS. Fixed seeded provider delays are shared between each pair. This is a reproducible policy/race test, not an end-to-end audio benchmark. The UI labels it prominently.

### Real LiveKit audio replay (requires credentials and running worker)

```powershell
python -m backchannel.replay --repeats 5
```

Publishes each recording as 20ms microphone frames into a fresh room, starts the same worker configuration, and receives both audio tracks. It uses one warmup pair, shuffles scenario/repetition order, randomizes A/B order within pairs, and uses sequential execution to avoid load from one arm influencing the other. Identical prompting, model settings, audio, track setup and telemetry are used in both arms; only the policy's enabled flag differs. The worker receives no fixture labels or scripted EOT signals.

`results/livekit.json` is written atomically after each run, including failures and raw events. Switch the dashboard data source to **LiveKit audio replay** afterward. Do not mix simulation and live runs. Five repeats are a small sample; use 20+ for tail analysis if budget allows. Provider version drift, cache effects and regional network conditions remain confounders, even with randomization.

## What the clocks measure

| Measurement | Start | End | Caveat |
| --- | --- | --- | --- |
| Actual response latency | Annotated source speech end on the replay pacing clock | First response-track PCM above RMS 0.008 at the receiver | Includes transport and endpointing; excludes hardware speaker latency |
| Backchannel latency | Engine decision | First backchannel-track PCM above threshold at receiver | Worker clock mapped to replay clock; uncertainty reported |
| EOT time | Annotated source speech end | Public user-turn-completed hook | Includes finalization and scheduling; not raw model inference time |
| LLM TTFT | Entry into LLM node | First nonempty text chunk | Includes plugin/network overhead |
| TTS first PCM | Entry into TTS node | First generated PCM frame | Includes waiting for text/chunking; not isolated TTS network time |
| STT finalization | Source speech end | Last observed final transcript event | Intermediate finals may occur before turn end |

Response latency uses the replay client's monotonic clock for both boundaries and never treats acknowledgement audio as the answer. Fixture speech boundaries are synthetic clip annotations with approximately a 10ms trailing margin. Input is paced against absolute deadlines; runs drifting more than 50ms are invalidated and the maximum lag is saved. That is a bounded **source-clock** measurement, not a recording of a physical microphone.

Five ping/pong samples align worker `perf_counter()` with receiver `perf_counter()`. The lowest-RTT sample supplies the midpoint offset; half that RTT is the reported uncertainty. Only worker events need this mapping; the primary response metric does not. Backchannel correlations use aligned submission events rather than arrival order of data packets. Browser `playing` events are shown as diagnostics and are not substituted for PCM onset measurements.

Sentence pauses can cause multiple detected turns and cancelled generations inside one fixture. LLM/TTS node events therefore carry request IDs. Each stage summary uses the last completed matching request before the first received response; EOT uses the last detected turn end before that response. These diagnostics do not form an additive latency breakdown or guarantee that both nodes belong to the same generation. All attempts remain in the timeline. A negative response latency means the agent answered before the fixture ended and is also counted as a premature response, not treated as a speed improvement.

P50/P95 use linear interpolation. Headline mode distributions use the **same complete matched pairs**. Failures, missing responses, telemetry drops, input pacing failures and unpaired successes are retained but excluded from the comparison. Paired mean differences get a deterministic bootstrap interval stratified by scenario only when every represented scenario has at least two complete pairs. A regression flag requires the lower 95% bound to exceed **30ms**. This is an exploratory flag, not a powered equivalence test. With few samples, do not interpret a small difference as proof of causation or no regression.

## Behaviour and what became slower

A potentially bad backchannel is one that starts in the final **400ms** of the user turn or after it, overlaps a normal response, or occurs without sufficient speech evidence. User overlap is expected for a listening acknowledgement and is reported as duration rather than automatically labelled bad. Audio intervals are estimated from received non-silent frames; low-energy phonemes and jitter can affect those estimates. Acknowledgement counts, per-run counts, cancellation events, EOT collisions, user overlap, response overlap, and premature responses are inspectable in the UI and JSON.

The included **simulation** has 40 complete pairs (80 runs), response P50 **898.8ms**, P95 **926.2ms** in both modes, and backchannel P50 **120ms**. There are **65** audible simulated acknowledgements, **10** cancellations and **5** near-EOT collisions. The collisions occur in the mid-sentence-pause scenario's final segment and expose the limits of duration/heuristic gating. Zero simulated response delta is imposed by the shared provider schedule; it does not measure CPU contention or provider variance. See `results/analysis.md`.

The real replay contains **15 complete pairs**. Baseline/enabled response P50 is **4,579.3/4,328.6 ms**; P95 is **5,859.8/6,555.1 ms**. The paired mean difference is **+255.3 ms**. The confidence interval is withheld because the pause scenario has only one complete pair. These are small-sample measurements on a shared development laptop, not proof of causation or equivalence. See `results/analysis.md` for stage metrics, collisions, retained failures and the setup-failure retry. Naturalness still needs blinded human listening evaluations.

## Reference-project design review

A local interview-platform reference uses LiveKit as transport with a Pipecat/ADK conversation pipeline. Its relevant pattern is **incoming** backchannel tolerance: brief user acknowledgements should not interrupt bot speech, while sustained user speech should. It also cancels interrupted inference, preserves history reflecting what was actually spoken, and logs latency stages.

This assignment implements **outgoing** agent acknowledgements while the user speaks. The reference's state-aware gating, cancellation discipline and stage timing informed the design. Its application-specific pipeline, credentials, prompts and private transport internals were not copied. A detailed private review was provided separately to the user. This public implementation is deliberately small and built on public LiveKit Agents APIs.

## Tests and operations

```powershell
python -m pytest -q
python -m ruff check backchannel tests scripts
```

Tests cover short/long speech, duplicate event storms, cooldown/repetition, pending and active audio cancellation, EOT risk, normal-response priority, slow/failed TTS, rapid transitions, stale/noisy transcripts, shutdown, non-cooperative providers, public adapter hooks, PCM forwarding, pair statistics, fixture integrity, token scoping and access protection. GitHub Actions runs tests and simulation on Python 3.12. Tests with fake transports do not certify cloud connectivity.

For containers, fill `.env` including `DEMO_ACCESS_KEY`, then `docker compose up --build`. The dashboard binds only to host loopback; the worker connects outbound to LiveKit. Docker is provided for deployment but was not executed in the Windows build environment. For hosting, run the dashboard and worker as separate long-lived services, serve the UI over HTTPS for microphone access, supply credentials through the host's secret manager, configure an access key, and retain results on persistent storage. A static site host alone cannot run the agent or securely mint tokens.

Before public production use: implement user authentication, shared rate limiting and quotas; use dedicated worker capacity; calibrate a semantic EOT model; collect diverse consented human audio; measure browser AudioWorklet/loopback playout; validate multilingual voices and noise; apply retention/redaction to transcripts; expose telemetry-drop/error metrics; and conduct longer randomized trials. The current bounded in-memory timeline retains 20,000 events and the telemetry queue 512; dropped events invalidate benchmark data. The local token rate limit is not a distributed abuse-control system.

## Public API references

- [Agent nodes and hooks](https://docs.livekit.io/agents/logic/nodes/)
- [Session events](https://docs.livekit.io/reference/agents/events/)
- [RTC AudioSource](https://docs.livekit.io/reference/python/livekit/rtc/audio_source.html)
- [Voice agent quickstart](https://docs.livekit.io/agents/start/voice-ai/)

Pinned Python SDK: LiveKit Agents 1.8.2. Vendored browser SDK version and SHA256 are in `web/vendor/VERSION.txt`, with its upstream license.

The dashboard opens on **Approach**, explaining architecture, policy choices, cancellation races, benchmark fairness and limitations before linking to Experiment and Live conversation. Its evidence summary reads the selected result file instead of duplicating numbers.

### Website online but waiting for worker

The Vercel web app serves the page and room tokens. The Python voice worker is a separate process; deploying the web app does not start it. If it runs on a laptop, closing it or putting that laptop to sleep makes the voice demo unavailable.

Start `python -m backchannel.worker dev` from the repository with its environment configured, wait for worker registration, then reconnect from the website. For an unattended submission demo, deploy the worker on LiveKit Cloud or an always-running server using the same LiveKit project and `backchannel-lab` dispatch name. A local restart restores service only while that process remains running.
