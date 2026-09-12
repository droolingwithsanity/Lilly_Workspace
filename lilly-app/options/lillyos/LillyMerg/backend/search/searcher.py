import json
import re
import requests
from typing import Optional
from urllib.parse import quote_plus

USER_AGENT = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 LillyOS/1.0"


class SearchTool:
    def __init__(self):
        self._ddgs = None
        self._weather_cache = {}
        self._news_cache = {"data": [], "time": 0}

    def _ddg_text(self, query: str, max_results: int = 5) -> list:
        try:
            try:
                from duckduckgo_search import DDGS
            except ImportError:
                from ddgs import DDGS
            with DDGS() as ddgs:
                results = []
                for r in ddgs.text(query, max_results=max_results):
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                    })
                return results
        except Exception:
            pass
        return self._fallback_search(query, max_results)

    def _ddg_images(self, query: str, max_results: 5) -> list:
        try:
            try:
                from duckduckgo_search import DDGS
            except ImportError:
                from ddgs import DDGS
            with DDGS() as ddgs:
                results = []
                for r in ddgs.images(query, max_results=max_results):
                    results.append({
                        "title": r.get("title", ""),
                        "image": r.get("image", ""),
                        "url": r.get("url", ""),
                    })
                return results
        except Exception:
            pass
        return self._fallback_image_search(query, max_results)

    def _fallback_search(self, query: str, max_results: int) -> list:
        try:
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
            if resp.status_code != 200:
                return []
            results = []
            for match in re.finditer(
                r'<a rel="nofollow" class="result__a" href="([^"]+)".*?>(.*?)</a>.*?'
                r'<a class="result__snippet".*?>(.*?)</a>',
                resp.text, re.DOTALL,
            ):
                results.append({
                    "title": re.sub(r'<[^>]+>', '', match.group(2)).strip(),
                    "url": match.group(1),
                    "snippet": re.sub(r'<[^>]+>', '', match.group(3)).strip(),
                })
            return results[:max_results]
        except Exception:
            return []

    def _fallback_image_search(self, query: str, max_results: int) -> list:
        try:
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&iax=images&ia=images"
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
            if resp.status_code != 200:
                return []
            results = []
            for match in re.finditer(
                r'<img[^>]+src="([^"]+)"[^>]+alt="([^"]*)"',
                resp.text,
            ):
                src = match.group(1)
                if src.startswith("//"):
                    src = "https:" + src
                results.append({
                    "title": match.group(2),
                    "image": src,
                    "url": src,
                })
            return results[:max_results]
        except Exception:
            return []

    def web_search(self, query: str, max_results: int = 5) -> list:
        return self._ddg_text(query, max_results)

    def image_search(self, query: str, max_results: int = 4) -> list:
        return self._ddg_images(query, max_results)

    def get_weather(self, location: str = None) -> dict:
        try:
            if location:
                geo = requests.get(
                    f"https://geocoding-api.open-meteo.com/v1/search?name={quote_plus(location)}&count=1&language=en&format=json",
                    timeout=10,
                ).json()
                if geo.get("results"):
                    lat = geo["results"][0]["latitude"]
                    lon = geo["results"][0]["longitude"]
                    name = geo["results"][0]["name"]
                    country = geo["results"][0].get("country", "")
                else:
                    return {"error": "Location not found"}
            else:
                lat, lon, name, country = 40.7128, -74.0060, "New York", "US"

            w = requests.get(
                f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
                f"weather_code,wind_speed_10m&daily=temperature_2m_max,temperature_2m_min,"
                f"weather_code&timezone=auto",
                timeout=10,
            ).json()

            codes = {
                0: "☀️ Clear", 1: "🌤️ Mainly clear", 2: "⛅ Partly cloudy", 3: "☁️ Overcast",
                45: "🌫️ Foggy", 48: "🌫️ Depositing rime fog",
                51: "🌦️ Light drizzle", 53: "🌦️ Moderate drizzle", 55: "🌦️ Dense drizzle",
                61: "🌧️ Slight rain", 63: "🌧️ Moderate rain", 65: "🌧️ Heavy rain",
                71: "🌨️ Slight snow", 73: "🌨️ Moderate snow", 75: "🌨️ Heavy snow",
                80: "🌦️ Slight rain showers", 81: "🌦️ Moderate rain showers", 82: "🌦️ Violent rain showers",
                95: "⛈️ Thunderstorm", 96: "⛈️ Thunderstorm with slight hail", 99: "⛈️ Thunderstorm with heavy hail",
            }

            current = w.get("current", {})
            daily = w.get("daily", {})
            weather_code = current.get("weather_code", 0)

            return {
                "location": f"{name}, {country}" if country else name,
                "temperature": current.get("temperature_2m"),
                "feels_like": current.get("apparent_temperature"),
                "humidity": current.get("relative_humidity_2m"),
                "wind_speed": current.get("wind_speed_10m"),
                "condition": codes.get(weather_code, f"Code {weather_code}"),
                "high": daily.get("temperature_2m_max", [None])[0] if daily.get("temperature_2m_max") else None,
                "low": daily.get("temperature_2m_min", [None])[0] if daily.get("temperature_2m_min") else None,
            }
        except Exception as e:
            return {"error": str(e)}

    def get_news(self, topic: str = "technology") -> list:
        try:
            query = f"{topic} news 2026"
            results = self._ddg_text(query, 5)
            news = []
            for r in results:
                news.append({
                    "title": r["title"],
                    "url": r["url"],
                    "snippet": r["snippet"],
                })
            return news
        except Exception:
            return []

    def get_local_info(self, query: str, location: str) -> list:
        return self._ddg_text(f"{query} {location}", 3)

    def search_multi(self, query: str, location: str = None) -> dict:
        result = {"text": [], "images": [], "sources": []}

        text_results = self.web_search(query, 5)
        for r in text_results:
            result["text"].append(r)
            result["sources"].append({"title": r["title"], "url": r["url"]})

        image_results = self.image_search(query, 4)
        result["images"] = [img["image"] for img in image_results if img.get("image")]

        if location:
            local = self._ddg_text(f"{query} {location}", 2)
            for r in local:
                if r not in result["text"]:
                    result["text"].append(r)
                    result["sources"].append({"title": r["title"], "url": r["url"]})

        return result
