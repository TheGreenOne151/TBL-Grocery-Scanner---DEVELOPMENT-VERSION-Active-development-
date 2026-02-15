# Brand Certification Maintenance Guide

## When Adding Certifications to Excel:

### 1. **Run consistency check:**
```bash
python brand_maintenance.py

## ✅ Case Study: Ben & Jerry's Fix

**Problem:** Ben & Jerry's was getting 5.0/5.0/5.0 scores (inheriting Unilever)

**Root Cause:**
1. In parent mapping: `"ben jerrys": "unilever"`
2. Hardcoded key mismatch: `"ben jerrys"` vs normalized `"ben and jerrys"`

**Solution:**
1. ✅ Commented out from parent mapping
2. ✅ Updated hardcoded key to `"ben and jerrys"`
3. ✅ Verified Excel certifications (B Corp=True, Fair Trade=True)

**Result:** Now scores 7.5/7.0/7.0 (7.17 overall) with correct certifications
