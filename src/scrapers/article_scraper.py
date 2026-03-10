from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


def extract_article_content(url: str, timeout: int = 10) -> Optional[str]:
    """
    Fetch the full article content from a news URL.
    Extracts the main article text by identifying common article containers.
    
    Returns the article text content, or None if extraction fails.
    """
    try:
        # Some sources (e.g. Sarawak Tribune) already provide clean article bodies via RSS.
        # For these, using the full HTML page can introduce unrelated text (sidebars, widgets).
        # In such cases, return None so callers fall back to the RSS summary/content instead.
        parsed = urlparse(url)
        if "sarawaktribune.com" in (parsed.netloc or ""):
            return None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, "lxml")
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "header", "footer", "aside"]):
            script.decompose()
        
        # Try to find article content using common selectors
        article_content = None
        
        # Common article container selectors for news sites
        selectors = [
            "article",
            ".td-post-content",  # common WordPress newspaper theme (e.g. Sarawak Tribune)
            ".article-content",
            ".article-body",
            ".post-content",
            ".entry-content",
            ".content",
            ".main-content",
            "#article-content",
            "#article-body",
            "#content",
            ".story-body",
            ".news-content",
        ]
        
        for selector in selectors:
            article = soup.select_one(selector)
            if article:
                article_content = article
                break
        
        # If no specific article container found, try to find the largest text block
        if not article_content:
            # Look for divs with substantial text content
            all_divs = soup.find_all("div")
            best_div = None
            max_text_length = 0
            
            for div in all_divs:
                text = div.get_text(strip=True)
                # Skip if too short or likely navigation/advertisement
                if len(text) > 200 and len(text) > max_text_length:
                    # Check if it looks like article content (has sentences)
                    if len(re.findall(r'[.!?]', text)) > 3:
                        best_div = div
                        max_text_length = len(text)
            
            if best_div:
                article_content = best_div
        
        if not article_content:
            # Fallback: get body text
            article_content = soup.find("body")
        
        if article_content:
            # Extract text and clean it up
            text = article_content.get_text(separator=" ", strip=True)
            
            # Remove excessive whitespace
            text = re.sub(r'\s+', ' ', text)
            
            # Remove common noise patterns
            text = re.sub(r'Advertisement.*?Advertisement', '', text, flags=re.IGNORECASE)
            text = re.sub(r'Click here.*?\.', '', text, flags=re.IGNORECASE)
            text = re.sub(r'Read more.*?\.', '', text, flags=re.IGNORECASE)
            
            # Limit length to avoid token limits (keep first 5000 characters)
            if len(text) > 5000:
                text = text[:5000] + "..."
            
            return text.strip() if text.strip() else None
        
        return None
        
    except Exception as e:
        print(f"Error fetching article from {url}: {e}")
        return None
