from wrangler import Frame

logs = Frame([
    {"entry": "2024-01-15 ERROR server cpu at 98% user=admin src=10.0.1.5"},
    {"entry": "Jan 16 2024 WARN disk usage high /dev/sda1 user=deploy"},
    {"entry": "2024/01/16 INFO deployed v2.3.1 to prod user=ci-bot"},
    {"entry": "17 January 2024 ERROR OOM killed pid=3847 user=app"},
    {"entry": "2024-01-18 INFO backup completed 3.2GB user=cron"},
    {"entry": "01-19-2024 WARN ssl cert expires in 7 days"},
    {"entry": "2024-01-20 ERROR connection timeout to db-replica-3 user=app"},
])

# Branch1 of the extract
parsed = logs.extract("entry", {
    "timestamp": "the datetime",
    "level":     "the log level (ERROR/WARN/INFO)",
    "message":   "the main message, excluding timestamp and level",
    "user":      "the username if present, else None",
})

print(parsed)

# Branch1's second commit of the extract
parsed = logs.extract("entry", {
    "date": "the date",
    "time": "the time",
    "level": "the log level (ERROR/WARN/INFO)",
    "message": "the main message, excluding timestamp and level",
    "user": "the username if present, else None",
})

print(parsed)

# Branch1's third commit of the extract
parsed = logs.extract("entry", {
    "timestamp": "the datetime",
    "level":     "the log level (ERROR/WARN/INFO/DEBUG)",
    "message":   "the main message, excluding timestamp and level",
    "user":      "the username if present, else None",
})

print(parsed)


# Branch1 of the filter
errors = parsed.filter("level", "is an error or warning")
print(errors)

# Branch2 of the filter
recent = parsed.filter("timestamp", "in the last 3 days of the dataset")
print(recent)

# Branch3 of the filter
infra = parsed.filter("message", "related to infrastructure, not application logic")


# Branch3's second commit of the filter
severe = parsed.filter(
    "message", 
    "indicates a serious production issue, things that would page an on-call engineer are included", 
)

print(severe)

# Branch3's third commit of the filter
critical = parsed.filter(
    "message",
    "indicates a serious production issue",
    severity_hint="things that would page an on-call engineer"
)
print(critical)