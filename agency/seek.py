"""Read SEEK directly, so Scout gets real postings instead of web-search ads.

    python3 agency/seek.py search "power bi" [--days 3] [--distance 10] [--pages 1]
    python3 agency/seek.py job <id>        # plain-text job description
"""
import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
BASE = "https://au.seek.com"
WORKTYPES = "242,244"  # full time, contract (Scout still drops contracts under 12 months)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-AU"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def search(keywords, where, distance, days, pages):
    for page in range(1, pages + 1):
        q = urllib.parse.urlencode({
            "siteKey": "AU-Main", "keywords": keywords, "where": where,
            "distance": distance, "daterange": days, "worktype": WORKTYPES,
            "page": page, "pageSize": 30,
        })
        rows = json.loads(get(f"{BASE}/api/jobsearch/v5/search?{q}")).get("data", [])
        for j in rows:
            loc = "; ".join(l.get("label", "") for l in j.get("locations", []))
            print("\t".join([
                j["id"], j.get("title", ""), j.get("companyName") or j.get("advertiser", {}).get("description", ""),
                loc, ",".join(j.get("workTypes", [])), j.get("listingDateDisplay", ""),
                f"{BASE}/job/{j['id']}",
            ]))
        if len(rows) < 30:
            break


def jd_text(page_html):
    """The description sits in the page's embedded JSON as escaped HTML."""
    blobs = re.findall(r'"content":"((?:\\.|[^"\\])*)"', page_html)
    if not blobs:
        return ""
    raw = json.loads('"' + max(blobs, key=len) + '"')
    text = re.sub(r"</(p|li|ul|ol|h\d)>|<br\s*/?>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def job(job_id):
    page = get(f"{BASE}/job/{job_id}")
    title = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    print(html.unescape(title.group(1)).strip() if title else f"job {job_id}")
    print(f"{BASE}/job/{job_id}\n")
    print(jd_text(page) or "(no description found)")


def selftest():
    sample = '{"x":"a","content":"<p>Build <b>Power BI</b> &amp; SQL</p><ul><li>Fabric</li></ul>"}'
    assert jd_text(sample) == "Build Power BI & SQL\nFabric", jd_text(sample)
    print("ok")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("keywords")
    s.add_argument("--where", default="Melbourne VIC 3000")
    s.add_argument("--distance", type=int, default=10)
    s.add_argument("--days", type=int, default=3)
    s.add_argument("--pages", type=int, default=1)
    sub.add_parser("job").add_argument("id")
    sub.add_parser("selftest")
    a = p.parse_args()
    if a.cmd == "search":
        search(a.keywords, a.where, a.distance, a.days, a.pages)
    elif a.cmd == "job":
        job(a.id)
    else:
        selftest()
    sys.stdout.flush()
