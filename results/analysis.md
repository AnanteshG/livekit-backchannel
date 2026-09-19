# Results and interpretation

Only engine-simulation results have been collected. No cloud-provider latency or live deployment is represented by these numbers.

| Metric | Baseline | Enabled |
| --- | ---: | ---: |
| Runs | 40 | 40 |
| Response P50 | 898.8 ms | 898.8 ms |
| Response P95 | 926.2 ms | 926.2 ms |
| Acknowledgements | 0 | 65 |
| Acknowledgement decision-to-audio P50 | unavailable | 120 ms |
| Near-EOT collisions | 0 | 5 |
| Cancellations | 0 | 10 |
| Response overlap | 0 ms | 0 ms |

The simulated normal-response schedule is identical by construction in each pair. Its zero delta and degenerate bootstrap interval are **not** evidence of real-world equivalence. The useful evidence here is repeatable behaviour of the asynchronous engine under controlled inputs.

The abrupt-stop case cancels at 2.66s after deciding at 2.60s, before the simulated acknowledgement becomes audible. The mid-sentence-pause case cancels active audio at a pause and still has one late acknowledgement in its final segment per repetition. This is a real policy limitation exposed by the test model. Hiding these collisions or tuning directly to eight fixture endings would overstate quality.

Live replay will use actual PCM through LiveKit and real STT/LLM/TTS. Until it runs, the assignment's central question remains empirically unanswered: does backchanneling make real conversation more natural without increasing actual response latency? The implemented harness and dashboard make that test available, but do not substitute for its missing evidence.
