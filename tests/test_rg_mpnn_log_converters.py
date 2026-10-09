from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_rg_mpnn_log_converters_are_safe_and_pandas_2_compatible():
    for name in ("log2csv_rgs.py", "log2csv_cls.py"):
        source = (ROOT / "src" / "activity" / "rg_mpnn" / "Utils" / name).read_text(
            encoding="utf-8"
        )
        assert "ast.literal_eval" in source
        assert "eval(" not in source.replace("literal_eval(", "")
        assert "df.append(" not in source
