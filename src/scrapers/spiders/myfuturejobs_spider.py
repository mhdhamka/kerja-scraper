import json
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

sys.path.append(str(Path(__file__).resolve().parents[2]))
from processing.extractor import JobExtractor

BASE_URL = "https://www.myfuturejobs.gov.my"


class MyFutureJobsSpider:
    source_name = "MYFutureJobs"

    def __init__(self, timeout: int = 12):
        self.base_url = BASE_URL
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
                "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-MY,en;q=0.9,ms;q=0.8",
                "Referer": f"{BASE_URL}/",
                "Connection": "keep-alive",
            }
        )

    @staticmethod
    def _clean_job_id(raw_val: str | None) -> str | None:
        if not raw_val:
            return None
        match = re.search(r"\b([A-Za-z0-9_-]{6,32})\b", str(raw_val).strip())
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
        location: str | None,
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
            "location": (location or "").strip() or "Malaysia",
            "skills": list(skills or []),
            "salary_range": salary_range,
        }

    def fetch_jobs(
        self,
        search_keyword: str = "software engineer",
        location: str = "Malaysia",
        limit: int = 10,
    ):
        print(f"Fetching MYFutureJobs listings for keyword: '{search_keyword}'...")

        encoded_kw = quote_plus(search_keyword)
        search_url = f"{self.base_url}/search?q={encoded_kw}"

        try:
            response = self.session.get(search_url, timeout=self.timeout)
            if response.status_code != 200:
                print(f"Failed to reach MYFutureJobs. Status code: {response.status_code}")
                return []

            content_type = (response.headers.get("Content-Type") or "").lower()
            if "application/json" in content_type or response.text.lstrip().startswith(("{", "[")):
                json_jobs = self._extract_from_json_payload(response.text, location, limit=limit)
                if json_jobs:
                    print(f"Successfully fetched {len(json_jobs)} verified jobs from MYFutureJobs JSON.")
                    return json_jobs

            embedded_jobs = self._extract_from_embedded_scripts(response.text, location, limit=limit)
            if embedded_jobs:
                print(f"Successfully fetched {len(embedded_jobs)} verified jobs from MYFutureJobs scripts.")
                return embedded_jobs

            soup = BeautifulSoup(response.text, "html.parser")
            job_cards = soup.select(
                "div.job-card, article.job-card, div[data-testid='job-card'], div.job-listing"
            )
            if not job_cards:
                print("No direct HTML job cards found on MYFutureJobs. Returning empty list (no mock fallback).")
                return []

            scraped_jobs = []
            seen_urls = set()

            for card in job_cards:
                if len(scraped_jobs) >= limit:
                    break

                title_elem = card.select_one("h3 a, h2 a, h3, h2, a[href*='/job/']")
                link_elem = card.select_one("a[href*='/job/'], a[href]") if card else None
                desc_elem = card.select_one("p.description, div.description, p")
                loc_elem = card.select_one("[data-testid='job-location'], span.location, div.location")
                salary_elem = card.select_one("[data-testid='job-salary'], span.salary, div.salary")

                title = title_elem.get_text(" ", strip=True) if title_elem else ""
                raw_text = desc_elem.get_text(" ", strip=True) if desc_elem else ""
                job_loc = loc_elem.get_text(" ", strip=True) if loc_elem else location
                salary_txt = salary_elem.get_text(" ", strip=True) if salary_elem else ""

                href = None
                if link_elem and link_elem.has_attr("href"):
                    href = link_elem.get("href")
                elif title_elem and title_elem.has_attr("href"):
                    href = title_elem.get("href")

                job_id = card.get("data-job-id") or card.get("data-id")
                parsed_data = self.extractor.parse_job(f"{title}. {salary_txt}. {raw_text}")

                record = self._build_verified_record(
                    title=title,
                    raw_url=href,
                    job_id=job_id,
                    location=job_loc,
                    skills=parsed_data.get("skills", []),
                    salary_range=parsed_data.get("salary_range") or (salary_txt or None),
                )
                if record and record["url"] not in seen_urls:
                    seen_urls.add(record["url"])
                    scraped_jobs.append(record)

            print(f"Successfully scraped {len(scraped_jobs)} verified jobs from MYFutureJobs.")
            return scraped_jobs

        except Exception as e:
            print(f"Error connecting to MYFutureJobs: {e}")
            return []

    def _extract_from_embedded_scripts(self, html_text: str, default_location: str, limit: int = 10):
        results = []
        seen_urls = set()

        for match in re.finditer(r"<script[^>]*>(.*?)</script>", html_text, flags=re.DOTALL | re.IGNORECASE):
            if len(results) >= limit:
                break
            block = match.group(1).strip()
            if not block or ("title" not in block and "jobTitle" not in block):
                continue

            json_chunks = []
            if block.startswith(("{", "[")):
                json_chunks.append(block)
            for assign in re.finditer(r"window\.[A-Za-z0-9_]+\s*=\s*(\{.*?\});", block, flags=re.DOTALL):
                json_chunks.append(assign.group(1))

            for raw_json in json_chunks:
                if len(results) >= limit:
                    break
                parsed_items = self._extract_from_json_payload(raw_json, default_location, limit=limit - len(results))
                for item in parsed_items:
                    if item["url"] not in seen_urls:
                        seen_urls.add(item["url"])
                        results.append(item)
                        if len(results) >= limit:
                            break

        return results

    def _extract_from_json_payload(self, raw_json: str, default_location: str, limit: int = 10):
        try:
            payload = json.loads(raw_json)
        except Exception:
            return []

        results = []
        seen_urls = set()

        def walk(obj):
            if len(results) >= limit:
                return
            if isinstance(obj, dict):
                raw_title = obj.get("title") or obj.get("jobTitle") or obj.get("positionTitle")
                raw_id = obj.get("id") or obj.get("jobId") or obj.get("jobCode") or obj.get("referenceNumber")
                raw_url = obj.get("url") or obj.get("jobUrl") or obj.get("applyUrl") or obj.get("link")

                if raw_title and (raw_url or raw_id):
                    title = str(raw_title).strip()
                    loc_val = obj.get("location") or obj.get("state") or obj.get("city") or default_location
                    if isinstance(loc_val, dict):
                        job_loc = str(loc_val.get("name") or loc_val.get("label") or default_location).strip()
                    else:
                        job_loc = str(loc_val or default_location).strip()

                    desc_val = str(obj.get("description") or obj.get("summary") or obj.get("snippet") or "").strip()
                    salary_val = str(obj.get("salary") or obj.get("salaryRange") or "").strip()
                    parsed = self.extractor.parse_job(f"{title}. {salary_val}. {desc_val}")

                    record = self._build_verified_record(
                        title=title,
                        raw_url=raw_url,
                        job_id=raw_id,
                        location=job_loc,
                        skills=parsed.get("skills", []),
                        salary_range=parsed.get("salary_range") or (salary_val or None),
                    )
                    if record and record["url"] not in seen_urls:
                        seen_urls.add(record["url"])
                        results.append(record)

                for v in obj.values():
                    if len(results) >= limit:
                        break
                    walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    if len(results) >= limit:
                        break
                    walk(item)

        walk(payload)
        return results


if __name__ == "__main__":
    spider = MyFutureJobsSpider()
    jobs = spider.fetch_jobs("python developer")
    print("Scraped / Fetched Results:", jobs)