# TBL Grocery Scanner 🌿

A FastAPI-based grocery scanning prototype that provides Triple Bottom Line (Social, Environmental, Economic) scoring for consumer products. This system identifies brands, verifies third-party certifications (B Corp, Fair Trade, Rainforest Alliance, Leaping Bunny), and calculates consistent sustainability scores using an objective, certification-based methodology.

## Features
- **Barcode Scanning** via Open Food Facts API
- **Brand Extraction** from product names
- **Excel-based Certification Management**
- **Transparent Scoring System** with detailed methodology endpoints
- **Consistent Scoring** across all search methods

## Quick Start
```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
python app.py
