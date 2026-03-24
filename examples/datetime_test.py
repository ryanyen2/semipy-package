from semipy import semiformal, semi, configure
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import seaborn as sns
from pathlib import Path

CACHE_DIR = '/Users/r4yen/Desktop/Research/semi-formal/repo/semipy-package/.semiformal-datetime-usecase'
_SESSION_SOURCE = str((Path(CACHE_DIR).resolve().parent / "examples").resolve())

configure(
    cache_dir=CACHE_DIR,
    session_source=_SESSION_SOURCE,
    verbose=True,
)

@semiformal
def infer_datetime_formatter(date_str: str) -> str:
    #< [Task] infer parse shape, then normalize label
    input_pattern = ... #> infer the input date regex/strptime pattern from the observed string format in this session.
    #< [Given] parser depends on session-specific pattern
    output_pattern = "%b %Y"
    #> [But] mismatched tokens raise parse warnings
    #> [Verify] avoid re.error: redefinition of group name 'S' as group 7
    #< [But] prefer strptime tokens over regex groups

    #< [Verify] stringify input before parsing attempt
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
            "01 01"
            "04/21/2025",
        ]
    }
)

data["formatted_signup_date"] = data["signup_date"].apply(infer_datetime_formatter)
# data["formatted_signup_date"] = semi(f"infer datetime formatter from {data['signup_date']}")
print(data["formatted_signup_date"].value_counts())



new_data = pd.DataFrame(
    {
        "signup_date": [
            "06/18/2025 11:30",
            "09-21-2025",
            "09-21-2025 11:30",
            "01-01-2025",
            "12/01/2025 11:30:00",
            "01/01/2025 11:30:00",
            "12/01/2025 11:30",
            "January 1 2025",
            "Sep 2025",
            "01.21.2025 11:30:00",
            "02.21.2025 11:30:00:00",
            "02.21.2025 11:30:00:00:00",
            
        ]
    }
)

new_data["formatted_signup_date"] = new_data["signup_date"].apply(infer_datetime_formatter)
print(new_data["formatted_signup_date"].value_counts())


