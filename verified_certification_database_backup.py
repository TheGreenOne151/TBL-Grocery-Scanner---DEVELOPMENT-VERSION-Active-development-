import pandas as pd

def create_certification_template_boolean(filename="grocery_certifications_boolean.xlsx"):
    """
    Create Excel template with TRUE/FALSE for certifications
    """

    # Top grocery brands
    top_items = [
        "Coca-Cola", "Pepsi", "Diet Coke", "Sprite", "Mountain Dew",
        "Gatorade", "Tropicana Orange Juice", "Simply Orange", "Minute Maid",
        "Dasani Water", "Aquafina", "Nestle Pure Life",
        "Lay's Potato Chips", "Doritos", "Cheetos", "Pringles", "Fritos",
        "Oreo Cookies", "Chips Ahoy", "Ritz Crackers", "Wheat Thins", "Triscuit",
        "M&M's", "Snickers", "Twix", "Reese's Peanut Butter Cups", "Kit Kat",
        "Hershey's Chocolate Bar", "Skittles", "Starburst", "Milky Way",
        "Cheerios", "Frosted Flakes", "Special K", "Raisin Bran", "Corn Flakes",
        "Lucky Charms", "Cap'n Crunch", "Fruit Loops", "Pop Tarts", "Eggo Waffles",
        "Jimmy Dean Sausage", "Oscar Mayer Bacon", "Hillshire Farm",
        "Kraft Mac & Cheese", "Philadelphia Cream Cheese", "Velveeta",
        "Yoplait Yogurt", "Chobani Yogurt", "Dannon Yogurt", "Activia", "Oikos",
        "Land O'Lakes Butter", "Breakstone's Sour Cream", "Cool Whip",
        "Hot Pockets", "Lean Cuisine", "Stouffer's Lasagna", "Digiorno Pizza",
        "Tombstone Pizza", "Birds Eye Vegetables", "Marie Callender's", "Banquet",
        "Campbell's Soup", "Progresso Soup", "Chef Boyardee", "SpaghettiOs",
        "Hormel Chili", "Dinty Moore Stew", "Spam", "Chef Boyardee Ravioli",
        "Heinz Ketchup", "French's Mustard", "Hellmann's Mayonnaise",
        "Hidden Valley Ranch", "Wish-Bone Salad Dressing", "Sweet Baby Ray's BBQ",
        "Pam Cooking Spray", "Crisco", "Swanson Broth", "Wesson Oil",
        "Folgers Coffee", "Maxwell House", "Starbucks Coffee", "Nescafe",
        "Lipton Tea", "Arizona Iced Tea", "Snapple", "Twinings",
        "Clif Bar", "Luna Bar", "Kind Bar", "Larabar",
        "Annie's Homegrown", "Nature's Path", "Kashi", "Amy's Kitchen",
        "Great Value (Walmart)", "Kirkland Signature (Costco)",
        "365 Everyday Value (Whole Foods)", "Market Pantry (Target)",
        "Simple Truth (Kroger)", "Good & Gather (Target)", "Up&Up (Target)",
        "Goya", "Badia", "Herdez", "La Costena", "Chi-Chi's",
        "Gerber", "Beech-Nut", "Earth's Best",
        "Purina", "Pedigree", "Whiskas", "Iams", "Friskies",
        "Betty Crocker", "Duncan Hines", "Pillsbury", "Jell-O",
        "Nestle Toll House", "Baker's Chocolate", "Gold Medal Flour",
        "Windex", "Clorox", "Tide", "Gain", "Downy", "Bounty", "Charmin"
    ]

    # Trim to 100 items
    top_items = top_items[:100]

    # Create DataFrame
    data = {
        "Product_Brand": top_items,
        "Category": [""] * 100,
        "Notes": [""] * 100,
        "B_Corp": [False] * 100,
        "Fair_Trade": [False] * 100,
        "Rainforest_Alliance": [False] * 100,
        "Leaping_Bunny": [False] * 100,
        "Research_Complete": [False] * 100,
        "Last_Updated": [""] * 100,
        "Confidence": ["Low"] * 100
    }

    df = pd.DataFrame(data)

    # Create Excel file
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Certifications', index=False)

    print(f"✅ Created {filename}")
    print(f"📊 {len(df)} products/brands")
    print("🔍 All certifications start as FALSE")
    print("💡 Change to TRUE when you verify certifications")

    return filename

if __name__ == "__main__":
    create_certification_template_boolean()
