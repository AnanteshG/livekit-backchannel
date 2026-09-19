import pytest

from backchannel.metrics import percentile, run_metrics, summarize


def test_missing_audio_is_not_zero_and_bc_is_not_response():
    result = run_metrics([{'t': 2, 'kind': 'bc_audio_received'}], 4)
    assert result['response_ms'] is None
    assert result['backchannels'] == 1
    assert result['bc_latency_ms'] == []


def test_actual_end_reference_and_decision_join():
    events = [{'t': 1, 'kind': 'bc_decision', 'decision': 2},
              {'t': 1.12, 'kind': 'bc_audio_received', 'decision': 2},
              {'t': 3.5, 'kind': 'eot_detected'},
              {'t': 3.9, 'kind': 'response_audio_received'}]
    m = run_metrics(events, 3)
    assert m['response_ms'] == pytest.approx(900)
    assert m['bc_latency_ms'] == pytest.approx([120])
    assert m['eot_ms'] == 500


def make(mode, value, pair='one', status='ok', warmup=False, sha='same'):
    metrics = run_metrics([], 0)
    metrics['response_ms'] = value
    return {'mode': mode, 'metrics': metrics, 'pair_id': pair, 'status': status,
            'warmup': warmup, 'audio_sha256': sha, 'scenario': 'one'}


def test_pairs_exclude_warmups_failures_and_missing_partners():
    s = summarize([make('baseline', 100), make('enabled', 120),
                   make('baseline', 10, 'warm', warmup=True),
                   make('enabled', 99, 'broken', status='failed'),
                   make('baseline', 130, 'missing')])
    assert s['complete_pairs'] == 1
    assert s['paired_mean_delta_ms'] == 20
    assert s['modes']['enabled']['failures'] == 1
    assert s['paired_mean_ci95_ms'] == [None, None]
    assert s['regression_flag'] is False


def test_mismatched_audio_cannot_be_paired():
    with pytest.raises(ValueError, match='different audio'):
        summarize([make('baseline', 10), make('enabled', 12, sha='different')])


def test_percentiles_interpolate_and_empty_is_null():
    assert percentile([], .95) is None
    assert percentile([1, 2, 3, 4], .5) == 2.5
    assert percentile([1, 2, 3, 4], .95) == pytest.approx(3.85)


def test_retried_nodes_and_multiple_turn_ends_do_not_mix_stage_clocks():
    events = [
        {'t': 1, 'kind': 'eot_detected'},
        {'t': 1.1, 'kind': 'llm_request', 'request': 1},
        {'t': 1.5, 'kind': 'llm_first_token', 'request': 1},
        {'t': 3.2, 'kind': 'eot_detected'},
        {'t': 3.3, 'kind': 'llm_request', 'request': 2},
        {'t': 3.4, 'kind': 'llm_request', 'request': 3},
        {'t': 3.8, 'kind': 'llm_first_token', 'request': 2},
        {'t': 4, 'kind': 'response_audio_received'},
        {'t': 5, 'kind': 'eot_detected'},
        {'t': 5.5, 'kind': 'llm_first_token', 'request': 3},
    ]
    metrics = run_metrics(events, 3)
    assert metrics['eot_ms'] == pytest.approx(200)
    assert metrics['llm_ttft_ms'] == pytest.approx(500)
