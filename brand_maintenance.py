#!/usr/bin/env python3
"""
Brand Maintenance Helper Script
Run this after updating Excel certifications
"""

import pandas as pd
import re
import sys

# Copy the normalization function and constants from app.py
def normalize_brand(brand: str) -> str:
    """Brand name normalization without importing entire app"""
    if not brand:
        return ""

    normalized = brand.strip().lower()

    # Remove common prefixes and suffixes
    remove_phrases = [
        "the ", "inc", "llc", "co", "corp", "corporation", "company",
        "ltd", "limited", "plc", "group", "holdings", "foods", "products",
        "brands", "international", "usa", "us", "uk", "canada", "europe",
        "®", "™", "©", "(", ")", "[", "]", "{", "}", "|", "\\", "/"
    ]

    for phrase in remove_phrases:
        normalized = normalized.replace(phrase, "")

    # Replace common symbols
    replacements = {
        "'": "", "&": "and", "+": "and", ".": "", ",": "",
        "-": " ", "_": " ", ";": " ", ":": " ", "!": "",
        "?": "", "@": "", "#": "", "$": "", "%": "",
        "^": "", "*": "", "=": "", "~": "",
    }

    for old, new in replacements.items():
        normalized = normalized.replace(old, new)

    # Remove multiple spaces
    while "  " in normalized:
        normalized = normalized.replace("  ", " ")

    return normalized.strip()

# Copy constants from app.py (only what we need)
def load_hardcoded_db():
    """Load hardcoded scores from a copy of the data"""
    # This should match the HARDCODED_SCORES_DB from app.py
    # For now, we'll parse it from the app.py file
    import ast

    with open("app.py", "r") as f:
        content = f.read()

    # Extract HARDCODED_SCORES_DB dictionary
    start_marker = "HARDCODED_SCORES_DB: ClassVar[Dict[str, Dict[str, Any]]] = {"
    end_marker = "}"

    start_idx = content.find(start_marker)
    if start_idx == -1:
        return {}

    # Find the matching closing brace
    brace_count = 0
    in_string = False
    escape_char = False
    string_char = None

    for i in range(start_idx + len(start_marker), len(content)):
        char = content[i]

        if escape_char:
            escape_char = False
            continue

        if char == "\\":
            escape_char = True
            continue

        if in_string:
            if char == string_char:
                in_string = False
                string_char = None
            continue

        if char in ('"', "'"):
            in_string = True
            string_char = char
            continue

        if char == "{":
            brace_count += 1
        elif char == "}":
            if brace_count == 0:
                dict_str = content[start_idx + len(start_marker):i + 1]
                break
            brace_count -= 1

    try:
        # Parse the dictionary
        dict_str = "{" + dict_str + "}"
        hardcoded_db = ast.literal_eval(dict_str)
        return hardcoded_db
    except:
        return {}

def load_parent_mapping():
    """Load parent company mapping from app.py"""
    import ast

    with open("app.py", "r") as f:
        content = f.read()

    # Extract PARENT_COMPANY_MAPPING dictionary
    start_marker = "PARENT_COMPANY_MAPPING: ClassVar[Dict[str, str]] = {"
    end_marker = "}"

    start_idx = content.find(start_marker)
    if start_idx == -1:
        return {}

    # Find the matching closing brace
    brace_count = 0
    in_string = False
    escape_char = False
    string_char = None

    for i in range(start_idx + len(start_marker), len(content)):
        char = content[i]

        if escape_char:
            escape_char = False
            continue

        if in_string:
            if char == string_char:
                in_string = False
                string_char = None
            continue

        if char in ('"', "'"):
            in_string = True
            string_char = char
            continue

        if char == "{":
            brace_count += 1
        elif char == "}":
            if brace_count == 0:
                dict_str = content[start_idx + len(start_marker):i + 1]
                break
            brace_count -= 1

    try:
        # Parse the dictionary
        dict_str = "{" + dict_str + "}"
        parent_mapping = ast.literal_eval(dict_str)
        return parent_mapping
    except:
        return {}

