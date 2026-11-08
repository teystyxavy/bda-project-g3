import numpy as np
import pandas as pd
import pytest

from bda_pipeline.dashboard_exploration import RankedPopulation, scorer_projection
from bda_pipeline import network


@pytest.mark.parametrize('model', [True, False])
@pytest.mark.parametrize('size', [0, 7, 1200])
def test_streamed_metrics_match_full_sort_with_ties_and_reordered_batches(model, size):
    rng = np.random.default_rng(459)
    frame = pd.DataFrame({'timestamp': pd.Timestamp('2022-09-01') + pd.to_timedelta(rng.integers(0, 12, size), unit='D'),
        'source_row_index': np.arange(size), 'is_laundering': rng.integers(0, 2, size)})
    values = rng.choice([0., .25, .5, 1.] if model else [0., 20., 50., 100.], size)
    population = RankedPopulation(250, model, 'HI-Large')
    for indices in np.array_split(rng.permutation(size), 17):
        population.add(frame.iloc[indices], values[indices])
    actual = population.finish()
    expected = scorer_projection(frame, values, 250, model, 'HI-Large')
    for key in ['queue', 'daily_by_k', 'histogram', 'pr_curve']:
        assert actual[key] == expected[key]
    for k in expected['metrics_by_k']:
        assert actual['metrics_by_k'][k] == pytest.approx(expected['metrics_by_k'][k])
    assert population.queue is None or len(population.queue) <= 250
    assert population.levels is None or len(population.levels) <= 4


def test_binary_network_compaction_matches_single_pass():
    from network_fixture import network_frame
    frame = network_frame()
    many = pd.concat([frame] * 37, ignore_index=True)
    expected = network.build_links([many])
    actual = network.build_links(many.iloc[indices] for indices in np.array_split(np.arange(len(many)), 93))
    assert actual[1:] == expected[1:]
    pd.testing.assert_frame_equal(actual[0].sort_values(['src', 'dst']).reset_index(drop=True),
                                  expected[0].sort_values(['src', 'dst']).reset_index(drop=True))


def test_early_cycle_pruning_preserves_both_directions_and_rotations():
    edges = pd.DataFrame([('c','a'), ('b','c'), ('a','b'), ('a','c'), ('c','b'), ('b','a'),
                          ('a','d'), ('d','e')], columns=['src','dst'])
    assert set(network.candidate_loops(edges).itertuples(index=False, name=None)) == {('a','b','c'), ('a','c','b')}
