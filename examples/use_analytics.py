
from analytics import get_current_conditions, ask_weather_question, compare_cities


def main():
    print("=== Use case 1: Current conditions (partial program + fetch) ===")
    summary = get_current_conditions("Seattle")
    print(f"Seattle: {summary}")
    print()

    print("=== Use case 2: Weather question (fetch required to answer) ===")
    rainy = ask_weather_question("Seattle", "Is it raining?")
    print(f"Is it raining in Seattle? {rainy}")
    print()

    print("=== Use case 3: Compare cities (multiple fetches) ===")
    comparison = compare_cities(["Seattle", "Portland", "San Francisco"], "temperature")
    print(comparison)


if __name__ == "__main__":
    main()