def check_brand_consistency():
    """Check for conflicts between Excel, hardcoded DB, and parent mapping"""

    # Load Excel data
    try:
        df = pd.read_excel("comprehensive_grocery_certifications.xlsx")
    except FileNotFoundError:
        print("❌ Error: Excel file not found!")
        print("Make sure 'comprehensive_grocery_certifications.xlsx' exists in the current directory.")
        return

    # Load data from app.py
    HARDCODED_DB = load_hardcoded_db()
    PARENT_MAPPING = load_parent_mapping()

    print("=" * 60)
    print("BRAND MAINTENANCE CHECK")
    print("=" * 60)

    conflicts = []
    recommendations = []

    for _, row in df.iterrows():
        brand = str(row.get('Product_Brand', '')).strip()
        if not brand:
            continue

        normalized = normalize_brand(brand)

        # Check if brand has certifications in Excel
        has_certs = False
        cert_columns = [
            'B_Corp', 'B Corp', 'b_corp', 'B Corp Certification', 'bcorp',
            'Fair_Trade', 'Fair Trade', 'fair_trade', 'Fair Trade Certified', 'fairtrade',
            'Rainforest_Alliance', 'Rainforest Alliance', 'rainforest_alliance', 'Rainforest Alliance Certified', 'rainforest',
            'Leaping_Bunny', 'Leaping Bunny', 'leaping_bunny', 'Cruelty Free', 'leapingbunny'
        ]

        for col in cert_columns:
            if col in row:
                value = row[col]
                if pd.notna(value):
                    if isinstance(value, bool) and value:
                        has_certs = True
                        break
                    elif isinstance(value, (int, float)) and value:
                        has_certs = True
                        break
                    elif isinstance(value, str) and value.strip().lower() in ['true', 'yes', 'y', '1', 't']:
                        has_certs = True
                        break

        # Check current status
        in_hardcoded = normalized in HARDCODED_DB
        in_parent_map = normalized in PARENT_MAPPING

        print(f"\n🔍 Brand: {brand}")
        print(f"   Normalized: {normalized}")
        print(f"   Has certs in Excel: {'✅' if has_certs else '❌'}")
        print(f"   In hardcoded DB: {'✅' if in_hardcoded else '❌'}")
        print(f"   In parent mapping: {'✅' if in_parent_map else '❌'}")

        # Analyze conflicts
        if has_certs and in_parent_map:
            conflicts.append({
                'brand': brand,
                'normalized': normalized,
                'issue': 'Has certifications but still in parent mapping',
                'recommendation': 'Consider removing from parent mapping'
            })
            print(f"   ⚠️  CONFLICT: Has certs but in parent mapping!")

        if has_certs and not in_hardcoded:
            recommendations.append({
                'brand': brand,
                'normalized': normalized,
                'action': 'Add to hardcoded DB',
                'reason': 'Has certifications, should be pre-calculated'
            })
            print(f"   💡 RECOMMENDATION: Add to hardcoded database")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    if conflicts:
        print(f"\n⚠️  FOUND {len(conflicts)} CONFLICTS:")
        for conflict in conflicts:
            print(f"   • {conflict['brand']} (norm: {conflict['normalized']})")
            print(f"     Issue: {conflict['issue']}")
            print(f"     Fix: {conflict['recommendation']}")

    if recommendations:
        print(f"\n💡 RECOMMENDATIONS ({len(recommendations)} brands):")
        for rec in recommendations:
            print(f"   • {rec['brand']} (norm: {rec['normalized']})")
            print(f"     Action: {rec['action']}")
            print(f"     Reason: {rec['reason']}")

    print(f"\n📊 STATS:")
    print(f"   Total brands in Excel: {len(df)}")
    print(f"   Brands in hardcoded DB: {len(HARDCODED_DB)}")
    print(f"   Brands in parent mapping: {len(PARENT_MAPPING)}")

    return conflicts, recommendations

