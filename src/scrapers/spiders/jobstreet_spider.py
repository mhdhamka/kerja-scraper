import json
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

sys.path.append(str(Path(__file__).resolve().parents[2]))
from processing.extractor import JobExtractor

BASE_URL = "https://www.jobstreet.com.my"


class JobStreetSpider:
    source_name = "JobStreet"

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
                "Accept-Language": "en-MY,en;q=0.9,ms;q=0.8",
                "Referer": f"{BASE_URL}/",
                "Connection": "keep-alive",
            }
        )

    @staticmethod
    def _clean_job_id(raw_val: str | None) -> str | None:
        if not raw_val:
            return None
        match = re.search(r"\b(\d{5,12})\b", str(raw_val).strip())
        return match.group(1) if match else None

    @classmethod
    def _normalize_job_url(cls, raw_url: str | None, job_id: str | None = None) -> str | None:
        if raw_url:
            candidate = str(raw_url).strip()
            if candidate:
                full_url = urljoin(BASE_URL, candidate)
                parsed = urlparse(full_url)
                if parsed.scheme in ("http", "https") and parsed.netloc:
                    return full_url

        clean_id = cls._clean_job_id(job_id)
        if clean_id:
            return f"{BASE_URL}/job/{clean_id}"

        return None

    @classmethod
    def _build_verified_record(
        cls,
        *,
        title: str,
        raw_url: str | None,
        job_id: str | None = None,
        location: str,
        skills: list[str],
        salary_range: str | None,
    ) -> dict | None:
        clean_title = (title or "").strip()
        verified_url = cls._normalize_job_url(raw_url, job_id=job_id)
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
        location: str = "malaysia",
        limit: int = 10,
    ):
        print(f"Fetching JobStreet listings for '{search_keyword}' in '{location}'...")

        slug_keyword = re.sub(r"[^a-z0-9]+", "-", search_keyword.lower()).strip("-") or "software-engineer"
        search_url = f"{BASE_URL}/{slug_keyword}-jobs"
        if location and location.lower() != "malaysia":
            slug_loc = re.sub(r"[^a-z0-9]+", "-", location.lower()).strip("-")
            if slug_loc:
                search_url = f"{BASE_URL}/{slug_keyword}-jobs/in-{slug_loc}"

        try:
            response = self.session.get(search_url, timeout=self.timeout)
            if response.status_code != 200:
                print(f"Failed to reach JobStreet. Status code: {response.status_code}")
                return []

            json_jobs = self._extract_from_embedded_json(response.text, location, limit=limit)
            if json_jobs:
                print(f"Successfully fetched {len(json_jobs)} verified jobs from JobStreet.")
                return json_jobs

            soup = BeautifulSoup(response.text, "html.parser")
            job_cards = soup.select(
                "article[data-automation='job-card'], article[data-testid='job-card'], div[data-automation='jobCard']"
            )
            if not job_cards:
                print("No direct HTML cards found on JobStreet. Returning empty list (no mock fallback).")
                return []

            scraped_jobs = []
            for card in job_cards:
                if len(scraped_jobs) >= limit:
                    break

                title_elem = card.select_one(
                    "a[data-automation='job-title'], a[data-testid='job-card-title'], h3 a, a[href*='/job/']"
                )
                company_elem = card.select_one(
                    "a[data-automation='job-company-name'], span[data-automation='job-company-name'], [data-testid='job-card-company']"
                )
                loc_elem = card.select_one(
                    "[data-automation='jobLocation'], [data-testid='job-card-location']"
                )
                salary_elem = card.select_one(
                    "[data-automation='job-salary'], [data-testid='job-card-salary']"
                )
                snippet_elem = card.select_one(
                    "[data-automation='job-snippet'], [data-testid='job-card-teaser']"
                )

                title = title_elem.get_text(" ", strip=True) if title_elem else ""
                company = company_elem.get_text(" ", strip=True) if company_elem else ""
                job_loc = loc_elem.get_text(" ", strip=True) if loc_elem else location
                salary_txt = salary_elem.get_text(" ", strip=True) if salary_elem else ""
                snippet_txt = snippet_elem.get_text(" ", strip=True) if snippet_elem else ""

                href = title_elem.get("href") if title_elem and title_elem.has_attr("href") else None
                job_id = card.get("data-job-id") or card.get("data-automation-id")

                display_title = f"{title} ({company})" if company and title else title
                raw_text = f"{display_title} - {job_loc}. {salary_txt}. {snippet_txt}"
                parsed_data = self.extractor.parse_job(raw_text)

                record = self._build_verified_record(
                    title=display_title,
                    raw_url=href,
                    job_id=job_id,
                    location=job_loc,
                    skills=parsed_data.get("skills", []),
                    salary_range=parsed_data.get("salary_range") or (salary_txt or None),
                )
                if record:
                    scraped_jobs.append(record)

            print(f"Successfully scraped {len(scraped_jobs)} verified jobs from JobStreet.")
            return scraped_jobs

        except Exception as e:
            print(f"Error connecting to JobStreet: {e}")
            return []

    def _extract_from_embedded_json(self, html_text: str, default_location: str, limit: int = 10):
        """Extract job records from embedded Next.js/SEEK state if present."""
        script_matches = re.findall(
            r"<script[^>]*>(.*?)</script>", html_text, flags=re.DOTALL | re.IGNORECASE
        )
        results = []
        seen_urls = set()

        for block in script_matches:
            if len(results) >= limit:
                break
            if "jobId" not in block and "jobTitle" not in block and "title" not in block:
                continue

            json_candidates = []
            next_data = re.search(
                r"<script[^>]*id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
                html_text,
                flags=re.DOTALL | re.IGNORECASE,
            )
            if next_data:
                json_candidates.append(next_data.group(1))

            for assign_match in re.finditer(
                r"window\.[A-Za-z0-9_]+\s*=\s*(\{.*?\});", block, flags=re.DOTALL
            ):
                json_candidates.append(assign_match.group(1))

            for raw_json in json_candidates:
                if len(results) >= limit:
                    break
                try:
                    payload = json.loads(raw_json)
                except Exception:
                    continue

                self._walk_json_jobs(payload, default_location, limit, seen_urls, results)

        return results

    def _walk_json_jobs(self, obj, default_location: str, limit: int, seen_urls: set, results: list):
        if len(results) >= limit:
            return
        if isinstance(obj, dict):
            raw_title = obj.get("title") or obj.get("jobTitle")
            raw_id = obj.get("id") or obj.get("jobId")
            raw_url = obj.get("jobUrl") or obj.get("url") or obj.get("shareLink")

            if raw_title and (raw_url or raw_id):
                title = str(raw_title).strip()
                company_obj = obj.get("advertiser") or obj.get("company") or obj.get("employer")
                if isinstance(company_obj, dict):
                    company = str(company_obj.get("description") or company_obj.get("name") or "").strip()
                else:
                    company = str(company_obj or "").strip()

                loc_obj = obj.get("location") or obj.get("jobLocation")
                if isinstance(loc_obj, dict):
                    job_loc = str(loc_obj.get("label") or loc_obj.get("name") or default_location).strip()
                elif isinstance(loc_obj, list) and loc_obj:
                    first_loc = loc_obj[0]
                    job_loc = str(
                        first_loc.get("label") if isinstance(first_loc, dict) else first_loc
                    ).strip()
                else:
                    job_loc = str(loc_obj or default_location).strip()

                teaser = str(obj.get("teaser") or obj.get("abstract") or obj.get("snippet") or "").strip()
                salary_txt = str(obj.get("salary") or obj.get("salaryLabel") or "").strip()

                display_title = f"{title} ({company})" if company and title else title
                parsed = self.extractor.parse_job(f"{display_title} - {job_loc}. {salary_txt}. {teaser}")

                record = self._build_verified_record(
                    title=display_title,
                    raw_url=raw_url,
                    job_id=raw_id,
                    location=job_loc,
                    skills=parsed.get("skills", []),
                    salary_range=parsed.get("salary_range") or (salary_txt or None),
                )
                if record and record["url"] not in seen_urls:
                    seen_urls.add(record["url"])
                    results.append(record)

            for v in obj.values():
                if len(results) >= limit:
                    break
                self._walk_json_jobs(v, default_location, limit, seen_urls, results)
        elif isinstance(obj, list):
            for item in obj:
                if len(results) >= limit:
                    break
                self._walk_json_jobs(item, default_location, limit, seen_urls, results)


if __name__ == "__main__":
    spider = JobStreetSpider()
    jobs = spider.fetch_jobs("software engineer", "malaysia")
    print(f"Fetched {len(jobs)} jobs:", jobs)