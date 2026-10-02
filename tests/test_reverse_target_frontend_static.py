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


def test_frontend_passes_query_smiles_and_uses_abortable_detail_requests():
    renderer = (PROJECT_ROOT / "src/web/static/js/reverse_target/results_renderer.js").read_text(
        encoding="utf-8"
    )
    api_client = (PROJECT_ROOT / "src/web/static/js/reverse_target/api_client.js").read_text(
        encoding="utf-8"
    )
    ui_manager = (PROJECT_ROOT / "src/web/static/js/reverse_target/ui_manager.js").read_text(
        encoding="utf-8"
    )
    assert "onShowSimilar(${inlineJsArg(targetName)}, ${inlineJsArg(querySmiles)})" in renderer
    assert "new AbortController()" in renderer
    assert "signal }" in api_client
    assert '"status"' in ui_manager
    assert '"error"' in ui_manager


def test_frontend_keeps_refined_and_2d_fallback_results_distinguishable():
    main = (PROJECT_ROOT / "src/web/static/js/reverse_target/main.js").read_text(
        encoding="utf-8"
    )
    renderer = (PROJECT_ROOT / "src/web/static/js/reverse_target/results_renderer.js").read_text(
        encoding="utf-8"
    )
    ui_manager = (PROJECT_ROOT / "src/web/static/js/reverse_target/ui_manager.js").read_text(
        encoding="utf-8"
    )

    assert 'ranking_group: "3d_refined"' in main
    assert 'ranking_group: "2d_fallback"' in main
    assert "2D fallback" in renderer
    assert "pharm_refinement_status" in renderer
    assert "pharm_refinement_status" in ui_manager
    assert "pharm_error" in ui_manager
    assert "original_row_index" in ui_manager
