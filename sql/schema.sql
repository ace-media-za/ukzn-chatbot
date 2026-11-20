CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS corporaterelations (
    id           bigserial PRIMARY KEY,
    url          text        NOT NULL,
    title        text        NOT NULL,
    chunk_index  int         NOT NULL,
    content      text        NOT NULL,
    content_hash text        NOT NULL,  -- sha256 of the page's full text; unchanged pages are skipped
    embedding    vector(768) NOT NULL,  -- nomic-embed-text dimension
    scraped_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (url, chunk_index)
);

CREATE INDEX IF NOT EXISTS corporaterelations_embedding_idx
    ON corporaterelations USING hnsw (embedding vector_cosine_ops);

-- Same shape as corporaterelations; ukzn.ac.za pages.
CREATE TABLE IF NOT EXISTS main_ukzn (
    id           bigserial PRIMARY KEY,
    url          text        NOT NULL,
    title        text        NOT NULL,
    chunk_index  int         NOT NULL,
    content      text        NOT NULL,
    content_hash text        NOT NULL,
    embedding    vector(768) NOT NULL,
    scraped_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (url, chunk_index)
);

CREATE INDEX IF NOT EXISTS main_ukzn_embedding_idx
    ON main_ukzn USING hnsw (embedding vector_cosine_ops);
