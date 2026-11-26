import sys
from pathlib import Path
from urllib.parse import urlparse
from prefect import flow, task

sys.path.append(str(Path(__file__).resolve().parents[2]))

from src.database.connection import get_connection, init_db
from src.scrapers.spiders.myfuturejobs_spider import MyFutureJobsSpider
from src.scrapers.spiders.remoteok_spider import RemoteOKSpider
from src.scrapers.spiders.linkedin_spider import LinkedInSpider
from src.scrapers.spiders.jobstreet_spider import JobStreetSpider
from src.scrapers.spiders.indeed_spider import IndeedSpider


def _has_valid_http_url(url_val: str | None) -> bool:
    if not url_val:
        return False
    parsed = urlparse(str(url_val).strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _ensure_url_column(con) -> bool:
    """Ensure `job_listings` has a `url` column so verified links are persisted."""
    cols = [row[1].lower() for row in con.execute("PRAGMA table_info('job_listings')").fetchall()]
    if "url" in cols:
        return True
    try:
        con.execute("ALTER TABLE job_listings ADD COLUMN url VARCHAR")
        return True
    except Exception as e:
        print(f"Warning: could not add `url` column to job_listings: {e}")
        return False


def _load_existing_urls(con, has_url_col: bool) -> set[str]:
    if not has_url_col:
        return set()
    rows = con.execute("SELECT DISTINCT url FROM job_listings WHERE url IS NOT NULL").fetchall()
    return {str(r[0]).strip() for r in rows if r and r[0]}


def _store_verified_jobs(
    con,
    jobs: list[dict],
    *,
    source_label: str,
    default_location: str,
    start_id: int,
    has_url_col: bool,
    seen_urls: set[str],
) -> tuple[int, int]:
    inserted = 0
    next_id = start_id

    for job in jobs or []:
        if not isinstance(job, dict):
            continue

        title = str(job.get("title") or "").strip()
        verified_url = str(job.get("url") or job.get("source_url") or "").strip()

        if not title or "fallback" in title.lower():
            print(f"Skipped invalid or fallback title from {source_label}: '{title}'")
            continue

        if not _has_valid_http_url(verified_url):
            print(f"Skipped {source_label} job without verified URL: '{title}'")
            continue

        if verified_url in seen_urls:
            print(f"Skipped duplicate {source_label} URL: {verified_url}")
            continue

        location = str(job.get("location") or default_location).strip() or default_location
        skills = job.get("skills") if isinstance(job.get("skills"), list) else []
        salary_range = job.get("salary_range")

        if has_url_col:
            con.execute(
                """
                INSERT INTO job_listings (id, title, source, location, skills, salary_range, url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [next_id, title, source_label, location, skills, salary_range, verified_url],
            )
        else:
            con.execute(
                """
                INSERT INTO job_listings (id, title, source, location, skills, salary_range)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [next_id, title, source_label, location, skills, salary_range],
            )

        seen_urls.add(verified_url)
        inserted += 1
        next_id += 1
        print(f"Stored {source_label}: {title} -> {verified_url} | Skills: {skills}")

    return inserted, next_id


@task(name="Initialize Database")
def setup_database():
    init_db()
    print("Database and tables verified.")


@task(name="Scrape and Process Job Postings")
def run_extraction_pipeline():
    con = get_connection(read_only=False)
    processed_count = 0

    try:
        has_url_col = _ensure_url_column(con)
        seen_urls = _load_existing_urls(con, has_url_col)

        res = con.execute("SELECT MAX(id) FROM job_listings").fetchone()
        base_id = (res[0] if res and res[0] is not None else 0) + 1

        spider_runs = [
            (
                "MyFutureJobs",
                "Malaysia",
                lambda: MyFutureJobsSpider().fetch_jobs("python developer", location="Malaysia"),
            ),
            (
                "RemoteOK",
                "Remote",
                lambda: RemoteOKSpider().fetch_jobs(limit=5),
            ),
            (
                "LinkedIn",
                "Malaysia",
                lambda: LinkedInSpider().fetch_jobs("software engineer", "Malaysia"),
            ),
            (
                "JobStreet",
                "Malaysia",
                lambda: JobStreetSpider().fetch_jobs("software engineer", "malaysia"),
            ),
            (
                "Indeed",
                "Malaysia",
                lambda: IndeedSpider().fetch_jobs("software engineer", "Malaysia"),
            ),
        ]

        for source_label, default_loc, fetcher in spider_runs:
            print(f"--- Running {source_label} Spider ---")
            try:
                raw_jobs = fetcher()
            except Exception as e:
                print(f"{source_label} spider failed: {e}")
                raw_jobs = []

            added, base_id = _store_verified_jobs(
                con,
                raw_jobs,
                source_label=source_label,
                default_location=default_loc,
                start_id=base_id,
                has_url_col=has_url_col,
                seen_urls=seen_urls,
            )
            processed_count += added
    finally:
        con.close()

    return (
        f"Successfully processed and stored {processed_count} verified jobs from "
        f"multi-source pipeline (MyFutureJobs, RemoteOK, LinkedIn, JobStreet, Indeed)."
    )


@flow(name="Daily Kerja Scraper Pipeline")
def daily_pipeline():
    setup_database()
    result = run_extraction_pipeline()
    print(result)


if __name__ == "__main__":
    daily_pipeline()