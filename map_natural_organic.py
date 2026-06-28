# map_natural_organic.py
import pandas as pd

NATURAL_ORGANIC_MAPPING = {
    # Baby Food
    'Beech-Nut Organic': 'en:baby-foods',
    'Earth\'s Best': 'en:baby-foods',
    'Gerber Organic': 'en:baby-foods',
    'Happy Baby': 'en:baby-foods',
    'Little Spoon': 'en:baby-foods',
    'NurturMe': 'en:baby-foods',
    'Once Upon a Farm': 'en:baby-foods',
    'Plum Organics': 'en:baby-foods',
    'Raised Real': 'en:baby-foods',
    'Sprout': 'en:baby-foods',
    'Yummy Spoonfuls': 'en:baby-foods',

    # Meal Kits/Delivery (these are prepared meals)
    'Blue Apron': 'en:meals',
    'Dinnerly': 'en:meals',
    'EveryPlate': 'en:meals',
    'Factor': 'en:meals',
    'Freshly': 'en:meals',
    'Green Chef': 'en:meals',
    'HelloFresh': 'en:meals',
    'Home Chef': 'en:meals',
    'Hungryroot': 'en:meals',
    'Marley Spoon': 'en:meals',
    'Purple Carrot': 'en:meals',
    'Sakara': 'en:meals',
    'Sun Basket': 'en:meals',
    'Veestro': 'en:meals',

    # Natural/Organic Grocery (general foods)
    'Amy\'s Kitchen': 'en:foods',
    'Annie\'s Homegrown': 'en:foods',
    'Arrowhead Mills': 'en:baking-ingredients',
    'Bob\'s Red Mill': 'en:baking-ingredients',
    'Cascadian Farm': 'en:foods',
    'Daily Harvest': 'en:frozen-foods',
    'Earthbound Farm': 'en:vegetables',
    'Eden Foods': 'en:foods',
    'Kashi': 'en:breakfast-cereals',
    'Muir Glen': 'en:canned-foods',
    'Nature\'s Path': 'en:breakfast-cereals',
    'Newman\'s Own': 'en:foods',
    'Stonyfield': 'en:dairies',

    # Store Brands
    '365 by Whole Foods Market': 'en:foods',
    'Good & Gather': 'en:foods',
    'Great Value Organic': 'en:foods',
    'Kirkland Organic': 'en:foods',
    'Market Pantry': 'en:foods',
    'O Organics': 'en:foods',
    'Open Nature': 'en:foods',
    'Signature SELECT': 'en:foods',
    'Simple Truth': 'en:foods',

    # Specialty
    'Imperfect Foods': 'en:foods',
    'Misfits Market': 'en:foods',
    'Thrive Market': 'en:foods',
}

df = pd.read_excel('comprehensive_grocery_certifications.xlsx', sheet_name='Sheet1')

updated_count = 0
for brand, category in NATURAL_ORGANIC_MAPPING.items():
    mask = (df['Category'] == 'Natural & Organic') & (df['Product_Brand'] == brand)
    if mask.any():
        df.loc[mask, 'Category'] = category
        updated_count += 1
        print(f"  {brand} → {category}")

print(f"\n✅ Updated {updated_count} products")

df.to_excel('comprehensive_grocery_certifications.xlsx', sheet_name='Sheet1', index=False)
print("💾 Saved!")
