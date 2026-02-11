from selection import Selection, Scatter, Line, GeoPlot

# ── Scatterplot: numeric axes, rect brush ──

scatter = Scatter(
    [{"mpg": 21, "hp": 110, "name": "Mazda RX4"},
     {"mpg": 22.8, "hp": 93, "name": "Datsun 710"},
     {"mpg": 14.3, "hp": 245, "name": "Duster 360"},
     {"mpg": 30.4, "hp": 52, "name": "Honda Civic"},
     {"mpg": 15.5, "hp": 150, "name": "Merc 450SL"}],
    x="hp", y="mpg"
)
scatter  # renders in Jupyter, drag to select


# ── Line chart: temporal x-axis, x-interval brush ──
# Scale sees date strings, auto-detects temporal,
# parses them, formats tick labels appropriately

line = Line(
    [{"date": "Jan 2024", "signups": 120},
     {"date": "Feb 2024", "signups": 89},
     {"date": "Mar 2024", "signups": 203},
     {"date": "Apr 2024", "signups": 156},
     {"date": "May 2024", "signups": 301}],
    x="date", y="signups"
)
line  # brush selects an x-interval of dates


# ── Geo plot: lat/lon, rect brush, auto coastline ──

geo = GeoPlot(
    [{"lat": 40.7, "lon": -74.0, "city": "NYC"},
     {"lat": 34.0, "lon": -118.2, "city": "LA"},
     {"lat": 41.8, "lon": -87.6, "city": "Chicago"},
     {"lat": 29.7, "lon": -95.3, "city": "Houston"},
     {"lat": 47.6, "lon": -122.3, "city": "Seattle"}],
    lat="lat", lon="lon", label="city"
)
geo  # shows points on rough map outline, drag to select


# ── Linked selection: same Selection across charts ──

data = [{"x": i, "y": i**2, "date": f"2024-{i+1:02d}-01", "value": i * 10}
        for i in range(12)]

shared = Selection(data)

s1 = Scatter(data, x="x", y="y")
s1.selection = shared

l1 = Line(data, x="date", y="value")
l1.selection = shared

# Brushing one updates the other (via shared Selection listeners)