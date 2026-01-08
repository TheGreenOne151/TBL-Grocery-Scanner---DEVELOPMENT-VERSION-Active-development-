import pandas as pd
import os

CERTIFICATION_EXCEL_FILE = "certifications.xlsx"

def create_sample_excel_file():
    """Create a sample Excel file with certification data"""
    sample_data = {
        'brand': [
            "Ben & Jerry's",
            "Hershey's",
            "Patagonia",
            "Lush",
            "Divine Chocolate",
            "Equal Exchange",
            "The Body Shop",
            "Tony's Chocolonely",
            "Starbucks",
            "Nespresso",
            "Lipton",
            "Unilever",
            "Aveda",
            "Burt's Bees",
            "Method",
            "Seventh Generation"
        ],
        'b_corp': [True, False, True, False, True, True, False, True, False, False, False, False, False, False, True, True],
        'fair_trade': [True, False, False, False, True, True, False, True, False, False, False, False, False, False, False, False],
        'rainforest_alliance': [False, True, False, False, False, False, False, False, False, True, True, True, False, False, False, False],
        'leaping_bunny': [False, False, False, True, False, False, True, False, False, False, False, False, True, True, True, True],
        'certification_date': [
            '2024-01-15', '2024-02-20', '2024-03-10', '2024-01-30',
            '2024-02-15', '2024-03-01', '2024-01-20', '2024-02-28',
            '2024-03-15', '2024-02-10', '2024-01-25', '2024-03-05',
            '2024-02-28', '2024-01-30', '2024-03-12', '2024-02-22'
        ],
        'certification_id': [
            'B12345', 'RA67890', 'B23456', 'LB34567',
            'FT12345', 'FT23456', 'LB45678', 'B34567',
            'C98765', 'RA54321', 'RA87654', 'RA23456',
            'LB76543', 'LB43210', 'B65432', 'B32109'
        ],
        'notes': [
            'B Corp since 2012',
            'Rainforest Alliance certified',
            'B Corp certified outdoor apparel',
            'Cruelty-free cosmetics',
            'Fair Trade chocolate',
            'Fair Trade coffee',
            'Against animal testing',
            'B Corp and Fair Trade',
            'Coffee company',
            'Rainforest Alliance coffee',
            'Rainforest Alliance tea',
            'Rainforest Alliance various products',
            'Cruelty-free hair care',
            'Cruelty-free skincare',
            'B Corp cleaning products',
            'B Corp household products'
        ]
    }
    
    df = pd.DataFrame(sample_data)
    df.to_excel(CERTIFICATION_EXCEL_FILE, index=False)
    print(f"✅ Created sample Excel file: {CERTIFICATION_EXCEL_FILE}")
    print(f"📊 Contains {len(df)} brands")
    print("\nSample brands:")
    for brand in df['brand'].head():
        print(f"  - {brand}")
    
    return df

def verify_excel_file():
    """Verify the Excel file exists and can be read"""
    if os.path.exists(CERTIFICATION_EXCEL_FILE):
        try:
            df = pd.read_excel(CERTIFICATION_EXCEL_FILE)
            print(f"✅ Excel file found: {CERTIFICATION_EXCEL_FILE}")
            print(f"📊 Shape: {df.shape} (rows: {df.shape[0]}, columns: {df.shape[1]})")
            print("\nColumns:")
            for col in df.columns:
                print(f"  - {col}")
            print("\nFirst 5 brands:")
            for brand in df['brand'].head():
                print(f"  - {brand}")
            return True
        except Exception as e:
            print(f"❌ Error reading Excel file: {e}")
            return False
    else:
        print(f"❌ Excel file not found: {CERTIFICATION_EXCEL_FILE}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Excel File Verification Tool")
    print("=" * 60)
    
    # Check current directory
    print(f"\nCurrent directory: {os.getcwd()}")
    print(f"Looking for: {CERTIFICATION_EXCEL_FILE}")
    
    # List files in directory
    print("\nFiles in directory:")
    for file in os.listdir('.'):
        if file.endswith(('.xlsx', '.xls', '.py', '.html')):
            print(f"  - {file}")
    
    # Verify or create Excel file
    if not verify_excel_file():
        print("\nCreating sample Excel file...")
        create_sample_excel_file()
        verify_excel_file()
    
    print("\n" + "=" * 60)
    print("✅ Excel file is ready for use!")
    print("=" * 60)