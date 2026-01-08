"""
Run this script to add major US grocery brands to your database with placeholder scores.
Place this file in the same directory as app.py and run: python add_brands.py
"""

# List of major US grocery brands to add
BRANDS_TO_ADD = [
    # PepsiCo
    "pepsi", "mountain dew", "gatorade", "tropicana", "aquafina",
    "lay's", "doritos", "cheetos", "fritos", "tostitos", "ruffles", "sunchips",
    "quaker oats", "aunt jemima",

    # Coca-Cola Company
    "coca-cola", "sprite", "fanta", "dasani",
    "poland spring", "smart water", "vitaminwater",
    "simply orange", "minute maid",

    # Nestlé
    "nestle", "nescafe", "nespresso",
    "kitkat", "crunch", "butterfinger",
    "digiorno", "stouffer's", "hot pockets",
    "gerber", "purina",

    # General Mills
    "cheerios", "lucky charms", "chex", "trix",
    "betty crocker", "pillsbury", "bisquick",
    "yoplait", "go-gurt",
    "nature valley", "fiber one",
    "haagen-dazs", "annie's homegrown",
    "blue buffalo",

    # Kellogg's
    "kellogg's", "corn flakes", "special k", "frosted flakes", "froot loops",
    "pop-tarts", "nutri-grain", "eggo",
    "keebler", "cheez-it", "pringles",
    "morningstar farms",

    # Kraft Heinz
    "kraft", "velveeta",
    "oscar mayer", "philadelphia cream cheese",
    "heinz", "grey poupon",
    "capri sun", "kool-aid",
    "lunchables", "planters",

    # Mars Inc.
    "m&m's", "snickers", "twix", "milky way",
    "skittles", "starburst",
    "ben's original", "uncle ben's",
    "pedigree", "whiskas",

    # Mondelez International
    "oreo", "chips ahoy", "ritz",
    "cadbury", "toblerone", "sour patch kids",
    "trident", "dentyne",

    # Unilever
    "ben & jerry's", "breyers",
    "hellmann's", "best foods",
    "lipton", "dove", "magnum",
    "knorr",

    # Danone
    "dannon", "activia", "oikos",
    "evian", "volvic",
    "international delight",

    # Meat/Protein
    "tyson", "perdue", "hormel", "smithfield", "jimmy dean",

    # ConAgra
    "birds eye", "healthy choice", "banquet",
    "marie callender's", "duncan hines", "slim jim", "reddi-wip",

    # Campbell Soup Company
    "campbell's", "prego", "pepperidge farm", "v8", "goldfish", "swanson",

    # Hershey Company
    "hershey's", "reese's", "jolly rancher",

    # Store Brands
    "kirkland signature", "great value", "good & gather", "365 everyday value",

    # Other Major Brands
    "general electric", "procter & gamble", "johnson & johnson",
    "colgate-palmolive", "starbucks", "dunkin'",
]

def generate_brands_dict():
    """Generate a Python dictionary with all brands and placeholder scores"""

    brands_dict = {}

    for brand in BRANDS_TO_ADD:
        brands_dict[brand] = {
            "social": 5.0,  # Placeholder - neutral score
            "environmental": 5.0,  # Placeholder - neutral score
            "economic": 5.0,  # Placeholder - neutral score
            "certifications": [],
            "last_verified": "2025-11-29"  # Today's date
        }

    return brands_dict

def print_formatted_dict():
    """Print the dictionary in a format you can copy-paste into app.py"""

    brands = generate_brands_dict()

    print("# Copy this entire dictionary and replace CERTIFIED_BRANDS_DB in app.py")
    print("CERTIFIED_BRANDS_DB = {")

    for brand, data in sorted(brands.items()):
        print(f'    "{brand}": {{')
        print(f'        "social": {data["social"]},')
        print(f'        "environmental": {data["environmental"]},')
        print(f'        "economic": {data["economic"]},')
        print(f'        "certifications": {data["certifications"]},')
        print(f'        "last_verified": "{data["last_verified"]}"')
        print('    },')

    print("}")
    print(f"\n# Total brands: {len(brands)}")

def save_to_file():
    """Save the dictionary to a file"""

    brands = generate_brands_dict()

    with open("brands_database.py", "w") as f:
        f.write("# Major US Grocery Brands Database\n")
        f.write("# All scores are placeholders (5.0) - update manually\n\n")
        f.write("CERTIFIED_BRANDS_DB = {\n")

        for brand, data in sorted(brands.items()):
            f.write(f'    "{brand}": {{\n')
            f.write(f'        "social": {data["social"]},\n')
            f.write(f'        "environmental": {data["environmental"]},\n')
            f.write(f'        "economic": {data["economic"]},\n')
            f.write(f'        "certifications": {data["certifications"]},\n')
            f.write(f'        "last_verified": "{data["last_verified"]}"\n')
            f.write('    },\n')

        f.write("}\n")

    print(f"✅ Saved {len(brands)} brands to 'brands_database.py'")
    print("\nNext steps:")
    print("1. Open brands_database.py")
    print("2. Copy the CERTIFIED_BRANDS_DB dictionary")
    print("3. Paste it into app.py, replacing the existing CERTIFIED_BRANDS_DB")
    print("4. Restart your server: python app.py")
    print("5. Manually update scores for each brand as needed")

if __name__ == "__main__":
    print("=" * 60)
    print("ADDING MAJOR US GROCERY BRANDS")
    print("=" * 60)
    print()

    # Option 1: Print to console
    # print_formatted_dict()

    # Option 2: Save to file (RECOMMENDED)
    save_to_file()

    print("\n" + "=" * 60)
    print(f"Total brands to add: {len(BRANDS_TO_ADD)}")
    print("=" * 60)
