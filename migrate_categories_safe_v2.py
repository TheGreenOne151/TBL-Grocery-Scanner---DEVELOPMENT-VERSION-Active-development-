# migrate_categories_safe_v2.py
"""
SAFE migration script for Column B (Category) and Column H (Certification_Categories).
Preserves ALL other data (certifications, notes, dates, etc.).
Creates a backup before making any changes.
"""

import pandas as pd
from pathlib import Path
from datetime import datetime
import re

# ============================================================
# MAPPING: Your Categories → OFF Keys (Column B)
# ============================================================

CATEGORY_MAPPING = {
    'Baby Food': 'en:baby-foods',
    'Baking & Cooking': 'en:baking-ingredients',
    'Beverages': 'en:beverages',
    'Coffee, Tea and Cocoa': 'en:coffee',
    'Condiments & Sauces': 'en:condiments',
    'Dairy & Eggs': 'en:dairies',
    'Snacks': 'en:snacks',
    'Frozen Foods': 'en:frozen-foods',
    'Canned & Packaged Goods': 'en:canned-foods',
    'Breakfast Cereals': 'en:breakfast-cereals',
    'Candy & Chocolate': 'en:confectioneries',
    'Cookies & Crackers': 'en:biscuits',
    'Breads': 'en:breads',
    'Breakfast Foods': 'en:breakfast-cereals',
    'Health & Nutrition': 'en:nutritional-supplements',
    'Nut Butters': 'en:nut-butters',
    'Pet Food': 'en:pet-foods',
    'Cleaning & Household': 'en:household-products',
    'Cosmetics & Personal Care': 'en:cosmetics',
    'Baby': 'en:baby',
    'Paper Products': 'en:paper-products',
    'Feminine Hygiene Products': 'en:feminine-hygiene',
}

# ============================================================
# MAPPING: Certification Categories (Column H) → Standardized Terms
# ============================================================

