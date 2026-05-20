from app.services.relevance import job_matches_query
from app.services.yc_stage import batch_code_to_stage, extract_yc_batches, infer_stage_from_yc_text


def test_extract_yc_batches():
    assert extract_yc_batches("Bild AI (W25) is hiring") == ["W25"]
    assert "S24" in extract_yc_batches("TaxGPT (S24) seed stage")


def test_batch_code_to_stage():
    assert batch_code_to_stage("W25", reference_year=2026) == "pre_seed"
    assert batch_code_to_stage("S24", reference_year=2026) == "seed"
    assert batch_code_to_stage("W22", reference_year=2026) == "series_a"


def test_infer_stage_from_yc_text():
    assert infer_stage_from_yc_text("Wyvern (W22)", "DevOps role") == "series_a"


def test_relaxed_relevance():
    assert job_matches_query(
        "founding software engineer",
        "Founding Product Engineer",
        "Build the core product at our startup.",
        link_title="Bild AI (W25)",
        page_text="Founding Product Engineer role",
        relaxed=True,
    )
