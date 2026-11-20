from scripts.ingest import chunk_text


def test_empty_text_gives_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("\n  \n") == []


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("Hello.\nWorld.") == ["Hello.\nWorld."]


def test_chunks_respect_size_and_overlap() -> None:
    text = "\n".join(f"Paragraph {i} " + "x" * 300 for i in range(20))
    chunks = chunk_text(text, size=1000, overlap=100)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)
    for previous, current in zip(chunks, chunks[1:]):
        assert current.startswith(previous[-100:])


def test_oversized_paragraph_is_split() -> None:
    chunks = chunk_text("y" * 5000, size=1000, overlap=100)
    assert all(len(c) <= 1000 for c in chunks)
    assert "".join(c[100:] if i else c for i, c in enumerate(chunks)).count("y") >= 5000
