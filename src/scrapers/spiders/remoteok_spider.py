import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

sys.path.append(str(Path(__file__).resolve().parents[2]))
from processing.extractor import JobExtractor

BASE_URL = "https://remoteok.com"


class RemoteOKSpider:
    source_name = "RemoteOK"

    def __init__(self, timeout: int = 12):
        self.api_url = f"{BASE_URL}/api"
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
                "Accept": "application/json,text/plain,*/*",
                "Referer": f"{BASE_URL}/",
            }
        )

    @staticmethod
    def _clean_job_id(raw_id) -> str | None:
        if raw_id is None:
            return None
        match = re.search(r"\b(\d{3,12})\b", str(raw_id).strip())
        return match.group(1) if match else None

    @classmethod
    def _normalize_job_url(cls, raw_url: str | None, job_id=None, slug: str | None = None) -> str | None:
        for candidate_val in (raw_url, slug):
            if not candidate_val:
                continue
            candidate = str(candidate_val).strip()
            if not candidate:
                continue
            full_url = urljoin(BASE_URL, candidate)
            parsed = urlparse(full_url)
            if parsed.scheme in ("http", "https") and parsed.netloc and parsed.path not in ("", "/"):
                return full_url

        clean_id = cls._clean_job_id(job_id)
        if clean_id:
            return f"{BASE_URL}/remote-jobs/{clean_id}"

        return None

    @staticmethod
    def _format_salary(item: dict, parsed_salary: str | None) -> str:
        if parsed_salary and parsed_salary != "Not Specified":
            return parsed_salary

        sal_min = item.get("salary_min")
        sal_max = item.get("salary_max")
        if sal_min and sal_max:
            return f"{sal_min} - {sal_max}"
        if sal_min:
            return f"From {sal_min}"
        if sal_max:
            return f"Up to {sal_max}"
        return "Global/Remote Rate"

    @staticmethod
    def _html_to_text(raw_html: str) -> str:
        if not raw_html:
            return ""
        text = BeautifulSoup(unescape(raw_html), "html.parser").get_text(" ", strip=True)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _build_verified_record(
        cls,
        *,
        title: str,
        raw_url: str | None,
        job_id=None,
        slug: str | None = None,
        company: str | None = None,
        location: str | None = None,
        skills: list[str] | None = None,
        salary_range: str | None = None,
    ) -> dict | None:
        clean_title = (title or "").strip()
        verified_url = cls._normalize_job_url(raw_url, job_id=job_id, slug=slug)
        if not clean_title or not verified_url:
            return None

        display_title = f"{clean_title} ({company.strip()})" if company and company.strip() else clean_title

        return {
            "title": display_title,
            "url": verified_url,
            "source_url": verified_url,
            "source": cls.source_name,
            "location": (location or "").strip() or "Remote",
            "skills": list(skills or []),
            "salary_range": salary_range or "Global/Remote Rate",
        }

    def fetch_jobs(
        self,
        limit: int = 10,
        search_keyword: str | None = None,
    ):
        print("Fetching remote listings from RemoteOK API...")

        try:
            response = self.session.get(self.api_url, timeout=self.timeout)
            if response.status_code != 200:
                print(f"Failed to reach RemoteOK API. Status code: {response.status_code}")
                return []

            data = response.json()
            if not isinstance(data, list):
                print("Unexpected RemoteOK API payload format.")
                return []

            # The first element in RemoteOK's JSON array is legal/metadata text, so skip it
            job_entries = data[1:] if len(data) > 1 else []
            kw_lower = search_keyword.strip().lower() if search_keyword else ""

            scraped_jobs = []
            seen_urls = set()

            for item in job_entries:
                if len(scraped_jobs) >= limit:
                    break
                if not isinstance(item, dict):
                    continue

                position = str(item.get("position") or "").strip()
                company = str(item.get("company") or "").strip()
                location = str(item.get("location") or "Remote").strip()
                description_text = self._html_to_text(str(item.get("description") or ""))
                tags = item.get("tags") if isinstance(item.get("tags"), list) else []
                tags_text = " ".join(str(t) for t in tags)

                if kw_lower:
                    haystack = f"{position} {company} {location} {tags_text} {description_text}".lower()
                    if kw_lower not in haystack:
                        continue

                combined_text = f"{position}. {tags_text}. {description_text}".strip()
                parsed_data = self.extractor.parse_job(combined_text)

                # Merge extracted skills with explicit RemoteOK tags without duplicates
                merged_skills = []
                seen_skills = set()
                for sk in list(parsed_data.get("skills") or []) + [str(t).strip() for t in tags if str(t).strip()]:
                    key = sk.upper()
                    if key not in seen_skills:
                        seen_skills.add(key)
                        merged_skills.append(sk)

                salary_range = self._format_salary(item, parsed_data.get("salary_range"))
                raw_url = item.get("url") or item.get("apply_url")
                slug = item.get("slug")
                job_id = item.get("id")

                record = self._build_verified_record(
                    title=position,
                    raw_url=raw_url,
                    job_id=job_id,
                    slug=slug,
                    company=company,
                    location=location,
                    skills=merged_skills,
                    salary_range=salary_range,
                )
                if record and record["url"] not in seen_urls:
                    seen_urls.add(record["url"])
                    scraped_jobs.append(record)

            print(f"Successfully scraped {len(scraped_jobs)} verified jobs from RemoteOK.")
            return scraped_jobs

        except Exception as e:
            print(f"Error connecting to RemoteOK: {e}")
            return []


if __name__ == "__main__":
    spider = RemoteOKSpider()
    jobs = spider.fetch_jobs(limit=3)
    print("RemoteOK Results:", jobs)