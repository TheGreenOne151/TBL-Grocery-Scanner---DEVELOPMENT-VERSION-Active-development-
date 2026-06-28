# check_natural_organic.py
import pandas as pd

df = pd.read_excel('comprehensive_grocery_certifications.xlsx', sheet_name='Sheet1')
nat_organic = df[df['Category'] == 'Natural & Organic']

print(f'Found {len(nat_organic)} products:')
print('=' * 50)

for idx, row in nat_organic.iterrows():
    brand = row.get('Product_Brand', 'Unknown')
    print(f'  {brand}')
