# Verification record

Local Windows / Python 3.12 verification:

- 30 automated tests passed, including public LiveKit adapter hooks and RTC frame forwarding.
- Ruff checks passed for Python source, tests and scripts.
- Python compilation and JavaScript syntax checks passed.
- Installed dependency compatibility check passed (`pip check`).
- Eight PCM16 recordings passed SHA256 and duration checks.
- Five repetitions per scenario/mode produced 40 complete simulated pairs.
- Browser checked: dashboard rendering, scenario selection, fifth-pair selection, expandable raw events, missing-live-results state and live-connection credential error.
- Browser LiveKit SDK served locally; no runtime CDN fetch required.
- GitHub push authorization worked on the requested remote and main branch.

Not verified:

- A real LiveKit room, cloud STT/LLM/TTS, microphone conversation, receiver timing under network conditions, or public hosting. Required service credentials were absent from the task environment and project.
- Container build/run. Docker CLI is installed but the Docker Desktop Linux engine pipe was unavailable. Docker/Compose files are provided as deployment configuration, not a tested deployment.

Two dependency deprecation warnings were emitted by the FastAPI/Starlette test client; they did not fail the tests. These are not cloud integration tests and do not establish speech naturalness or production latency.