def generate_hardcoded_entry(brand_name):
    """Generate hardcoded entry for a brand based on Excel certifications"""
    try:
        df = pd.read_excel("comprehensive_grocery_certifications.xlsx")
    except FileNotFoundError:
        print("❌ Error: Excel file not found!")
        return None

    # Find the brand
    brand_row = None
    for _, row in df.iterrows():
        excel_brand = str(row.get('Product_Brand', '')).strip()
        if excel_brand.lower() == brand_name.lower():
            brand_row = row
            break

    if brand_row is None:
        print(f"Brand '{brand_name}' not found in Excel")
        return None

    # Extract certifications
    certs = []

    # B Corp
    b_corp_cols = ['B_Corp', 'B Corp', 'b_corp', 'B Corp Certification', 'bcorp']
    for col in b_corp_cols:
        if col in brand_row and pd.notna(brand_row[col]):
            value = brand_row[col]
            if (isinstance(value, bool) and value) or \
               (isinstance(value, (int, float)) and value) or \
               (isinstance(value, str) and value.strip().lower() in ['true', 'yes', 'y', '1', 't']):
                certs.append("B Corp")
                break

    # Fair Trade
    ft_cols = ['Fair_Trade', 'Fair Trade', 'fair_trade', 'Fair Trade Certified', 'fairtrade']
    for col in ft_cols:
        if col in brand_row and pd.notna(brand_row[col]):
            value = brand_row[col]
            if (isinstance(value, bool) and value) or \
               (isinstance(value, (int, float)) and value) or \
               (isinstance(value, str) and value.strip().lower() in ['true', 'yes', 'y', '1', 't']):
                certs.append("Fair Trade")
                break

    # Rainforest Alliance
    ra_cols = ['Rainforest_Alliance', 'Rainforest Alliance', 'rainforest_alliance', 'Rainforest Alliance Certified', 'rainforest']
    for col in ra_cols:
        if col in brand_row and pd.notna(brand_row[col]):
            value = brand_row[col]
            if (isinstance(value, bool) and value) or \
               (isinstance(value, (int, float)) and value) or \
               (isinstance(value, str) and value.strip().lower() in ['true', 'yes', 'y', '1', 't']):
                certs.append("Rainforest Alliance")
                break

    # Leaping Bunny
    lb_cols = ['Leaping_Bunny', 'Leaping Bunny', 'leaping_bunny', 'Cruelty Free', 'leapingbunny']
    for col in lb_cols:
        if col in brand_row and pd.notna(brand_row[col]):
            value = brand_row[col]
            if (isinstance(value, bool) and value) or \
               (isinstance(value, (int, float)) and value) or \
               (isinstance(value, str) and value.strip().lower() in ['true', 'yes', 'y', '1', 't']):
                certs.append("Leaping Bunny")
                break

    # Calculate scores
    base = 5.0
    social = base
    environmental = base
    economic = base

    # Apply certification bonuses
    bonus_applied = False
    for cert in certs:
        if cert == "B Corp":
            social += 1.0
            environmental += 1.0
            economic += 1.0
            bonus_applied = True
        elif cert == "Fair Trade":
            social += 1.0
            environmental += 0.5
            economic += 0.5
            bonus_applied = True
        elif cert == "Rainforest Alliance":
            social += 0.5
            environmental += 1.0
            economic += 0.5
            bonus_applied = True
        elif cert == "Leaping Bunny":
            social += 1.0
            environmental += 0.5
            economic += 0.0
            bonus_applied = True

    # Apply multi-cert bonus
    multi_cert_bonus = 0.0
    if bonus_applied and len(certs) > 1:
        multi_cert_bonus = (len(certs) - 1) * 0.5
        social += multi_cert_bonus
        environmental += multi_cert_bonus
        economic += multi_cert_bonus

    # Cap at 10.0
    social = min(10.0, social)
    environmental = min(10.0, environmental)
    economic = min(10.0, economic)

    # Generate Python code
    normalized = normalize_brand(brand_name)

    code = f'''    "{normalized}": {{
        "social": {social:.1f},
        "environmental": {environmental:.1f},
        "economic": {economic:.1f},
        "certifications": {certs},
        "multi_cert_applied": {str(bonus_applied).lower()},
        "multi_cert_bonus": {multi_cert_bonus:.1f},
    }},'''

    print("\n" + "=" * 60)
    print(f"GENERATED HARDCODED ENTRY FOR: {brand_name}")
    print("=" * 60)
    print("\nCopy this to HARDCODED_SCORES_DB in app.py:\n")
    print(code)
    print(f"\n📊 Stats:")
    print(f"   Total certifications: {len(certs)}")
    print(f"   Scores: Social={social:.1f}, Environmental={environmental:.1f}, Economic={economic:.1f}")
    print(f"   Multi-cert bonus: {multi_cert_bonus:.1f}")

    return code

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--generate":
        if len(sys.argv) > 2:
            brand_name = " ".join(sys.argv[2:])
            generate_hardcoded_entry(brand_name)
        else:
            print("Usage: python brand_maintenance.py --generate 'Brand Name'")
    else:
        check_brand_consistency()

    # Optional: Generate entry for a specific brand
    # generate_hardcoded_entry("Your Brand Name")

    # Run maintenance script:python brand_maintenance.py
    # Check for conflicts (brands with certs but in parent mapping)
    # Add to hardcoded DB if needed: python brand_maintenance.py --generate "Brand Name"
    # Update parent mapping if needed
    # Test with endpoints:
      # # Test scoring: curl "https://your-app.onrender.com/test/scoring/Brand%20Name"
      # Test certification lookup: curl "https://your-app.onrender.com/certifications/search/Brand%20Name"
