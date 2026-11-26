import re
import sys
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

sys.path.append(str(Path(__file__).resolve().parents[2]))
from processing.extractor import JobExtractor

BASE_URL = "https://www.linkedin.com"
GUEST_JOBS_API = f"{BASE_URL}/jobs-guest/jobs/api/seeMoreJobPostings/search"


class LinkedInSpider:
    source_name = "LinkedIn"

    def __init__(self, timeout: int = 12):
        self.extractor = JobExtractor()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"{BASE_URL}/jobs/search",
                "Connection": "keep-alive",
            }
        )

    @staticmethod
    def _extract_job_id(raw_val: str | None) -> str | None:
        if not raw_val:
            return None
        text = str(raw_val).strip()
        urn_match = re.search(r"urn:li:jobPosting:(\d{5,20})", text)
        if urn_match:
            return urn_match.group(1)
        view_match = re.search(r"/jobs/view/(?:[^/?#]+-)?(\d{5,20})", text)
        if view_match:
            return view_match.group(1)
        digits_match = re.search(r"\b(\d{5,20})\b", text)
        return digits_match.group(1) if digits_match else None

    @classmethod
    def _normalize_job_url(cls, raw_url: str | None, entity_urn: str | None = None) -> str | None:
        if raw_url:
            candidate = str(raw_url).strip()
            if candidate:
                full_url = urljoin(BASE_URL, candidate)
                parsed = urlparse(full_url)
                if parsed.scheme in ("http", "https") and parsed.netloc:
                    # Strip tracking query params for a clean canonical link while preserving valid path
                    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                    if clean_url.rstrip("/") != BASE_URL:
                        return clean_url

        job_id = cls._extract_job_id(entity_urn) or cls._extract_job_id(raw_url)
        if job_id:
            return f"{BASE_URL}/jobs/view/{job_id}/"

        return None

    @classmethod
    def _build_verified_record(
        cls,
        *,
        title: str,
        raw_url: str | None,
        entity_urn: str | None = None,
        location: str,
        skills: list[str],
        salary_range: str | None,
    ) -> dict | None:
        clean_title = (title or "").strip()
        verified_url = cls._normalize_job_url(raw_url, entity_urn=entity_urn)
        if not clean_title or not verified_url:
            return None

        return {
            "title": clean_title,
            "url": verified_url,
            "source_url": verified_url,
            "source": cls.source_name,
            "location": (location or "").strip() or None,
            "skills": list(skills or []),
            "salary_range": salary_range,
        }

    def fetch_jobs(
        self,
        search_keyword: str = "software engineer",
        location: str = "Malaysia",
        limit: int = 10,
    ):
        print(f"Fetching LinkedIn listings for '{search_keyword}' in '{location}'...")

        endpoints = [
            f"{GUEST_JOBS_API}?keywords={quote_plus(search_keyword)}&location={quote_plus(location)}&start=0",
            f"{BASE_URL}/jobs/search?keywords={quote_plus(search_keyword)}&location={quote_plus(location)}",
        ]

        for url in endpoints:
            try:
                response = self.session.get(url, timeout=self.timeout)
                if response.status_code != 200:
                    continue

                jobs = self._parse_cards(response.text, fallback_location=location, limit=limit)
                if jobs:
                    print(f"Successfully scraped {len(jobs)} verified jobs from LinkedIn.")
                    return jobs
            except Exception as e:
                print(f"LinkedIn request error ({url}): {e}")

        print("LinkedIn guest wall/anti-bot triggered or 0 verified jobs found. Returning empty list.")
        return []

    def _parse_cards(self, html_text: str, fallback_location: str, limit: int = 10):
        soup = BeautifulSoup(html_text, "html.parser")
        job_cards = soup.select("div.base-search-card, li.jobs-search__results-list > div, li > div.base-card")
        if not job_cards:
            return []

        scraped_jobs = []
        seen_urls = set()

        for card in job_cards:
            if len(scraped_jobs) >= limit:
                break

            title_elem = card.select_one(
                "h3.base-search-card__title, a.base-card__full-link, h3.base-card__title"
            )
            company_elem = card.select_one(
                "h4.base-search-card__subtitle a, h4.base-search-card__subtitle, a.hidden-nested-link"
            )
            loc_elem = card.select_one(
                "span.job-search-card__location, span.base-search-card__metadata"
            )
            link_elem = card.select_one("a.base-card__full-link, a.base-search-card__full-link, a[href*='/jobs/view/']")

            title = title_elem.get_text(" ", strip=True) if title_elem else ""
            company = company_elem.get_text(" ", strip=True) if company_elem else ""
            job_loc = loc_elem.get_text(" ", strip=True) if loc_elem else fallback_location

            href = link_elem.get("href") if link_elem and link_elem.has_attr("href") else None
            entity_urn = card.get("data-entity-urn") or (
                link_elem.get("data-tracking-id") if link_elem else None
            )

            display_title = f"{title} ({company})" if company and title else title
            raw_text = f"Role: {display_title} in {job_loc}."
            parsed_data = self.extractor.parse_job(raw_text)

            record = self._build_verified_record(
                title=display_title,
                raw_url=href,
                entity_urn=entity_urn,
                location=job_loc,
                skills=parsed_data.get("skills", []),
                salary_range=parsed_data.get("salary_range"),
            )
            if record and record["url"] not in seen_urls:
                seen_urls.add(record["url"])
                scraped_jobs.append(record)

        return scraped_jobs


if __name__ == "__main__":
    spider = LinkedInSpider()
    jobs = spider.fetch_jobs("software engineer", "Malaysia")
    print(f"Fetched {len(jobs)} jobs:", jobs)