-- sparc_warc article schema. Point the DSN at a frozen snapshot or restored copy.
-- Bound the page ID window before ranking. Websites provide a publisher stratum.
WITH articles AS (
    SELECT p.id AS page_id, p.url, p.warc_record_id, p.warc_date AS captured_at,
           m.title, m.language, m.published_date AS published_at, w.domain AS publisher,
           c.cleaned_text AS text
    FROM pages AS p
    JOIN metadata AS m ON m.page_id = p.id
    JOIN content AS c ON c.page_id = p.id
    LEFT JOIN websites AS w ON w.id = p.website_id
    WHERE p.id BETWEEN %(min_page_id)s AND %(max_page_id)s
      AND m.language = ANY(%(languages)s)
      AND trim(c.cleaned_text) <> ''
), ranked AS (
    SELECT *,
           row_number() OVER (
               PARTITION BY language, publisher, substr(coalesce(nullif(published_at, ''), captured_at), 1, 7)
               ORDER BY md5(CAST(page_id AS TEXT) || %(seed)s), page_id
           ) AS bucket_rank
    FROM articles
), sampled AS (
    SELECT *, row_number() OVER (
        PARTITION BY language
        ORDER BY bucket_rank, md5(CAST(page_id AS TEXT) || %(seed)s), page_id
    ) AS language_rank
    FROM ranked
)
SELECT 'article:p' || CAST(page_id AS TEXT) AS source_id, 'article' AS source_unit,
       'article:p' || CAST(page_id AS TEXT) AS event_id,
       page_id, url, warc_record_id, title, language, publisher, published_at, captured_at, text
FROM sampled
WHERE language_rank <= %(limit_per_language)s
ORDER BY language_rank, language, page_id;
