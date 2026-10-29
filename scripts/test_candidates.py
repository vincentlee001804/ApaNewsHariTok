"""Test candidate RSS feeds for the bot. Usage: python scripts/test_candidates.py"""
import urllib.request
import xml.etree.ElementTree as ET

CANDIDATES = {
    # Sarawak-focused gaps
    "Utusan Borneo": "https://www.utusanborneo.com.my/feed/",
    "Sarawak Tribune (EN)": "https://www.newsarawaktribune.com/feed/",
    "Sarawak Gov (UKAS)": "https://ukas.sarawak.gov.my/feed/",
    # National English
    "The Star": "https://www.thestar.com.my/rss/latest",
    "Astro Awani": "https://www.astroawani.com/rss",
    "The Edge MY": "https://theedgemalaysia.com/rss",
    "NST": "https://www.nst.com.my/rss",
    "The Sun Daily": "https://www.thesun.my/rss",
    "The Vibes": "https://www.thevibes.com/feed",
    "Malaysia Gazette": "https://malaysiagazette.com/feed/",
    # Chinese-language (big Sarawak readership)
    "Sin Chew": "https://www.sinchew.com.my/feed/",
    "China Press": "https://www.chinapress.com.my/feed/",
    "Nanyang Siang Pau": "https://www.enanyang.my/rss",
    # Malay-language
    "Sinar Harian": "https://www.sinarharian.com.my/rss",
    "Oriental Daily": "https://www.orientaldaily.com.my/rss",
    "Berita Harian": "https://www.bharian.com.my/rss",
}

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"}

for name, url in CANDIDATES.items():
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
            data = r.read()
        try:
            root = ET.fromstring(data)
            items = root.findall(".//item") + root.findall(".//{*}entry")
            first = ""
            if items:
                t = items[0].findtext("title") or items[0].findtext("{*}title") or ""
                first = t.strip()[:70]
            print(f"OK    {name:22s} items={len(items):3d}  {first}")
        except ET.ParseError:
            print(f"XML?  {name:22s} HTTP 200 but unparseable ({len(data)} bytes)")
    except Exception as e:
        print(f"FAIL  {name:22s} {type(e).__name__}: {e}")
