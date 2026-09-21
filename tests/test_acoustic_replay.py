import pytest

from backchannel.acoustic_replay import summary


def run(pair, enabled, response, inference=None):
    return {
        'status': 'ok', 'warmup': False, 'pair_id': pair,
        'acoustic_enabled': enabled,
        'metrics': {
            'response_ms': response, 'eot_ms': response - 100,
            'llm_ttft_ms': response / 2, 'tts_first_ms': None,
        },
        'acoustic_metrics': {
            'inference_p50_ms': inference,
            'signal_to_receiver_p50_ms': 120 if enabled else None,
        },
    }


def test_summary_pairs_modes_and_uses_fractional_percentiles():
    runs = [
        run('a', False, 1000), run('a', True, 1010, 1),
        run('b', False, 2000), run('b', True, 2030, 3),
    ]
    report = summary(runs)
    assert report['complete_pairs'] == 2
    assert report['off']['response_p50_ms'] == 1500
    assert report['off']['response_p95_ms'] == pytest.approx(1950)
    assert report['on']['acoustic_inference_p50_ms'] == 2
    assert report['on']['tts_first_p50_ms'] is None
    assert report['paired_response_mean_delta_ms'] == 20
