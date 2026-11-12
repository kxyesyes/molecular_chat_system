from scripts import health_check


def test_empty_rag_index_is_unavailable(tmp_path, monkeypatch):
    index = tmp_path / "index"
    index.touch()
    monkeypatch.setenv("RAG_INDEX_PATH", str(index))
    assert not health_check.check_rag_index()[0]


def test_sample_check_includes_the_docking_demo_file(tmp_path, monkeypatch):
    samples = tmp_path / "data" / "samples"
    samples.mkdir(parents=True)
    for path in ("5.sdf", "MAGL_5zun.pdb"):
        (samples / path).write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(health_check, "PROJECT_ROOT", tmp_path)
    assert not health_check.check_sample_assets()[0]
    demo = samples / "PDE4A_apo_6FG5.pdb"
    demo.mkdir()
    assert not health_check.check_sample_assets()[0]
    demo.rmdir()
    demo.write_text("fixture", encoding="utf-8")
    assert not health_check.check_sample_assets()[0]
    (samples / "roflumilast.pdbqt").write_text("fixture", encoding="utf-8")
    assert health_check.check_sample_assets()[0]
