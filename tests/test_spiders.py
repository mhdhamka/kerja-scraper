import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.scrapers.spiders.indeed_spider import IndeedSpider
from src.scrapers.spiders.jobstreet_spider import JobStreetSpider
from src.scrapers.spiders.linkedin_spider import LinkedInSpider
from src.scrapers.spiders.myfuturejobs_spider import MyFutureJobsSpider
from src.scrapers.spiders.remoteok_spider import RemoteOKSpider


def _assert_valid_http_url(url_value: str | None):
    assert url_value, "Expected a non-empty listing URL"
    parsed = urlparse(str(url_value).strip())
    assert parsed.scheme in ("http", "https"), f"Expected http(s) URL, got: {url_value}"
    assert bool(parsed.netloc), f"Missing domain in URL: {url_value}"


def test_linkedin_spider_no_fallback_and_verified_url(monkeypatch):
    spider = LinkedInSpider()
    assert not hasattr(spider, "_get_fallback_listings")

    monkeypatch.setattr(
        spider.session,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network unavailable")),
    )
    jobs = spider.fetch_jobs("software engineer", "Malaysia")
    assert jobs == []

    record = LinkedInSpider._build_verified_record(
        title="Backend Engineer (Acme)",
        raw_url="https://www.linkedin.com/jobs/view/9876543210/?trk=public_jobs",
        location="Malaysia",
        skills=["PYTHON", "SQL"],
        salary_range=None,
    )
    assert record is not None
    assert "fallback" not in record["title"].lower()
    _assert_valid_http_url(record["url"])
    assert record["source_url"] == record["url"]


def test_myfuturejobs_spider_no_fallback_and_verified_url(monkeypatch):
    spider = MyFutureJobsSpider()
    assert not hasattr(spider, "_get_fallback_listings")

    monkeypatch.setattr(
        spider.session,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network unavailable")),
    )
    jobs = spider.fetch_jobs("python developer")
    assert jobs == []

    record = MyFutureJobsSpider._build_verified_record(
        title="Junior Python Developer",
        raw_url="/job/MFJ-123456",
        location="Kuching",
        skills=["PYTHON", "FASTAPI"],
        salary_range="RM 3,800 - RM 5,500",
    )
    assert record is not None
    assert "fallback" not in record["title"].lower()
    _assert_valid_http_url(record["url"])
    assert record["source_url"] == record["url"]


def test_jobstreet_and_indeed_no_fallback_and_verified_url(monkeypatch):
    jobstreet = JobStreetSpider()
    indeed = IndeedSpider()
    assert not hasattr(jobstreet, "_get_fallback_listings")

    monkeypatch.setattr(
        jobstreet.session,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network unavailable")),
    )
    monkeypatch.setattr(
        indeed.session,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network unavailable")),
    )

    assert jobstreet.fetch_jobs("software engineer", "malaysia") == []
    assert indeed.fetch_jobs("software engineer", "Malaysia") == []

    js_record = JobStreetSpider._build_verified_record(
        title="Software Engineer (TechMY)",
        raw_url="/job/88776655",
        location="Kuala Lumpur",
        skills=["PYTHON", "REACT"],
        salary_range="RM 5,000 - RM 7,500",
    )
    ind_record = IndeedSpider._build_verified_record(
        title="Software Engineer (DevCorp)",
        raw_url=None,
        jobkey="1a2b3c4d5e6f7a8b",
        location="Malaysia",
        skills=["PYTHON", "SQL"],
        salary_range=None,
    )

    assert js_record is not None
    _assert_valid_http_url(js_record["url"])
    assert ind_record is not None
    assert "jk=1a2b3c4d5e6f7a8b" in ind_record["url"]
    _assert_valid_http_url(ind_record["url"])


def test_remoteok_spider_integration():
    spider = RemoteOKSpider()
    # Verify the spider correctly instantiates the extractor and handles parsing logic
    assert hasattr(spider, "extractor")
    parsed = spider.extractor.parse_job(
        "Remote Python and DuckDB backend developer needed. Salary: $5,000 - $8,000"
    )

    assert "PYTHON" in parsed["skills"]
    assert "DUCKDB" in parsed["skills"]

    record = RemoteOKSpider._build_verified_record(
        title="Backend Engineer",
        raw_url="https://remoteok.com/remote-jobs/remoteok-backend-engineer-12345",
        company="RemoteCorp",
        location="Remote",
        skills=parsed["skills"],
        salary_range=parsed["salary_range"],
    )
    assert record is not None
    _assert_valid_http_url(record["url"])
    assert record["source_url"] == record["url"]