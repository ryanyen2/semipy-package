from semipy import semiformal, semi
import pandas as pd
import numpy as np


df = pd.read_csv("examples/data/covid_19_clean_complete.csv")

df['Year'] = df['Date'].apply(lambda x: semi(f"Year of {x}"))
print(df['Year'].value_counts())


df['Continent'] = df['Country/Region'].apply(lambda x: semi(f"Continent of {x}"))
print(df['Continent'].value_counts())