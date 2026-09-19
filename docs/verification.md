# Verification record

Local Windows / Python 3.12 verification:

- 31 automated tests passed, including public LiveKit adapter hooks, RTC frame forwarding and correlated timing across retried requests.
- Ruff checks passed for Python source, tests and scripts.
- Python compilation and JavaScript syntax checks passed.
- Installed dependency compatibility check passed (`pip check`).
- Eight PCM16 recordings passed SHA256 and duration checks.
- Five repetitions per scenario/mode produced 40 complete simulated pairs.
- Browser checked: dashboard rendering, scenario selection, fifth-pair selection, expandable raw events, missing-live-results state and live-connection credential error.
- Browser LiveKit SDK served locally; no runtime CDN fetch required.
- GitHub push authorization worked on the requested remote and main branch.
- Real LiveKit Cloud room connection, Deepgram streaming recognition, OpenAI response generation and OpenAI speech generation verified through actual replay PCM.
- Real benchmark: 36 attempts including two warmups and one retry pair; 15 complete matched pairs. All failures retained. See `results/analysis.md` and `results/livekit.json`.
- Worker restarted after it was found stopped during the retry pair. The two worker-ready timeouts are retained; local process lifetime is an operational limitation, not hidden as successful deployment.
- GitHub Actions passed for source commit `41999b3`: https://github.com/AnanteshG/livekit-backchannel/actions/runs/35457516644
- Browser microphone connection reached `ready` and received an interim speech transcript; the diagnostic call was ended and microphone released. This verifies connection and input, not a blinded listening evaluation.
- User reported a native Windows `livekit_ffi.dll` Soxr FFT-cache assertion. The current public-API workaround uses 16 kHz room input, 24 kHz output, no pre-connect audio and no cloud session recording. The original paired dataset predates that audio configuration change and is labelled as such.
- After the workaround, four consecutive real replay sessions passed (two short baseline checks and two enabled long-turn checks, three acknowledgements in each long turn). The worker remained running after all four closed. Evidence: `results/windows-audio-smoke.json`. These checks do not establish long-term stability or comparative performance.

Not verified:

- Public hosting or a blinded naturalness evaluation. The dashboard and worker run on the local laptop and need to remain running. A microphone conversation requires browser microphone permission and the user's own speech.
- Container build/run. Docker CLI is installed but the Docker Desktop Linux engine pipe was unavailable. Docker/Compose files are provided as deployment configuration, not a tested deployment.

Two dependency deprecation warnings were emitted by the FastAPI/Starlette test client; they did not fail the tests. These are not cloud integration tests and do not establish speech naturalness or production latency.