CERT_CATEGORY_MAPPING = {
    # Baby products
    'diapers': 'Diapers',
    'baby pants': 'Baby clothing',
    'baby care': 'Baby care products',
    'wipes and bags': 'Baby wipes & bags',
    'bath and body': 'Bath & body care',
    'baby skincare': 'Baby skincare',
    'baby bath': 'Baby bath products',
    'baby lotion': 'Baby lotion',
    'baby oil': 'Baby oil',
    'baby powder': 'Baby powder',
    'baby cream': 'Baby cream',
    'baby shampoo': 'Baby shampoo',
    'baby wipes': 'Baby wipes',
    'baby formula': 'Infant formula',
    'baby food jars': 'Baby food (jars)',
    'baby food pouches': 'Baby food (pouches)',
    'pantry snacks for babies': 'Baby snacks',
    'baby food snack bars': 'Baby snacks',
    'cereals for babies': 'Baby cereals',
    'baby meals': 'Baby meals',

    # Beverages
    'beverages': 'Beverages',
    'water': 'Water',
    'mineral water': 'Mineral water',
    'iced coffee': 'Coffee (iced)',
    'coffee': 'Coffee',
    'tea': 'Tea',
    'cocoa': 'Cocoa',
    'cocoa powder': 'Cocoa powder',
    'plant-based beverages': 'Plant-based beverages',
    'vanilla': 'Vanilla',
    'ice cream': 'Ice cream',

    # Food
    'bread': 'Bread',
    'organic': 'Organic',
    'snacks': 'Snacks',
    'popcorn': 'Popcorn',
    'potatoes chips': 'Potato chips',
    'chips': 'Chips',
    'crisps': 'Crisps',
    'nuts': 'Nuts',

    # Dairy
    'dairy': 'Dairy',
    'yogurt': 'Yogurt',

    # Chocolate
    'chocolate': 'Chocolate',
    'cocoa': 'Cocoa',

    # Household
    'bathroom cleaner': 'Bathroom cleaner',
    'floor cleaner': 'Floor cleaner',
    'household cleaner': 'Household cleaner',
    'cleaning wipes': 'Cleaning wipes',
    'dish detergent': 'Dish detergent',
    'dish soap': 'Dish soap',
    'laundry': 'Laundry products',
    'stain remover': 'Stain remover',
    'degreaser': 'Degreaser',
    'carpet cleaner': 'Carpet cleaner',
    'bleach': 'Bleach',
    'cleaning products': 'Cleaning products',

    # Paper
    'toilet paper': 'Toilet paper',
    'paper towels': 'Paper towels',

    # Personal care
    'lip care': 'Lip care',
    'body care': 'Body care',
    'face care': 'Face care',
    'hair care': 'Hair care',
    'shampoo': 'Shampoo',
    'conditioner': 'Conditioner',
    'soap': 'Soap',
    'sun care': 'Sun care',
    'feminine hygiene': 'Feminine hygiene',
    'dental care': 'Dental care',
    'hand sanitizers': 'Hand sanitizers',
    'bath bubbles and salts': 'Bath products',
    'aromatherapy': 'Aromatherapy',
    'condoms/lubricants': 'Sexual wellness',
    'makeup': 'Makeup',
    'skincare': 'Skincare',

    # Hair
    'scalp cleanse': 'Scalp cleanser',
    'scalp detox': 'Scalp detox',
    'hydrating shampoo': 'Hydrating shampoo',
    'hair reparative treatment': 'Hair repair treatment',
    'hair repair conditioner': 'Hair repair conditioner',
    'hair glow oil': 'Hair glow oil',
    'leave-in hair conditioner': 'Leave-in conditioner',
    'pre-shampoo masque': 'Pre-shampoo masque',
    'hair styling': 'Hair styling',
    'shampoo/conditioner': 'Shampoo & Conditioner',

    # Sleep/wellness
    'sleep aids': 'Sleep aids',
    'sleep pillow spray': 'Sleep pillow spray',
    'sleep shower gel': 'Sleep shower gel',
    'sleep beauty soak': 'Sleep beauty soak',
    'sleep overnight cream': 'Sleep overnight cream',
    'sleep retinoid complex': 'Sleep retinoid complex',
    'sleep breathe in': 'Sleep breathe in',
    'sleep beauty oil': 'Sleep beauty oil',
    'sleep bath oil': 'Sleep bath oil',
    'sleep body whip': 'Sleep body whip',
    'sleep body cocoon': 'Sleep body cocoon',
    'sleep overnight cleanser': 'Sleep overnight cleanser',
    'anti-wrinkle moisturizer': 'Anti-wrinkle moisturizer',
    'skin moisturizing serum': 'Skin moisturizing serum',
    'eye wrinkle care': 'Eye wrinkle care',
    'eye wrinkle repair': 'Eye wrinkle repair',
    'skin cleansing pads': 'Skin cleansing pads',
    'pre-shower beauty wash': 'Pre-shower beauty wash',
    'leg care': 'Leg care',
    'hand and foot care': 'Hand & foot care',
    'body oils': 'Body oils',
    'neck and bust': 'Neck & bust care',
    'body moisturizers': 'Body moisturizers',
    'leg moisturizer': 'Leg moisturizer',
    'leg oil': 'Leg oil',
    'bronzing serum': 'Bronzing serum',
    'sun screen': 'Sunscreen',

    # Skin
    'skin oils': 'Skin oils',
    'skin serums': 'Skin serums',
    'anti-aging': 'Anti-aging',
    'skin creams': 'Skin creams',
    'skin moisturizers': 'Skin moisturizers',
    'face-lift treatments': 'Face-lift treatments',
    'cleansers': 'Cleansers',
    'toners': 'Toners',
    'face oils': 'Face oils',
    'eye treatments': 'Eye treatments',
    'body lotion': 'Body lotion',
    'hand cream': 'Hand cream',
    'body scrub': 'Body scrub',
    'fragrance roller': 'Fragrance roller',
    'facial creams': 'Facial creams',
    'day creams': 'Day creams',
    'night creams': 'Night creams',

    # Household bags
    'sandwich bags': 'Sandwich bags',
    'food storage bags': 'Food storage bags',
    'compostable food storage bags': 'Compostable food storage bags',
    'compostable kitchen trash bags': 'Compostable trash bags',
    'compostable drawstring kitchen bags': 'Compostable drawstring bags',
    'compostable bags with handles': 'Compostable bags with handles',
    'storage bags': 'Storage bags',
    'gloves': 'Gloves',

    # Health
    'mushrooms': 'Mushrooms',
    'supplements': 'Supplements',
    'women\'s multivitamin': 'Women\'s multivitamin',
    'frother': 'Frother',

    # Pet
    'peanut butter for dogs': 'Peanut butter (dog treats)',
    'dog treat toys': 'Dog treat toys',
    'dog chews': 'Dog chews',
    'canned cat food': 'Canned cat food',
    'pouch cat food': 'Cat food (pouch)',
    'freeze-dried cat food': 'Freeze-dried cat food',
    'cat supplements': 'Cat supplements',
    'cat treats': 'Cat treats',
    'freeze-dried dog food': 'Freeze-dried dog food',
    'dog food toppers': 'Dog food toppers',
    'canned dog food': 'Canned dog food',
    'dog supplements': 'Dog supplements',
    'dog treats': 'Dog treats',

    # General
    'nut butters': 'Nut butters',
    'peanut butter': 'Peanut butter',
    'peanut butter bars': 'Peanut butter bars',
    'liners': 'Liners',
    'pads': 'Pads',
    'feminine care': 'Feminine care',
}

