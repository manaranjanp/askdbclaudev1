import pytest

from app.retrieval.embedder import BgeSmallEmbedder, cosine


@pytest.mark.real_model
def test_bge_small_ranks_related_text_above_unrelated(request: pytest.FixtureRequest) -> None:
    # Opt-in: loads bge-small-en-v1.5 (downloads it on first run). Run with --real-model.
    if not request.config.getoption("--real-model"):
        pytest.skip("needs --real-model")
    embedder = BgeSmallEmbedder()

    query = embedder.embed_query("total revenue by region")
    related = embedder.embed(["sales amount per region"])[0]
    unrelated = embedder.embed(["zoo animal enclosure temperature"])[0]

    assert len(query) == 384
    assert cosine(query, related) > cosine(query, unrelated)
