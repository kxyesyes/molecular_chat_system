from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_batch_results_colspan_matches_declared_column_count():
    script = (PROJECT_ROOT / "src/web/static/js/reverse_target/results_renderer.js").read_text(
        encoding="utf-8"
    )

    assert "const SINGLE_RESULT_COLUMN_COUNT = 7;" in script
    assert "const BATCH_RESULT_COLUMN_COUNT = 7;" in script
    assert 'colspan="${BATCH_RESULT_COLUMN_COUNT}"' in script
    assert 'colspan="${BATCH_RESULT_COLUMN_COUNT - 2}"' in script
    assert 'colspan="8"' not in script
    assert 'colspan="5"' not in script
