# Known Limitations & Data Quirks

Behaviors that are correct given the current data but may surprise users.
None of these are bugs. Each is a data characteristic that would require a
data change to fix. Documented here so future sessions don't rediscover them.

## 1. Taxonomy plurality is strict
The Browse tab in Taxonomy mode requires the exact `en:` code. For example,
`en:coffee` is a valid taxonomy, but `en:coffees` is not (even though `coffees`
is a valid Column H product form). The autocomplete dropdown will correctly
show zero results for `en:coffees` and populate immediately when the user
types `en:coffee`. This is expected behavior.

## 2. "Procter & Gamble Tips" is a bad data row
A row exists in the workbook with the `Product_Brand` "Procter & Gamble Tips".
This appears to be an ingestion error (a scraped article heading rather than
a brand name). The normalizer turns it into the key `procter gamble tips`.
It is filed under `en:coffee` and shows up in that taxonomy search.
**Fix requires:** removing or correcting that specific row in the workbook.
Not scheduled.
