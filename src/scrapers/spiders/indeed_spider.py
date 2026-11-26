import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

sys.path.append(str(Path(__file__).resolve().parents[2]))
from processing.extractor import JobExtractor

BASE_URL = "https://my.indeed.com"


class IndeedSpider:
    source_name = "Indeed"

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
    def _clean_jobkey(raw_val: str | None) -> str | None:
        if not raw_val:
            return None
        match = re.search(r"\b([a-f0-9]{10,24})\b", str(raw_val).strip(), re.IGNORECASE)
        return match.group(1) if match else None

    @classmethod
    def _normalize_job_url(cls, raw_url: str | None, jobkey: str | None = None) -> str | None:
        clean_jk = cls._clean_jobkey(jobkey)
        if clean_jk:
            return f"{BASE_URL}/viewjob?jk={clean_jk}"

        if not raw_url:
            return None

        candidate = str(raw_url).strip()
        if not candidate:
            return None

        full_url = urljoin(BASE_URL, candidate)
        parsed = urlparse(full_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None

        qs = parse_qs(parsed.query)
        if "jk" in qs and qs["jk"]:
            query_jk = cls._clean_jobkey(qs["jk"][0])
            if query_jk:
                return f"{BASE_URL}/viewjob?jk={query_jk}"

        return full_url

    @classmethod
    def _extract_jobkey_from_card(cls, card, title_el) -> str | None:
        for el in (title_el, card):
            if not el:
                continue
            for attr in ("data-jk", "id"):
                jk = cls._clean_jobkey(el.get(attr))
                if jk:
                    return jk
        return None

    @classmethod
    def _build_verified_record(
        cls,
        *,
        title: str,
        raw_url: str | None,
        jobkey: str | None = None,
        location: str,
        skills: list[str],
        salary_range: str | None,
    ) -> dict | None:
        clean_title = (title or "").strip()
        verified_url = cls._normalize_job_url(raw_url, jobkey=jobkey)
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
        print(f"Fetching Indeed listings for '{search_keyword}' in '{location}'...")

        jobs = self._fetch_from_rss(search_keyword, location, limit=limit)
        if jobs:
            print(f"Successfully fetched {len(jobs)} verified jobs from Indeed RSS.")
            return jobs

        jobs = self._fetch_from_html(search_keyword, location, limit=limit)
        if jobs:
            print(f"Successfully scraped {len(jobs)} verified jobs from Indeed HTML.")
            return jobs

        print("Indeed blocked the request or returned 0 verified listings. Returning empty list.")
        return []

    def _fetch_from_rss(self, keyword: str, location: str, limit: int = 10):
        rss_url = f"{BASE_URL}/rss?q={quote_plus(keyword)}&l={quote_plus(location)}"
        try:
            resp = self.session.get(rss_url, timeout=self.timeout)
            if resp.status_code != 200 or "<rss" not in resp.text.lower():
                return []

            root = ET.fromstring(resp.text)
            items = root.findall(".//item")
            results = []

            for item in items:
                if len(results) >= limit:
                    break

                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc_html = item.findtext("description") or ""
                desc_text = BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True)

                raw_text = f"{title}. {desc_text}"
                parsed = self.extractor.parse_job(raw_text)

                record = self._build_verified_record(
                    title=title,
                    raw_url=link,
                    location=location,
                    skills=parsed.get("skills", []),
                    salary_range=parsed.get("salary_range"),
                )
                if record:
                    results.append(record)

            return results
        except Exception as e:
            print(f"Indeed RSS fetch skipped ({e})")
            return []

    def _fetch_from_html(self, keyword: str, location: str, limit: int = 10):
        search_url = f"{BASE_URL}/jobs?q={quote_plus(keyword)}&l={quote_plus(location)}"
        try:
            resp = self.session.get(search_url, timeout=self.timeout)
            if resp.status_code != 200:
                print(f"Indeed HTML request failed with status {resp.status_code}.")
                return []

            # 1) Try embedded mosaic JSON if present in script tags
            json_jobs = self._extract_from_mosaic_json(resp.text, location, limit=limit)
            if json_jobs:
                return json_jobs

            # 2) Fallback to HTML DOM parsing
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select("div.job_seen_beacon, div.cardOutline, td.resultContent")
            if not cards:
                return []

            results = []
            for card in cards:
                if len(results) >= limit:
                    break

                title_el = card.select_one(
                    "h2.jobTitle a, a.jcs-JobTitle, span[id^='jobTitle-']"
                )
                company_el = card.select_one(
                    "[data-testid='company-name'], span.companyName, span[data-tn-element='companyName']"
                )
                loc_el = card.select_one(
                    "[data-testid='text-location'], div.companyLocation"
                )
                salary_el = card.select_one(
                    "[data-testid='attribute_snippet_testid'], div.salary-snippet-container, span.estimated-salary"
                )
                snippet_el = card.select_one(
                    "div.job-snippet, ul[style*='list-style-type'] li, div.underShelfContent"
                )

                title = title_el.get_text(" ", strip=True) if title_el else ""
                company = company_el.get_text(" ", strip=True) if company_el else ""
                job_loc = loc_el.get_text(" ", strip=True) if loc_el else location
                salary_txt = salary_el.get_text(" ", strip=True) if salary_el else ""
                snippet_txt = snippet_el.get_text(" ", strip=True) if snippet_el else ""

                href = title_el.get("href") if title_el and title_el.has_attr("href") else None
                jobkey = self._extract_jobkey_from_card(card, title_el)

                display_title = f"{title} ({company})" if company and title else title
                raw_text = f"{display_title} - {job_loc}. {salary_txt}. {snippet_txt}"
                parsed = self.extractor.parse_job(raw_text)

                record = self._build_verified_record(
                    title=display_title,
                    raw_url=href,
                    jobkey=jobkey,
                    location=job_loc,
                    skills=parsed.get("skills", []),
                    salary_range=parsed.get("salary_range") or (salary_txt or None),
                )
                if record:
                    results.append(record)

            return results
        except Exception as e:
            print(f"Error scraping Indeed HTML: {e}")
            return []

    def _extract_from_mosaic_json(self, html_text: str, default_location: str, limit: int = 10):
        match = re.search(
            r"window\.mosaic\.initialData\s*=\s*(\{.*?\});", html_text, re.DOTALL
        )
        if not match:
            return []

        try:
            data = json.loads(match.group(1))
        except Exception:
            return []

        results = []

        def walk(obj):
            if len(results) >= limit:
                return
            if isinstance(obj, dict):
                if "title" in obj and ("jobkey" in obj or "viewJobLink" in obj or "url" in obj):
                    title = str(obj.get("title") or "").strip()
                    company = str(obj.get("company") or "").strip()
                    job_loc = str(
                        obj.get("formattedLocation") or obj.get("location") or default_location
                    ).strip()
                    snippet = str(obj.get("snippet") or "").strip()
                    raw_link = obj.get("viewJobLink") or obj.get("url")
                    jobkey = obj.get("jobkey")

                    display_title = f"{title} ({company})" if company and title else title
                    parsed = self.extractor.parse_job(f"{display_title}. {snippet}")

                    record = self._build_verified_record(
                        title=display_title,
                        raw_url=raw_link,
                        jobkey=jobkey,
                        location=job_loc,
                        skills=parsed.get("skills", []),
                        salary_range=parsed.get("salary_range"),
                    )
                    if record:
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

        walk(data)
        return results


if __name__ == "__main__":
    spider = IndeedSpider()
    jobs = spider.fetch_jobs("software engineer", "Malaysia")
    print(f"Fetched {len(jobs)} jobs:", jobs)