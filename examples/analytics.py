
from __future__ import annotations

from typing import Any

from semipy import semiformal, semi


@semiformal("fetch current weather for a city and return a readable summary")
def get_current_conditions(city: str, **kwargs: Any) -> str:
    return semi(
        f"""Call {{FETCH_WEATHER(city)}} for city '{city}' and return a short readable summary
        (temperature, conditions if available, wind). Do not make up data. Use the fetched result."""
    )


@semiformal("fetch weather and answer a yes/no question about conditions")
def ask_weather_question(city: str, question: str, **kwargs: Any) -> bool:
    return semi(
        f"""Call {{FETCH_WEATHER(city)}} for '{city}' and answer the question: {question}
        Return True or False based on the fetched weather data."""
    )


@semiformal("fetch weather for multiple cities and compare")
def compare_cities(cities: list[str], metric: str, **kwargs: Any) -> str:
    cities_str = ", ".join(repr(c) for c in cities)
    return semi(
        f"""For each city in [{cities_str}], call {{FETCH_WEATHER(city)}} to get current weather.
        Compare them by '{metric}' and return a short summary (e.g. which is warmest, rankings)."""
    )
