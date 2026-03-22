from liteparse import LiteParse

parser = LiteParse()

# dataset from https://www.kaggle.com/datasets/dhiaznaidi/receiptdatasetssd300v2
result = parser.parse(
    "examples/data/receipts/images/038.jpg",
    ocr_enabled=True,
)
print(result.to_dict())