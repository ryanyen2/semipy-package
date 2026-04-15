from semipy import semiformal, semi, configure
import pandas as pd
from datetime import datetime
from pathlib import Path

# Portal session id matches this directory basename (same as VS Code workspace folder when you open `examples/`).
_EXAMPLES_ROOT = Path(__file__).resolve().parent
_SESSION_SOURCE = str(_EXAMPLES_ROOT)
CACHE_DIR = str(_EXAMPLES_ROOT / ".semiformal")

configure(
    cache_dir=CACHE_DIR,
    session_source=_SESSION_SOURCE,
    verbose=True,
)

@semiformal
def infer_datetime_formatter(date_str: str) -> str:
    #< [Task] Validate exact datetime parsing and skip invalid cases
    #< [Given] Slot category is statement with no output_names
    #< [Given] Observed date formats include slashes, dashes, times, and
    #< [Then] Used regex fullmatch derived from strptime pattern
    #< [Then] Allowed 1-2 digits for numeric date/time directives
    #< [When] Statement block contract requires returning None
    #< [Verify] Ran build_and_run_gist on sample 03/14/2025
    #< [But] Returning formatted string on success
    input_pattern = ... #> infer the input date regex/strptime pattern from the observed string format in this session.
    output_pattern = "%b %Y"
    #> [But] parse must match separators exactly, skip invalid cases
    return datetime.strptime(str(date_str), input_pattern).strftime(output_pattern)


data = pd.DataFrame(
    {
        "signup_date": [
            "03/14/2025",
            "03/20/2025",
            "04/05/2025",
            "04/18/2025",
            "05-01-2025",
            "05-12-2025 11:30",
            "08-18-2025 09:30:00",
            "06-18-2025 9:11",
            "June 21 2025",
            "July 09 2026",
            "Aug 2026",
            "01/01/2026",
            "04/21/2025",
        ]
    }
)

data["formatted_signup_date"] = data["signup_date"].apply(infer_datetime_formatter)
print(data["formatted_signup_date"].value_counts())



# new_data = pd.DataFrame(
#     {
#         "signup_date": [
#             "06/18/2025 11:30",
#             "09-21-2025",
#             "09-21-2025 11:30",
#             "01-01-2025",
#             "12/01/2025 11:30:00",
#             "01/01/2025 11:30:00",
#             "12/01/2025 11:30",
#             "January 1 2025",
#             "Sep 2025",
#             "01.21.2025 11:30:00",
#             "02.21.2025 09:12:00",
            
#         ]
#     }
# )

# new_data["formatted_signup_date"] = new_data["signup_date"].apply(infer_datetime_formatter)
# print(new_data["formatted_signup_date"].value_counts())


