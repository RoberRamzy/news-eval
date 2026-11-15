import logging
import os
from pathlib import Path

from .eval_io import read_rows, timestamp

log = logging.getLogger(__name__)


def sample_archive(args):
    import psycopg
    from psycopg.rows import dict_row

    sql = Path(args.sql).read_text(encoding="utf-8")
    with psycopg.connect(os.environ["RAG_EVAL_PG_DSN"], row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute(sql, {"snapshot": args.snapshot, "languages": args.languages,
                                 "limit_per_language": args.limit, "seed": args.seed,
                                 "min_page_id": args.min_page_id, "max_page_id": args.max_page_id})
            return cursor.fetchall()


def normalize_dates(article):
    article = dict(article)
    for field in ("published_at", "captured_at"):
        try:
            value = article[field]
            article[field] = timestamp(value.strip()).isoformat() if value else None
        except (KeyError, ValueError, AttributeError, TypeError):
            article[field] = None
    return article



def load_articles(args):
    if args.articles:
        log.info("Reading archive sample from %s", args.articles)
        rows = read_rows(args.articles)
    else:
        log.info("Sampling snapshot %s, page IDs %d–%d, languages %s",
                 args.snapshot, args.min_page_id, args.max_page_id, ", ".join(args.languages))
        rows = sample_archive(args)
    articles = [normalize_dates(article) for article in rows]
    languages = sorted({article["language"] for article in articles})
    log.info("Loaded %d rows across languages: %s", len(articles), ", ".join(languages))
    return articles
