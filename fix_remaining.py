# fix_remaining.py
"""
Fix remaining issues after migration:
- en:baby → Baby (non-food, not in OFF food-only JSON)
- Household Goods → en:household-products
"""

import pandas as pd

file_path = 'comprehensive_grocery_certifications.xlsx'
sheet_name = 'Sheet1'  # The sheet is called Sheet1, not Certifications

print("📂 Reading Excel file...")
df = pd.read_excel(file_path, sheet_name=sheet_name)
print(f"📊 Found {len(df)} rows")

# Show current state
print("\n📋 Current issues to fix:")
baby_rows = df[df['Category'] == 'en:baby']
household_rows = df[df['Category'] == 'Household Goods']
print(f"   en:baby: {len(baby_rows)} row(s)")
if len(baby_rows) > 0:
    for idx, row in baby_rows.iterrows():
        brand = row['Product_Brand'] if 'Product_Brand' in df.columns else 'Unknown'
        print(f"      Row {idx+2}: {brand}")
print(f"   Household Goods: {len(household_rows)} row(s)")
if len(household_rows) > 0:
    for idx, row in household_rows.iterrows():
        brand = row['Product_Brand'] if 'Product_Brand' in df.columns else 'Unknown'
        print(f"      Row {idx+2}: {brand}")

# Fix en:baby → Baby
df.loc[df['Category'] == 'en:baby', 'Category'] = 'Baby'

# Fix Household Goods → en:household-products
df.loc[df['Category'] == 'Household Goods', 'Category'] = 'en:household-products'

print("\n🔄 Saving changes...")
df.to_excel(file_path, sheet_name=sheet_name, index=False)

print("\n✅ Fix complete!")
print(f"   en:baby → Baby")
print(f"   Household Goods → en:household-products")

# Show updated categories
print("\n📊 Updated categories in Column B:")
for cat in sorted(df['Category'].dropna().unique()):
    count = df[df['Category'] == cat].shape[0]
    print(f"   - {cat}: {count} products")
