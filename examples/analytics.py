
from __future__ import annotations

from typing import Any

from semipy import semiformal, semi


@semiformal("fetch current weather for a city and return a readable summary")
def get_current_conditions(city: str, **kwargs: Any) -> str:
    return semi(
        f"""Fetch current weather for city '{city}' (use a public API, e.g. Open-Meteo) and return a short
        readable summary: temperature, conditions if available, wind. Do not make up data."""
    )


@semiformal("fetch weather and answer a yes/no question about conditions")
def ask_weather_question(city: str, question: str, **kwargs: Any) -> bool:
    return semi(
        f"""Fetch current weather for '{city}' (use a public API), then answer the question: {question}
        Return True or False based on the fetched weather data."""
    )


@semiformal("fetch weather for multiple cities and compare")
def compare_cities(cities: list[str], metric: str, **kwargs: Any) -> str:
    cities_str = ", ".join(repr(c) for c in cities)
    return semi(
        f"""For each city in [{cities_str}], fetch current weather (use a public API). Compare them by
        '{metric}' and return a short summary (e.g. which is warmest, rankings)."""
    )