def standardize_cert_categories(value):
    """
    Standardize a certification categories string.
    Splits by comma, maps each term, and joins back.
    """
    if pd.isna(value) or not isinstance(value, str):
        return value

    # Skip empty strings
    if not value.strip():
        return value

    # Split by common separators
    terms = re.split(r'[,;]', value)
    standardized = []

    for term in terms:
        term = term.strip().lower()
        if not term:
            continue

        # Check for exact match
        if term in CERT_CATEGORY_MAPPING:
            standardized.append(CERT_CATEGORY_MAPPING[term])
        else:
            # Try partial match (for terms not in mapping)
            mapped = False
            for key, mapped_value in CERT_CATEGORY_MAPPING.items():
                if key in term or term in key:
                    standardized.append(mapped_value)
                    mapped = True
                    break

            if not mapped:
                # Capitalize first letter of each word as fallback
                standardized.append(' '.join(word.capitalize() for word in term.split()))

    # Remove duplicates while preserving order
    seen = set()
    unique_terms = []
    for t in standardized:
        if t not in seen:
            seen.add(t)
            unique_terms.append(t)

    return ', '.join(unique_terms)


def safe_migrate_excel():
    """Safely migrate Column B and Column H"""

    file_path = Path('comprehensive_grocery_certifications.xlsx')

    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        print(f"   Current directory: {Path.cwd()}")
        return

    print(f"📂 Reading {file_path}...")

    try:
        df = pd.read_excel(file_path, sheet_name='Certifications')
        print(f"📊 Found {len(df)} rows and {len(df.columns)} columns")
    except Exception as e:
        print(f"❌ Error reading Excel file: {e}")
        return

    # Create timestamped backup
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = file_path.with_suffix(f'.xlsx.backup_{timestamp}')
    print(f"💾 Creating backup at {backup_path}")
    df.to_excel(backup_path, index=False)
    print(f"✅ Backup created: {backup_path}")

    # ============================================================
    # MIGRATE COLUMN B (Category)
    # ============================================================

    print("\n📋 Column B (Category) changes:")
    category_changes = []

    if 'Category' in df.columns:
        for idx, row in df.iterrows():
            original = row['Category']
            if pd.isna(original):
                continue
            original_str = str(original).strip()

            # Skip if already an OFF key
            if original_str.startswith('en:'):
                continue

            # Check if it's in our mapping
            if original_str in CATEGORY_MAPPING:
                new_value = CATEGORY_MAPPING[original_str]
                category_changes.append({
                    'row': idx + 2,
                    'original': original_str,
                    'new': new_value
                })

        if category_changes:
            print(f"\n   Found {len(category_changes)} rows to update in Column B:")
            for change in category_changes[:10]:
                print(f"   Row {change['row']}: '{change['original']}' → '{change['new']}'")
            if len(category_changes) > 10:
                print(f"   ... and {len(category_changes) - 10} more")
        else:
            print("   No changes needed - all categories already use OFF keys!")

    # ============================================================
    # MIGRATE COLUMN H (Certification_Categories)
    # ============================================================

    print("\n📋 Column H (Certification_Categories) changes:")
    cert_changes = []

    if 'Certification_Categories' in df.columns:
        for idx, row in df.iterrows():
            original = row['Certification_Categories']
            if pd.isna(original) or not isinstance(original, str):
                continue
            original_str = original.strip()
            if not original_str:
                continue

            # Standardize the string
            new_value = standardize_cert_categories(original_str)

            if new_value != original_str:
                cert_changes.append({
                    'row': idx + 2,
                    'original': original_str[:50] + ('...' if len(original_str) > 50 else ''),
                    'new': new_value[:50] + ('...' if len(new_value) > 50 else '')
                })

        if cert_changes:
            print(f"\n   Found {len(cert_changes)} rows to update in Column H:")
            for change in cert_changes[:10]:
                print(f"   Row {change['row']}: '{change['original']}' → '{change['new']}'")
            if len(cert_changes) > 10:
                print(f"   ... and {len(cert_changes) - 10} more")
        else:
            print("   No changes needed - all certification categories are already standardized!")

    # If no changes needed, exit
    if not category_changes and not cert_changes:
        print("\n✅ No changes needed! All columns already standardized.")
        return

    # Ask for confirmation
    print("\n⚠️  WARNING: This will update Column B and Column H only.")
    print("   ALL certification data (B Corp, Fair Trade, Rainforest Alliance, Leaping Bunny) will be preserved.")
    print("   A backup has been created.")

    response = input("\n   Continue? (yes/no): ").strip().lower()
    if response not in ['yes', 'y']:
        print("❌ Migration cancelled.")
        return

    # ============================================================
    # APPLY CHANGES
    # ============================================================

    print("\n🔄 Applying changes...")

    # Apply Column B changes
    if category_changes:
        category_updated = 0
        for idx, row in df.iterrows():
            original = row['Category']
            if pd.isna(original):
                continue
            original_str = str(original).strip()
            if original_str.startswith('en:'):
                continue
            if original_str in CATEGORY_MAPPING:
                df.at[idx, 'Category'] = CATEGORY_MAPPING[original_str]
                category_updated += 1
        print(f"   ✅ Updated {category_updated} rows in Column B")

    # Apply Column H changes
    if cert_changes:
        cert_updated = 0
        for idx, row in df.iterrows():
            original = row['Certification_Categories']
            if pd.isna(original) or not isinstance(original, str):
                continue
            original_str = original.strip()
            if not original_str:
                continue

            new_value = standardize_cert_categories(original_str)
            if new_value != original_str:
                df.at[idx, 'Certification_Categories'] = new_value
                cert_updated += 1
        print(f"   ✅ Updated {cert_updated} rows in Column H")

    # Save the migrated file
    print(f"\n💾 Saving migrated file to {file_path}")
    df.to_excel(file_path, index=False)

    # Show summary
    print("\n✅ Migration complete!")
    print(f"   Column B updates: {len(category_changes)} rows")
    print(f"   Column H updates: {len(cert_changes)} rows")
    print(f"   Backup saved to: {backup_path}")

    # Show unique categories after migration
    print("\n📊 New categories in Column B:")
    for cat in sorted(df['Category'].dropna().unique()):
        count = df[df['Category'] == cat].shape[0]
        print(f"   - {cat}: {count} products")


if __name__ == '__main__':
    print("=" * 60)
    print("  SAFE MIGRATION - Columns B & H")
    print("  Preserves ALL certification data")
    print("=" * 60)
    print()
    safe_migrate_excel()
