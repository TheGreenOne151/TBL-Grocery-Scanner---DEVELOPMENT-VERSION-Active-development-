# create_excel.py
import pandas as pd
import os

def create_sample_excel_file():
    """Create a sample Excel file with certification data"""

    # Sample certification data
    data = {
        'Brand': [
            'Nespresso', 'Ben & Jerry\'s', 'Evian', 'Volvic', 'Dannon',
            'Activia', 'Oikos', 'Starbucks', 'Cadbury', 'Dunkin',
            '365 Everyday Value', 'Coca-Cola', 'Hershey\'s', 'Lipton',
            'Magnum', 'Nestle', 'Dove', 'General Mills', 'Kellogg\'s',
            'PepsiCo', 'Mondelez', 'Kraft Heinz'
        ],
        'B Corp': [
            True, True, True, True, True,
            True, True, False, False, False,
            False, False, False, False,
            False, False, False, False, False,
            False, False, False
        ],
        'Fair Trade': [
            True, True, False, False, False,
            False, False, True, True, True,
            True, False, False, False,
            False, False, False, False, False,
            False, False, False
        ],
        'Rainforest Alliance': [
            True, False, False, False, False,
            False, False, False, False, True,
            True, True, True, True,
            True, True, False, False, False,
            False, False, False
        ],
        'Leaping Bunny': [
            False, False, False, False, False,
            False, False, False, False, False,
            True, False, False, False,
            False, False, True, False, False,
            False, False, False
        ]
    }

    # Create DataFrame
    df = pd.DataFrame(data)

    # Save to Excel
    output_file = 'certifications.xlsx'
    df.to_excel(output_file, index=False)

    print(f"✅ Created {output_file} with {len(df)} brands")
    print(f"📊 Columns: {list(df.columns)}")
    print("\nSample data:")
    print(df.head())

    # Verify file was created
    if os.path.exists(output_file):
        file_size = os.path.getsize(output_file)
        print(f"\n📁 File size: {file_size} bytes")
        return True
    else:
        print("❌ File not created!")
        return False

if __name__ == "__main__":
    create_sample_excel_file()
