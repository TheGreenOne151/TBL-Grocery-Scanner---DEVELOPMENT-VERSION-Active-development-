# -*- coding: utf-8 -*-
import redis
import os
import re
import io
import csv
import json
import math
import time
import threading
import sqlite3
import importlib.util
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Set, ClassVar
from urllib.parse import quote
from collections import Counter
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from dataclasses import dataclass
from difflib import SequenceMatcher

import httpx
from fastapi import FastAPI, Query, HTTPException, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, field_validator


# ==================== BRAND MATCHING ====================
# This handles real-world product names like:
# - "Starbucks Coffee" → "Starbucks" (word overlap)
# - "Nestlé" → "Nestle" (unicode normalization)
# - "P&G" → "Procter & Gamble" (brand aliases)
# - "Great Value" → SKIP (store brand filtering)
#
# It evolved over time to solve specific user-facing issues.
# Each rule was added to fix a real problem.
#
# Why so complex? Because users search with:
# - Incomplete names: "Coca" → should match "Coca-Cola"
# - Misspellings: "Nestle" → should match "Nestlé"
# - Abbreviations: "P&G" → should match "Procter & Gamble"
# - Store brands: "Great Value" → should NOT match similar words
# - Product names: "Oreo Cookies" → should extract "Oreo"
#
# If you're changing this, test with ALL these cases!
# =======================================================

# Add this function near the top of elegant_app.py
def normalize_barcode(barcode: str) -> str:
    """Normalize barcode for consistent caching

    - Removes leading zeros (UPC/EAN normalization)
    - Trims whitespace
    - Returns normalized string
    """
    if not barcode:
        return barcode

    # Strip whitespace first
    normalized = barcode.strip()

    # Remove leading zeros (but keep at least one digit)
    normalized = normalized.lstrip('0')
    if not normalized:  # If it was all zeros, keep one zero
        normalized = '0'

    return normalized

def safe_float(value, default=0.0):
    """Convert any value to a JSON-safe float - prevents NaN and Infinity errors"""
    try:
        if value is None:
            return default

        # Convert to float first
        num = float(value)

        # Check for NaN or Infinity
        if math.isnan(num) or math.isinf(num):
            return default

        return num
    except (ValueError, TypeError, OverflowError):
        return default


def sanitize_for_json(data):
    """Recursively sanitize data to be JSON-compliant"""
    if isinstance(data, dict):
        return {k: sanitize_for_json(v) for k, v in data.items()}
    elif isinstance(data, (list, tuple)):
        return [sanitize_for_json(item) for item in data]
    elif isinstance(data, float):
        return safe_float(data)
    elif isinstance(data, (int, str, bool, type(None))):
        return data
    else:
        # For any other type, convert to string
        return str(data)


PORT = int(os.getenv("PORT", 8000))

# REMOVED: import bcrypt           # Will load inside functions
# REMOVED: import pandas as pd     # Will load inside functions


def lazy_import(module_name: str):
    """Import modules only when needed to save memory"""
    import importlib
    return importlib.import_module(module_name)


# Add these after the lazy_import function in your imports section
_BCRYPT = None


def get_bcrypt():
    """Get bcrypt module with lazy loading and caching"""
    global _BCRYPT
    if _BCRYPT is None:
        _BCRYPT = lazy_import("bcrypt")
    return _BCRYPT


# CACHED PANDAS IMPORT
_PANDAS = None
_OPENPYXL = None


def get_pandas():
    """Get pandas module with lazy loading and caching"""
    global _PANDAS
    if _PANDAS is None:
        _PANDAS = lazy_import("pandas")
    return _PANDAS


def get_openpyxl():
    """Get openpyxl module with lazy loading and caching"""
    global _OPENPYXL
    if _OPENPYXL is None:
        _OPENPYXL = lazy_import("openpyxl")
    return _OPENPYXL


# Add Numpy caching to your imports section (after pandas caching)
# NUMPY CACHING (not currently used, but available for future)
_NUMPY = None


def get_numpy():
    """Get numpy module with lazy loading and caching"""
    global _NUMPY
    if _NUMPY is None:
        _NUMPY = lazy_import("numpy")
    return _NUMPY


# ==================== CONFIGURATION DATACLASSES ====================


@dataclass
class ScoringConfig:
    """Configuration for scoring system"""

    BASE_SCORE: ClassVar[float] = 5.0
    MULTI_CERT_BONUS: ClassVar[float] = 0.5
    CERTIFICATION_BONUSES: ClassVar[Dict[str, Dict[str, float]]] = {
        "B Corp": {"social": 3.5, "environmental": 3.5, "economic": 3.5},
        "Fair Trade": {"social": 2.0, "environmental": 1.0, "economic": 2.0},
        "Rainforest Alliance": {"social": 1.0, "environmental": 2.5, "economic": 1.0},
        "Leaping Bunny": {"social": 1.0, "environmental": 0.0, "economic": 0.0},
    }
    GRADE_THRESHOLDS: ClassVar[Dict[str, float]] = {
        "EXCELLENT": 8.5,
        "GREAT": 7.0,
        "GOOD": 5.0,
        "POOR": 0.0,
    }


@dataclass
class FileConfig:
    """Configuration for file paths"""
    CERTIFICATION_EXCEL_FILE: ClassVar[str] = "comprehensive_grocery_certifications_COMPLETE.xlsx"  # CHANGED
    CREATE_EXCEL_SCRIPT: ClassVar[str] = "create_excel.py"
    CERT_SOURCES: ClassVar[Dict[str, str]] = {
        "b_corp": "https://www.bcorporation.net/en-us/find-a-b-corp/",
        "fair_trade": "https://www.flocert.net/fairtrade-customer-search/",
        "rainforest_alliance": "https://www.rainforest-alliance.org/find-certified/",
        "leaping_bunny": "https://www.leapingbunny.org/shopping-guide/",
    }


@dataclass
class BrandData:
    """Brand scoring data container"""

    brand: str  # ADD THIS FIELD
    social: float
    environmental: float
    economic: float
    certifications: List[str]
    scoring_method: str = "dynamic_calculation"
    multi_cert_applied: bool = False
    multi_cert_bonus: float = 0.0
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with JSON-safe values"""
        return {
            "brand": str(self.brand) if self.brand else "Unknown",
            "social": safe_float(self.social),
            "environmental": safe_float(self.environmental),
            "economic": safe_float(self.economic),
            "certifications": list(self.certifications) if self.certifications else [],
            "scoring_method": str(self.scoring_method),
            "multi_cert_applied": bool(self.multi_cert_applied),
            "multi_cert_bonus": safe_float(self.multi_cert_bonus),
            "notes": str(self.notes) if self.notes else "",
        }

# ==================== DECORATORS ====================


def cache_result(func):
    """Cache expensive function results"""
    cache = {}

    def wrapper(*args, **kwargs):
        # Create a cache key from arguments
        key = str(args) + str(sorted(kwargs.items()))

        if key not in cache:
            cache[key] = func(*args, **kwargs)

        return cache[key]

    return wrapper


def log_execution(func):
    """Log function execution"""

    def wrapper(*args, **kwargs):
        logger = logging.getLogger(__name__)
        logger.debug(
            f"Executing {func.__name__} with args={args}, kwargs={kwargs}")
        result = func(*args, **kwargs)
        logger.debug(f"{func.__name__} returned {result}")
        return result

    return wrapper


# ==================== HELPER FUNCTIONS ====================


def safe_get(dict_obj: Dict, key: str, default: Any = None) -> Any:
    """Safely get value from dictionary with default"""
    return dict_obj.get(key, default)


def normalize_text(text: str) -> str:
    """Normalize text for comparison"""
    if not text:
        return ""
    return text.strip().lower()


def calculate_overall_score(
    social: float, environmental: float, economic: float
) -> Dict[str, Any]:
    """Calculate overall TBL score and grade"""
    overall = (social + environmental + economic) / 3

    if overall >= ScoringConfig.GRADE_THRESHOLDS["EXCELLENT"]:
        grade = "EXCELLENT"
    elif overall >= ScoringConfig.GRADE_THRESHOLDS["GREAT"]:
        grade = "GREAT"
    elif overall >= ScoringConfig.GRADE_THRESHOLDS["GOOD"]:
        grade = "GOOD"
    else:
        grade = "POOR"

    return {"overall_score": round(overall, 2), "grade": grade}


# ==================== FASTAPI APP ====================


from fastapi.responses import JSONResponse

class UTF8JSONResponse(JSONResponse):
    """JSONResponse that always declares charset=utf-8.

    FastAPI's default JSONResponse sends `Content-Type: application/json`
    without a charset. RFC 8259 says JSON is UTF-8 by default, so compliant
    clients decode it correctly. But Windows PowerShell 5.1's
    Invoke-RestMethod falls back to Latin-1 when charset is absent, which
    renders 'Nestlé' as 'NestlÃ©' in the terminal. Declaring the charset
    explicitly fixes PS 5.1 without changing behavior for any other client.
    """
    media_type = "application/json; charset=utf-8"


app = FastAPI(
    title="TBL Grocery Scanner",
    version="2.4.0",
    default_response_class=UTF8JSONResponse,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change to "*" for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== PYDANTIC MODELS ====================


class UserRegistration(BaseModel):
    username: str
    email: str
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, password: str) -> str:
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters")
        return password


class LoginRequest(BaseModel):
    username: str
    password: str


class Product(BaseModel):
    barcode: str = ""
    brand: str = ""
    product_name: str = ""
    category: str = ""
    price: Optional[float] = None

    @field_validator("brand", "product_name", "category")
    @classmethod
    def validate_fields(cls, value: str, info) -> str:
        field_name = info.field_name

        if not value or value.strip() == "":
            return {
                "brand": "Unknown",
                "product_name": "Generic Product",
                "category": "General",
            }[field_name]

        return value.strip()


class BrandInput(BaseModel):
    brand: str


class BrandAdd(BaseModel):
    brand: str
    social: float
    environmental: float
    economic: float
    certifications: List[str] = []

    @field_validator("social", "environmental", "economic")
    @classmethod
    def validate_scores(cls, score: float) -> float:
        if not 0 <= score <= 10:
            raise ValueError("Scores must be between 0 and 10")
        return score


class ProductSearch(BaseModel):
    product_name: str
    max_results: int = 10


# ==================== BRAND NORMALIZER ====================


class BrandNormalizer:
    """Encapsulate all brand normalization logic"""

    # Known national brands for prioritization
    NATIONAL_BRANDS: ClassVar[Set[str]] = {
        "coca cola",
        "pepsi",
        "nestle",
        "kraft",
        "heinz",
        "unilever",
        "procter gamble",
        "general mills",
        "kelloggs",
        "campbells",
        "hershey",
        "mars",
        "mondelez",
        "danone",
        "coca-cola",
        "pepsico",
        "johnson johnson",
        "kimberly clark",
        "colgate palmolive",
        "p g",
        "kellogg",
        "general electric",
        "dannon",
        "quaker",
        "conagra",
        "tyson",
        "smithfield",
        "hormel",
        "jbs",
        "perdue",
        "cargill",
        "adm",
        "bunge",
        "land olakes",
        "dairy farmers of america",
        "dean foods",
        "saputo",
        "frontera",
        "chobani",
        "stonyfield",
        "organic valley",
        "horizon organic",
        "lifeway",
        "kefir",
        "yoplait",
        "activia",
        "siggi",
        "noosa",
        "liberte",
        "brown cow",
        "wallaby",
        "alexander",
        "maple hill",
        "clover organic",
        "straus",
        "berkley",
        "jensen",
        "organic meadows",
    }

    # Store brands (to deprioritize)
    STORE_BRANDS: ClassVar[Set[str]] = {
        "great value",
        "kirkland signature",
        "market pantry",
        "up up",
        "equate",
        "good gather",
        "good good",
        "simply nature",
        "open nature",
        "whole foods",
        "trader joes",
        "365 everyday value",
        "365",
        "aldi",
        "happy farms",
        "friendly farms",
        "burmans",
        "benton",
        "bakers corner",
        "clancy",
        "friendly",
        "specially selected",
        "simply",
        "private selection",
        "everyday essentials",
        "essentials",
        "value",
        "store brand",
        "generic",
        "private label",
        "house brand",
        "own brand",
    }

    # Parent company mapping for brand identification ONLY
    PARENT_COMPANY_MAPPING: ClassVar[Dict[str, str]] = {
        # General Mills products
        "cheerios": "general mills",
        "chex": "general mills",
        "lucky charms": "general mills",
        "cocoa puffs": "general mills",
        "trix": "general mills",
        "reeses puffs": "general mills",
        "cinnamon toast crunch": "general mills",
        "gold medal": "general mills",
        "betty crocker": "general mills",
        "pillsbury": "general mills",
        "haagen dazs": "general mills",
        "yoplait": "general mills",
        "totinos": "general mills",
        "annanies": "general mills",
        "progresso": "general mills",
        "green giant": "general mills",
        "old el paso": "general mills",
        "fibre one": "general mills",
        "nature valley": "general mills",
        # Kellogg's products
        "frosted flakes": "kelloggs",
        "corn flakes": "kelloggs",
        "special k": "kelloggs",
        "raisin bran": "kelloggs",
        "rice krispies": "kelloggs",
        "fruit loops": "kelloggs",
        "apple jacks": "kelloggs",
        "cocoa krispies": "kelloggs",
        "pop tarts": "kelloggs",
        "egg": "kelloggs",
        "nutri grain": "kelloggs",
        "morningstar farms": "kelloggs",
        # Mondelez products
        "oreo": "mondelez",
        "chips ahoy": "mondelez",
        "ritz": "mondelez",
        "wheat thins": "mondelez",
        "triscuit": "mondelez",
        "belvita": "mondelez",
        "halloween": "mondelez",
        "milka": "mondelez",
        "cadbury": "mondelez",
        "toblerone": "mondelez",
        "sour patch kids": "mondelez",
        "tang": "mondelez",
        # PepsiCo products
        "lays": "pepsico",
        "doritos": "pepsico",
        "cheetos": "pepsico",
        "fritos": "pepsico",
        "tostitos": "pepsico",
        "ruffles": "pepsico",
        "sun chips": "pepsico",
        "quaker": "pepsico",
        "tropicana": "pepsico",
        "gatorade": "pepsico",
        "mountain dew": "pepsico",
        "pepsi": "pepsico",
        "7up": "pepsico",
        "aquafina": "pepsico",
        "lipton": "pepsico",
        "brisk": "pepsico",
        # Coca-Cola products
        "coca cola": "coca cola",
        "coke": "coca cola",
        "diet coke": "coca cola",
        "sprite": "coca cola",
        "fanta": "coca cola",
        "minute maid": "coca cola",
        "powerade": "coca cola",
        "dasani": "coca cola",
        "smartwater": "coca cola",
        "fairlife": "coca cola",
        # Nestle products
        "nescafe": "nestle",
        "nesquik": "nestle",
        "stouffers": "nestle",
        "lean cuisine": "nestle",
        "digiorno": "nestle",
        "tombstone": "nestle",
        "butterfinger": "nestle",
        "baby ruth": "nestle",
        "100 grand": "nestle",
        "raisinets": "nestle",
        "sno caps": "nestle",
        "wonka": "nestle",
        "purina": "nestle",
        "friskies": "nestle",
        "crunch": "nestle",
        # Unilever products
        "dove": "unilever",
        "axe": "unilever",
        "rexona": "unilever",
        "vaseline": "unilever",
        "lipton": "unilever",
        # "ben jerrys": "unilever",  # removed - Ben & Jerry's is independent
        "magnum": "unilever",
        "breyers": "unilever",
        "klondike": "unilever",
        "hellmanns": "unilever",
        "best foods": "unilever",
        "knorr": "unilever",
        # Kraft Heinz products
        "kraft": "kraft heinz",
        "heinz": "kraft heinz",
        "oscar mayer": "kraft heinz",
        "philadelphia": "kraft heinz",
        "velveeta": "kraft heinz",
        "cool whip": "kraft heinz",
        "jell o": "kraft heinz",
        "kool aid": "kraft heinz",
        "capri sun": "kraft heinz",
        "lunchables": "kraft heinz",
        # Mars products
        "mms": "mars",
        "snickers": "mars",
        "twix": "mars",
        "milky way": "mars",
        "skittles": "mars",
        "starburst": "mars",
        "orbit": "mars",
        "extra": "mars",
        "dove chocolate": "mars",
        "pedigree": "mars",
        "whiskas": "mars",
        "royal canin": "mars",
        # Procter & Gamble products
        "tide": "procter gamble",
        "pampers": "procter gamble",
        "gillette": "procter gamble",
        "oral b": "procter gamble",
        "crest": "procter gamble",
        "head shoulders": "procter gamble",
        "olay": "procter gamble",
        "pantene": "procter gamble",
        "downy": "procter gamble",
        "bounty": "procter gamble",
        "charmin": "procter gamble",
        "puffs": "procter gamble",
        "vicks": "procter gamble",
        "metamucil": "procter gamble",
        # Johnson & Johnson products
        "band aid": "johnson johnson",
        "tylenol": "johnson johnson",
        "motrin": "johnson johnson",
        "benadryl": "johnson johnson",
        "zyrtec": "johnson johnson",
        "neutrogena": "johnson johnson",
        "aveeno": "johnson johnson",
        "listerine": "johnson johnson",
        "reach": "johnson johnson",
        "splenda": "johnson johnson",
        # Campbell Soup products
        "campbells": "campbell soup",
        "prego": "campbell soup",
        "pepperidge farm": "campbell soup",
        "v8": "campbell soup",
        "swanson": "campbell soup",
        "pace": "campbell soup",
        "snyder of hanover": "campbell soup",
        # Conagra Brands products
        "healthy choice": "conagra",
        "chef boyardee": "conagra",
        "hunt": "conagra",
        "pam": "conagra",
        "reddi wip": "conagra",
        "duncan hines": "conagra",
        "slim jim": "conagra",
        "egg beater": "conagra",
        # Tyson Foods products
        "tyson": "tyson foods",
        "jimmy dean": "tyson foods",
        "hillshire farm": "tyson foods",
        "ball park": "tyson foods",
        "sara lee": "tyson foods",
        "state fair": "tyson foods",
        # Hormel products
        "spam": "hormel",
        "jennie o": "hormel",
        "applegate": "hormel",
        "wholly guacamole": "hormel",
        "herdez": "hormel",
        "skipper": "hormel",
        # Danone products
        "dannon": "danone",
        "oikos": "danone",
        "activia": "danone",
        "international delight": "danone",
        "silk": "danone",
        "so delicious": "danone",
        "vega": "danone",
        # Other common mappings
        "hershey": "hershey",
        "reese": "hershey",
        "kitkat": "hershey",
        "jolly rancher": "hershey",
        "ice breaker": "hershey",
        "barkthins": "hershey",
        "starbucks": "starbucks",
        "seattle best": "starbucks",
        "teavana": "starbucks",
        "evolution fresh": "starbucks",
        "cholula": "cholula",
        "frank redhot": "mccormick",
        "french": "mccormick",
        "old bay": "mccormick",
        "goya": "goya",
        "badia": "badia",
    }

    # Common brand abbreviations and aliases
    BRAND_ALIASES: ClassVar[Dict[str, str]] = {
        "gm": "general mills",
        "p&g": "procter gamble",
        "pg": "procter gamble",
        "j&j": "johnson johnson",
        "jj": "johnson johnson",
        "k": "kelloggs",
        "kmart": "kmart",
        "walmart": "walmart",
        "target": "target",
        "costco": "costco",
        "sams": "sams club",
        "aldi": "aldi",
        "trader joes": "trader joes",
        "whole foods": "whole foods",
        "tjs": "trader joes",
        "wf": "whole foods",
        "YVB": "Your Very Best",
    }

    # Common brand name variations
    BRAND_VARIATIONS: ClassVar[Dict[str, List[str]]] = {
        "general mills": ["gm", "general mills inc", "generalmills", "g mills"],
        "kelloggs": ["kellogg", "kellogg company", "kellogg's"],
        "mondelez": ["mondelez international", "kraft foods"],
        "pepsico": ["pepsi", "pepsi co", "pepsico inc"],
        "coca cola": ["coca-cola", "coke", "coca cola company"],
        "nestle": ["nestle", "nestle sa"],
        "unilever": ["unilever plc", "unilever nv"],
        "kraft heinz": ["kraft", "heinz", "kraft heinz company"],
        "mars": ["mars inc", "mars incorporated"],
        "procter gamble": ["p&g", "procter & gamble", "pg"],
        "johnson johnson": ["j&j", "johnson & johnson"],
        "campbell soup": ["campbell", "campbell's"],
        "conagra": ["conagra brands", "conagra foods"],
        "tyson foods": ["tyson", "tyson chicken"],
        "hormel": ["hormel foods"],
        "danone": ["dannon", "danone sa"],
        "hershey": ["hershey's", "hershey company"],
    }

    # Brand synonyms for matching
    BRAND_SYNONYMS: ClassVar[Dict[str, str]] = {
        "generalmills": "general mills",
        "g mills": "general mills",
        "gm": "general mills",
        "kellogg": "kelloggs",
        "kelloggs": "kelloggs",
        "kraft": "kraft heinz",
        "heinz": "kraft heinz",
        "p g": "procter gamble",
        "p&g": "procter gamble",
        "procter": "procter gamble",
        "johnson": "johnson johnson",
        "campbell": "campbell soup",
        "campbells": "campbell soup",
        "tyson": "tyson foods",
        "dannon": "danone",
        "hersheys": "hershey",
        "starbucks coffee": "starbucks",
        "nestle crunch": "nestle",
    }

    # ===== BRAND IDENTIFICATION DATABASE (DISABLED - Using Excel only) =====
    """
    BRAND_IDENTIFICATION_DB: ClassVar[Dict[str, Dict[str, Any]]] = {
        "365 everyday value": {
            "certifications": ["Fair Trade", "Rainforest Alliance", "Leaping Bunny"]
        },
        "365 by whole foods market": {
            "certifications": ["Leaping Bunny"]
        },
        "activia": {"certifications": ["B Corp"]},
        "amavida coffee & trading company": {"certifications": ["B Corp", "Fair Trade"]},
        "annies homegrown": {"certifications": []},
        "aquafina": {"certifications": []},
        "banquet": {"certifications": []},
        "barrys tea": {"certifications": ["Rainforest Alliance"]},
        "ben & jerrys": {"certifications": ["B Corp", "Fair Trade"]},
        "bens original": {"certifications": []},
        "best foods": {"certifications": []},
        "bettergoods": {"certifications": ["Rainforest Alliance"]},
        "betty crocker": {"certifications": []},
        "big spoon roasters": {"certifications": ["B Corp"]},
        "birds eye": {"certifications": []},
        "bisquick": {"certifications": []},
        "blk & bold": {"certifications": ["Fair Trade"]},
        "blue buffalo": {"certifications": []},
        "bones coffee company": {"certifications": ["Rainforest Alliance"]},
        "breyers": {"certifications": ["Rainforest Alliance"]},
        "brooklyn roasting company": {"certifications": ["Fair Trade"]},
        "bulletproof cold brew coffee": {"certifications": ["Rainforest Alliance"]},
        "butterfinger": {"certifications": []},
        "caboo": {"certifications": ["B Corp", "Leaping Bunny"]},
        "cadbury": {"certifications": []},
        "caffe umbria coffee": {"certifications": ["Fair Trade"]},
        "campbells": {"certifications": []},
        "capri sun": {"certifications": []},
        "caribou coffee": {"certifications": ["Rainforest Alliance"]},
        "cheerios": {"certifications": []},
        "cheetos": {"certifications": []},
        "cheez it": {"certifications": []},
        "chex": {"certifications": []},
        "chips ahoy": {"certifications": []},
        "clif": {"certifications": ["Rainforest Alliance"]},
        "coca cola": {"certifications": []},
        "colgate palmolive": {"certifications": []},
        "corn flakes": {"certifications": []},
        "costa coffee": {"certifications": ["Rainforest Alliance"]},
        "counter culture": {"certifications": ["B Corp"]},
        "crunch": {"certifications": ["B Corp", "Fair Trade", "Rainforest Alliance"]},
        "dannon": {"certifications": ["B Corp"]},
        "dasani": {"certifications": []},
        "dentyne": {"certifications": []},
        "digiorno": {"certifications": []},
        "doritos": {"certifications": []},
        "dove": {"certifications": []},
        "duncan hines": {"certifications": []},
        "dunkin": {"certifications": ["Rainforest Alliance"]},
        "eggo": {"certifications": []},
        "eight oclock": {"certifications": ["Rainforest Alliance"]},
        "evian": {"certifications": ["B Corp"]},
        "fanta": {"certifications": []},
        "feline natural": {"certifications": ["B Corp"]},
        "fiber one": {"certifications": []},
        "fritos": {"certifications": []},
        "froot loops": {"certifications": []},
        "frosted flakes": {"certifications": []},
        "gatorade": {"certifications": []},
        "general electric": {"certifications": []},
        "gerber": {"certifications": []},
        "gimme coffee": {"certifications": ["Fair Trade"]},
        "go gurt": {"certifications": []},
        "goldfish": {"certifications": []},
        "good gather": {"certifications": []},
        "great value": {"certifications": []},
        "grey poupon": {"certifications": []},
        "haagen dazs": {"certifications": []},
        "happy family organics": {"certifications": ["B Corp"]},
        "healthy choice": {"certifications": []},
        "heinz": {"certifications": []},
        "hellmanns": {"certifications": []},
        "hersheys": {"certifications": ["Rainforest Alliance"]},
        "hersheys milk chocolate cocoa mix": {"certifications": ["Rainforest Alliance"]},
        "hormel": {"certifications": []},
        "hot pockets": {"certifications": []},
        "hurricane": {"certifications": ["Rainforest Alliance"]},
        "international delight": {"certifications": []},
        "jaf tea": {"certifications": ["Rainforest Alliance"]},
        "jimmy dean": {"certifications": []},
        "joe coffee company": {"certifications": ["Rainforest Alliance"]},
        "johnson johnson": {"certifications": []},
        "jolly rancher": {"certifications": []},
        "k9 natural": {"certifications": ["B Corp"]},
        "keebler": {"certifications": []},
        "kelloggs": {"certifications": []},
        "keurig": {"certifications": ["Fair Trade", "Rainforest Alliance"]},
        "kirkland signature": {"certifications": []},
        "kitkat": {"certifications": []},
        "knorr": {"certifications": []},
        "kool aid": {"certifications": []},
        "kraft": {"certifications": []},
        "kroger": {"certifications": ["Rainforest Alliance"]},
        "la colombe": {"certifications": ["Rainforest Alliance"]},
        "lavazza": {"certifications": ["Rainforest Alliance"]},
        "lays": {"certifications": []},
        "lipton": {"certifications": ["Rainforest Alliance"]},
        "lipton pure leaf": {"certifications": ["Rainforest Alliance"]},
        "lucky charms": {"certifications": []},
        "lunchables": {"certifications": []},
        "luzianne tea": {"certifications": ["Rainforest Alliance"]},
        "magnum": {"certifications": ["Rainforest Alliance"]},
        "marie callenders": {"certifications": []},
        "meraki": {"certifications": ["B Corp"]},
        "milky way": {"certifications": []},
        "minute maid": {"certifications": []},
        "mms": {"certifications": []},
        "morningstar farms": {"certifications": []},
        "mountain dew": {"certifications": []},
        "naty": {"certifications": ["B Corp"]},
        "nature valley": {"certifications": []},
        "nescafe": {"certifications": ["B Corp", "Fair Trade", "Rainforest Alliance"]},
        "nespresso": {"certifications": ["B Corp", "Fair Trade", "Rainforest Alliance"]},
        "nesquik": {"certifications": ["Rainforest Alliance"]},
        "nestle": {"certifications": ["B Corp", "Fair Trade", "Rainforest Alliance"]},
        "nordqvist": {"certifications": ["Rainforest Alliance"]},
        "nutri grain": {"certifications": []},
        "oikos": {"certifications": ["B Corp"]},
        "once upon a farm": {"certifications": ["B Corp"]},
        "oreo": {"certifications": []},
        "oscar mayer": {"certifications": []},
        "pedigree": {"certifications": []},
        "pepperidge farm": {"certifications": []},
        "pepsi": {"certifications": []},
        "perdue": {"certifications": []},
        "pg tips": {"certifications": ["Rainforest Alliance"]},
        "philadelphia cream cheese": {"certifications": []},
        "pillsbury": {"certifications": []},
        "planters": {"certifications": []},
        "poland spring": {"certifications": []},
        "pop tarts": {"certifications": []},
        "popzup popcorn": {"certifications": ["B Corp"]},
        "prego": {"certifications": []},
        "pringles": {"certifications": []},
        "procter gamble": {"certifications": []},
        "purelife": {"certifications": []},
        "purina": {"certifications": []},
        "quaker oats": {"certifications": []},
        "reddi wip": {"certifications": []},
        "reeses": {"certifications": []},
        "reunion coffee roasters": {"certifications": ["Rainforest Alliance"]},
        "ritz": {"certifications": []},
        "rosamonte": {"certifications": ["Rainforest Alliance"]},
        "royal cup coffee": {"certifications": ["Rainforest Alliance"]},
        "ruffles": {"certifications": []},
        "second cup": {"certifications": ["Rainforest Alliance"]},
        "simply orange": {"certifications": []},
        "sisterly": {"certifications": ["B Corp"]},
        "skittles": {"certifications": []},
        "skinnygirl": {"certifications": ["Rainforest Alliance"]},
        "slim jim": {"certifications": []},
        "smart water": {"certifications": []},
        "smithfield": {"certifications": []},
        "snickers": {"certifications": []},
        "soleivita": {"certifications": ["Rainforest Alliance"]},
        "sour patch kids": {"certifications": []},
        "special k": {"certifications": []},
        "sprite": {"certifications": []},
        "starbucks": {"certifications": ["Fair Trade"]},
        "starburst": {"certifications": []},
        "stonyfield": {"certifications": ["B Corp"]},
        "stouffers": {"certifications": []},
        "sunchips": {"certifications": []},
        "sure house coffee roasting co": {"certifications": ["Fair Trade"]},
        "swanson": {"certifications": []},
        "taragüí": {"certifications": ["Rainforest Alliance"]},
        "tayst": {"certifications": ["Rainforest Alliance"]},
        "tchibo": {"certifications": ["Rainforest Alliance"]},
        "te reval": {"certifications": ["Rainforest Alliance"]},
        "teekanne": {"certifications": ["Rainforest Alliance"]},
        "tejava": {"certifications": ["Rainforest Alliance"]},
        "tetley": {"certifications": ["Rainforest Alliance"]},
        "this works": {"certifications": ["B Corp"]},
        "toblerone": {"certifications": []},
        "tostitos": {"certifications": []},
        "tres": {"certifications": ["Rainforest Alliance"]},
        "trident": {"certifications": []},
        "trix": {"certifications": []},
        "tropicana": {"certifications": []},
        "true botanicals": {"certifications": ["B Corp", "Leaping Bunny"]},
        "twix": {"certifications": []},
        "tyson": {"certifications": []},
        "uncle bens": {"certifications": []},
        "unni": {"certifications": ["B Corp"]},
        "v8": {"certifications": []},
        "velveeta": {"certifications": []},
        "verena street": {"certifications": ["Rainforest Alliance"]},
        "vitaminwater": {"certifications": []},
        "volvic": {"certifications": ["B Corp"]},
        "wawa": {"certifications": ["Rainforest Alliance"]},
        "westrock coffee": {"certifications": ["Fair Trade", "Rainforest Alliance"]},
        "whiskas": {"certifications": []},
        "yogi tea": {"certifications": ["Rainforest Alliance"]},
        "yoplait": {"certifications": ["B Corp"]},
        "yorkshire tea": {"certifications": ["Rainforest Alliance"]},
        "your very best": {"certifications": ["B Corp"]},
        "zavida coffee": {"certifications": ["Fair Trade", "Rainforest Alliance"]},
    }
    """
    BRAND_IDENTIFICATION_DB: ClassVar[Dict[str, Dict[str, Any]]] = {}


    @classmethod
    @cache_result
    def normalize(cls, brand: str) -> str:
        """Enhanced brand name normalization with better handling of variations"""
        if not brand:
            return ""

        # ADD THIS: Normalize accents (Ã© â†’ e, etc.)
        import unicodedata
        brand = unicodedata.normalize('NFKD', brand).encode('ASCII', 'ignore').decode('ASCII')

        normalized = brand.strip().lower()

        # Replace common symbols and special characters FIRST,
        # so the word-count guard below sees the final word form.
        replacements = {
            "'": "",
            "&": "and",
            "+": "and",
            ".": "",
            ",": "",
            "-": " ",
            "_": " ",
            ";": " ",
            ":": " ",
            "!": "",
            "?": "",
            "@": "",
            "#": "",
            "$": "",
            "%": "",
            "^": "",
            "*": "",
            "=": "",
            "~": "",
        }

        for old, new in replacements.items():
            normalized = normalized.replace(old, new)

        # Remove common prefixes and suffixes.
        #
        # Applied with a guard: refuse any strip that would reduce a
        # multi-word name to a single content word. "The Honest
        # Company" must not become "honest". "Best Foods" must not
        # become "best". The pairing of two content words is what
        # makes many brand names distinctive.
        #
        # Process longest phrases first, so " company" is tried
        # before " co".
        remove_phrases = [
            " inc",
            " llc",
            " co",
            "co ",
            " corp",
            " corporation",
            " company",
            " ltd",
            " limited",
            " plc",
            " group",
            " holdings",
            " foods",
            " products",
            " brands",
            " international",
            " usa",
            " us",
            " uk",
            " canada",
            " europe",
            "®",
            "™",
            "©",
            "(",
            ")",
            "[",
            "]",
            "{",
            "}",
            "|",
            "\\",
            "/",
        ]

        ordered_phrases = sorted(remove_phrases, key=lambda p: -len(p.strip()))
        for phrase in ordered_phrases:
            phrase_clean = phrase.strip()
            if not phrase_clean:
                continue
            pattern = r"\b" + re.escape(phrase_clean) + r"\b"
            candidate = re.sub(pattern, "", normalized).strip()
            if _content_word_count(normalized) >= 2 and _content_word_count(candidate) < 2:
                continue
            normalized = candidate

        # Handle brand aliases
        for alias, canonical in cls.BRAND_ALIASES.items():
            # Skip single-character aliases — they cause false positives
            # inside unrelated brand names (e.g. 'k' matching the 'k' in
            # 'special k' and turning it into 'special kelloggs').
            if len(alias) < 2:
                continue
            if alias == normalized or re.search(r"\b" + re.escape(alias) + r"\b", normalized):
                normalized = re.sub(r"\b" + re.escape(alias) + r"\b", canonical, normalized)

        # Handle brand synonyms
        for synonym, canonical in cls.BRAND_SYNONYMS.items():
            if synonym == normalized:
                normalized = canonical

        # Remove multiple spaces and trim
        while "  " in normalized:
            normalized = normalized.replace("  ", " ")

        return normalized.strip()

    @classmethod
    def find_parent_company(cls, product_name: str) -> Optional[str]:
        """Find parent company for a product using word-boundary matching.

        Matching strategy (in order):
          1. Whole-key phrase match: the entire product key must appear
             as a contiguous sequence of whole words in the product name.
             "raisin bran" matches "Raisin Bran Cereal" but not "testbrand".
          2. First-word anchor match: for multi-word keys where the first
             word is at least 4 characters, match if that first word
             appears as a whole word. Handles truncated product names
             like "Cinnamon Toast" for the key "cinnamon toast crunch".

        We deliberately do NOT do substring matching. A previous version
        matched "bran" inside "testbrand" and "egg" inside "veggie",
        producing wrong parent-company attributions on real products.
        """
        if not product_name:
            return None

        product_normalized = cls.normalize(product_name)

        # Precompute the set of whole words in the product name once.
        product_words = set(product_normalized.split())

        # --- Pass 1: whole-key phrase match ---
        for product_key, parent in cls.PARENT_COMPANY_MAPPING.items():
            if not product_key:
                continue

            pattern = r"\b" + re.escape(product_key) + r"\b"
            if re.search(pattern, product_normalized):
                logger.info(
                    f"Found parent company for '{product_name}': {parent} "
                    f"(via whole-key match: '{product_key}')"
                )
                return parent

        # --- Pass 2: first-word anchor for multi-word keys ---
        for product_key, parent in cls.PARENT_COMPANY_MAPPING.items():
            if not product_key:
                continue

            key_parts = product_key.split()
            if len(key_parts) < 2:
                continue

            first_word = key_parts[0]

            # Do not anchor on generic words. See is_brand_token() for
            # the classification rule. This is the same filter used by
            # _improved_partial_match, so both prefix-based matchers
            # respect a single vocabulary boundary.
            if not is_brand_token(first_word):
                continue

            if first_word in product_words:
                logger.info(
                    f"Found parent company for '{product_name}': {parent} "
                    f"(via first-word anchor: '{first_word}' in '{product_key}')"
                )
                return parent

        return None

    @classmethod
    @classmethod
    def extract_brand_from_product_text(
        cls, product_name: str) -> Optional[str]:
        """Enhanced brand extraction from product name using multiple strategies"""
        if not product_name or product_name.lower() in [
                "unknown", "generic product"]:
            return None

        product_lower = product_name.lower()

        # Strategy 1: Look for parent company mapping
        parent_company = cls.find_parent_company(product_name)
        if parent_company:
            return parent_company.title()

        # Strategy 2: Check for known brand patterns in product name (Excel data only)
        # Use the global certification_manager instance.
        #
        # Candidates are filtered through is_brand_token() so that a
        # single generic word in the sheet (which shouldn't exist, but
        # might if the corpus changes) cannot match as a brand.
        if certification_manager.data is not None:
            for brand in certification_manager.data.keys():
                if not is_brand_token(brand):
                    continue

                # Check if brand appears in product name
                if brand in product_lower:
                    # Get the original brand name from Excel data
                    first_product = next(iter(certification_manager.data[brand].values()))
                    original_brand = first_product.get("original_brand", brand.title())
                    logger.info(
                        f"Found brand '{original_brand}' directly in product name '{product_name}'"
                    )
                    return original_brand

                # Check for brand variations
                if brand in cls.BRAND_VARIATIONS:
                    for variation in cls.BRAND_VARIATIONS[brand]:
                        if variation in product_lower:
                            first_product = next(iter(certification_manager.data[brand].values()))
                            original_brand = first_product.get("original_brand", brand.title())
                            logger.info(
                                f"Found brand variation '{variation}' for '{brand}' in product name"
                            )
                            return original_brand

        # Strategy 3: Extract likely brand from beginning of product name
        words = product_name.split()
        if len(words) > 1 and certification_manager.data is not None:
            first_word = cls.normalize(words[0])
            second_word = cls.normalize(words[1]) if len(words) > 1 else ""

            # Check first word as potential brand
            if is_brand_token(first_word):
                for brand in certification_manager.data.keys():
                    if not is_brand_token(brand):
                        continue
                    if brand == first_word or brand.startswith(first_word):
                        first_product = next(iter(certification_manager.data[brand].values()))
                        original_brand = first_product.get("original_brand", brand.title())
                        logger.info(
                            f"Extracted brand '{original_brand}' from first word of product name"
                        )
                        return original_brand

            # Check first two words as potential brand
            if second_word and is_brand_token(first_word):
                first_two_words = f"{first_word} {second_word}"
                for brand in certification_manager.data.keys():
                    if not is_brand_token(brand):
                        continue
                    if brand == first_two_words or brand.startswith(first_two_words):
                        first_product = next(iter(certification_manager.data[brand].values()))
                        original_brand = first_product.get("original_brand", brand.title())
                        logger.info(
                            f"Extracted brand '{original_brand}' from first two words of product name"
                        )
                        return original_brand

        return None

# =====================================================================
# GENERIC_WORDS
#
# Words that appear across many unrelated brand names and therefore
# cannot, on their own, establish that two brands are the same.
#
# Built empirically from the actual brand corpus. Every word here was
# observed co-occurring across multiple unrelated brands in the
# grocery dataset. If you add a word to this set, you are asserting
# that two brands sharing ONLY this word should NOT be treated as a
# match. That is the correct default for category nouns ("body",
# "beauty"), function words ("the", "and"), and geographic modifiers
# ("new", "california"), but the WRONG default for brand tokens.
#
# When in doubt, do not add. False negatives (a real match rejected)
# are harder to notice than false positives (an unrelated brand
# inheriting certifications), but both are bugs. Prefer to err on
# the side of NOT adding unless the corpus clearly shows the word
# spanning unrelated brands.
# =====================================================================
GENERIC_WORDS = frozenset({
    # --- function words / articles ---
    "a", "an", "the", "and", "or", "of", "for", "with", "by", "to",
    "at", "in", "on", "as", "is", "it", "its", "my", "your", "our",
    "we", "us", "you", "they", "them", "this", "that", "these", "those",

    # --- product categories (nouns describing what the product IS) ---
    "body", "beauty", "skin", "skincare", "hair", "haircare", "care",
    "cosmetic", "cosmetics", "makeup", "nail", "nails", "lip", "lips",
    "eye", "eyes", "face", "facial", "hand", "hands", "foot", "feet",
    "bodycare", "bath", "soap", "soaps", "shampoo", "conditioner",
    "lotion", "cream", "balm", "oil", "oils", "serum", "serums",
    "cleanser", "toner", "mask", "masks", "scrub", "spa", "wellness",
    "health", "nutrition", "supplement", "supplements", "vitamin",
    "vitamins", "protein", "snack", "snacks", "candy", "chocolate",
    "coffee", "tea", "teas", "juice", "juices", "soda", "water",
    "milk", "yogurt", "cheese", "butter", "bread", "breads", "cereal",
    "cereals", "pasta", "sauce", "sauces", "dressing", "dressings",
    "condiment", "condiments", "seasoning", "seasonings", "spice",
    "spices", "herb", "herbs", "salt", "pepper", "sugar", "sweetener",
    "sweeteners", "syrup", "syrups", "flour", "flours", "baking",
    "cookies", "cracker", "crackers", "biscuit", "biscuits", "chip",
    "chips", "pretzel", "pretzels", "popcorn", "granola", "bar",
    "bars", "gum", "mint", "mints", "pet", "pets", "dog", "dogs",
    "cat", "cats", "food", "foods", "beverage", "beverages", "drink",
    "drinks", "meal", "meals", "kitchen", "dinner", "lunch",
    "breakfast", "recipe", "cafe", "caffe",

    # --- marketing / adjective words (positive-sounding, non-identifying) ---
    "natural", "bare", "naturals", "naturally", "organic", "organics",
    "fresh", "freshly", "pure", "purely", "simple", "simply",
    "clean", "cleaner", "cleaning", "clear", "bright", "bold",
    "good", "better", "best", "great", "grand", "green", "happy",
    "honest", "kind", "little", "modern", "native", "new", "open",
    "original", "prime", "proper", "proud", "real", "rebel", "smart",
    "sole", "sun", "super", "sweet", "true", "wild", "wise", "world",
    "love", "lovely", "joy", "peace", "free", "freedom", "golden",
    "gold", "silver", "silky", "smooth", "soft", "warm", "cool",
    "white", "black", "red", "blue", "yellow", "orange", "brown",
    "artisan", "artisanal", "craft", "crafted", "handmade", "homemade", "old",
    "premium", "classic", "traditional", "vintage", "retro",
    "essential", "essentials", "quality", "value", "select",
    "choice", "signature", "reserve", "heritage",
    "urban", "rural", "country", "coast", "coastal", "mountain",
    "river", "valley", "garden", "farm", "farmhouse", "homestead",
    "forest", "meadow", "field", "grove", "orchard", "ranch",
    "aura", "beyond", "high", "pink",   # NEW: positive-sounding modifiers

    # --- geographic (too generic to identify a brand) ---
    "york", "california", "brooklyn", "paris", "london",
    "north", "south", "east", "west", "american", "america", "usa",
    "san", "los", "angeles", "city", "island", "hawaii", "hawai'i",
    "texas", "vegas", "florida", "chicago", "boston",
    "seattle", "portland", "denver", "austin", "nashville",
    "canada", "canadian", "mexico", "mexican", "italy", "italian",
    "france", "french", "spain", "spanish", "germany", "german",
    "japan", "japanese", "china", "chinese", "india", "indian",
    "korea", "korean", "thai", "vietnam", "vietnamese",

    # --- natural-world nouns (things-in-the-world any brand might name itself after) ---
    # NEW GROUP. Distinct from marketing words: these are concrete nouns,
    # but they are common enough that many unrelated brands have adopted
    # them. "Earth Breeze" and "Earth Mama" are unrelated; the shared
    # word "earth" does not establish shared brand identity.
    "earth", "mineral", "moon", "sea",

    # --- venues and landmarks (physical locations a brand might name itself after) ---
    # NEW GROUP. Same reasoning as natural-world nouns: common enough
    # that many unrelated brands have adopted them.
    "castle",

    # --- titles and honorifics (attached to a distinctive token; not identifying alone) ---
    # NEW GROUP. These behave like "mr", "mrs", "dr", "st" — they appear
    # as the first token of many brand names, always followed by the
    # distinctive part. "King" alone does not identify King Arthur
    # Baking or King's Hawaiian.
    "king",

    # --- business / corporate (survive normalization in some forms) ---
    "brand", "brands", "store", "stores", "market", "markets",
    "shop", "shops", "company", "companies", "group", "groups",
    "holdings", "industries", "industry", "enterprises", "ventures",
    "solutions", "systems", "products", "product", "ingredients",
    "labs", "lab", "laboratories", "laboratory", "trading", "distillers",
    "distillery", "brewery", "brewing", "roasters", "roasting",
    "manufacturing", "wholesale", "retail", "distribution", "logistics",
    "international", "global", "national", "united",
    "standard", "excellence",

    # --- time / calendar ---
    "day", "days", "daily", "night", "morning", "evening", "summer",
    "winter", "spring", "autumn", "fall", "week", "weekend", "sunday",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",

    # --- common proper names (first names, surnames) ---
    # NEW GROUP. A common name alone does not identify a brand:
    # "Ella's Kitchen" is a brand, "Ella's" is not. The same
    # distinction that applies to "Earth Mama" vs "Earth" applies here.
    # This group exists so that future additions of this kind have an
    # obvious home. Note the plural "ellas": BrandNormalizer strips the
    # apostrophe from "Ella's", so the sheet key is "ellas kitchen".
    "ella", "ellas",
})
# Hardcoded ambiguous forms used as a fallback if the CSV is missing.
# The CSV is the primary source; this set only matters when
# product_form_categories.csv can't be loaded.
FALLBACK_AMBIGUOUS_FORMS = frozenset({
    "soap", "oil", "cream", "lotion", "spray", "powder",
    "snacks", "candy", "chocolate", "cookies",
})
# =====================================================================
# FUNCTION_WORDS
#
# The subset of GENERIC_WORDS that consists of function words:
# articles, conjunctions, prepositions, pronouns, and short linking
# words. These never carry meaning about a brand's identity.
#
# Everything else in GENERIC_WORDS is a "content word that is
# generic in isolation" — like "honest", "best", "candy", "company",
# "foods". Those words are not distinctive on their own (one generic
# word is not a brand) but ARE distinctive in combination (two
# generic words together can be a brand name, e.g. "Honest Company").
#
# This distinction drives two things:
#   - The normalizer refuses to strip a suffix if doing so would
#     reduce a multi-word name to a single content word.
#   - The partial matcher treats two shared content words as
#     sufficient evidence of a match, even if both are generic.
# =====================================================================
FUNCTION_WORDS = frozenset({
    "a", "an", "the",
    "and", "or",
    "of", "for", "with", "by", "to", "at", "in", "on", "as",
    "is", "it", "its",
    "my", "your", "our",
    "we", "us", "you",
    "they", "them",
    "this", "that", "these", "those",
})


def _content_word_count(text: str) -> int:
    """Count words in `text` that are not function words.

    Function words (articles, conjunctions, prepositions, pronouns)
    carry no brand-identifying information. Content words — even
    generic ones like "honest" or "company" — can.
    """
    if not text:
        return 0
    return sum(1 for w in text.split() if w not in FUNCTION_WORDS)

def is_brand_token(word: str) -> bool:
    """Return True if `word` could plausibly identify a brand on its own.

    A token is a brand token if it is:
      - non-empty
      - at least 3 characters long (shorter tokens are almost always
        function words, honorifics, or coincidental fragments)
      - not in GENERIC_WORDS

    This is the single point of truth for the classification. Every
    matcher in this module that needs to decide "should this token be
    treated as evidence of brand identity?" calls this function, so
    that adding a word to GENERIC_WORDS automatically affects every
    matcher at once.

    Callers should not consult GENERIC_WORDS directly. If a caller
    needs a different rule (e.g., a different minimum length), add a
    parameter here rather than duplicating the membership test at the
    call site.
    """
    if not word:
        return False
    if len(word) < 3:
        return False
    return word not in GENERIC_WORDS


class CertificationManager:
    """Manage all certification-related operations"""

    def __init__(self):
        self.data = None
        self.brand_categories = None  # Track categories per brand
        self.last_loaded = None
        self._lock = threading.Lock()
        # Inverted indices for the /sheet-search endpoints.
        # Built at the end of load_certification_data().
        # Both map token -> list of (brand_key, product_key) refs
        # into self.data.
        self.category_index = {}   # Column B (en: taxonomy codes)
        self.cert_cat_index = {}   # Column H (product-form tokens)
        # Product-form → en: category lookup.
        # Loaded from product_form_categories.csv at startup.
        # See load_product_form_categories() below.
        self.product_form_categories = {}   # form -> en: category
        self.product_form_notes = {}        # form -> notes
        self.product_form_suggestions = {}  # form -> comma-joined sub-forms

    def load_certification_data(self) -> bool:
        """Load certification data from Excel file and build category index"""
        try:
            if os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE):
                logger.info(
                    f"Loading certification data from {FileConfig.CERTIFICATION_EXCEL_FILE}"
                )

                # First get pandas, then use it
                pd = get_pandas()
                df = pd.read_excel(FileConfig.CERTIFICATION_EXCEL_FILE)
                logger.info(f"Excel file loaded. Columns: {list(df.columns)}")

                cert_data = {}
                brand_categories = {}  # NEW: Track categories per brand

                for _, row in df.iterrows():
                    # Use "Product_Brand" column from new file
                    brand = None
                    if "Product_Brand" in df.columns:
                        brand_value = row.get("Product_Brand")
                        if not pd.isna(brand_value):
                            brand = str(brand_value).strip()

                    if not brand:
                        continue

                    brand_normalized = BrandNormalizer.normalize(brand)

                    # NEW: Get category for this row
                    category = ""
                    if "Category" in df.columns:
                        category_value = row.get("Category")
                        if not pd.isna(category_value):
                            category = str(category_value).strip()

                    # NEW: Track categories for this brand
                    if brand_normalized not in brand_categories:
                        brand_categories[brand_normalized] = set()
                    if category:
                        brand_categories[brand_normalized].add(category)

                    # Get certifications with exact column names
                    certifications = self._extract_certifications(
                        row, df.columns)

                    # Store brand data with category as secondary key (store all products)
                    if brand_normalized not in cert_data:
                        cert_data[brand_normalized] = {}

                    # Use category as the key to store product-specific data
                    product_key = category if category else "_default"

                    # Store this product's data
                    cert_data[brand_normalized][product_key] = {
                        "original_brand": brand,
                        "certifications": certifications,
                        "research_complete": certifications.get("research_complete", False),
                        "row_data": row.to_dict(),
                        "category": category,  # Store category for reference
                    }

                self.data = cert_data
                self.brand_categories = brand_categories
                self.last_loaded = datetime.now()

                logger.info(f"Loaded {len(cert_data)} certification records")
                logger.info(f"Loaded category index for {len(brand_categories)} brands")

                # Build inverted indices for /sheet-search endpoints
                self._build_search_indices()

                # Sanity-check the alias and synonym maps. A key in either
                # map that is itself a generic word would cause the map's
                # canonical value to be substituted for the generic word
                # at normalization time, producing false matches on
                # queries for that word. Warn once at load time rather
                # than discovering it via a user report.
                self._check_alias_maps_for_generic_keys()
                self.load_product_form_categories()

                # ✅ ADD THIS: Verify data is ready
                if self.data is not None and len(self.data) > 0:
                    logger.info(f"✅ Data verification: {len(self.data)} records ready")
                    return True
                else:
                    logger.warning("⚠️ Data appears empty, retrying...")
                    time.sleep(1)
                    return self.load_certification_data()
            else:
                logger.warning(
                    f"Certification Excel file {FileConfig.CERTIFICATION_EXCEL_FILE} not found"
                )
                return False
        except Exception as e:
            logger.error(f"Error loading certification data: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return False

    @staticmethod
    def _check_alias_maps_for_generic_keys() -> None:
        """Warn if any alias or synonym key is a generic word.

        BrandNormalizer.normalize applies BRAND_ALIASES and
        BRAND_SYNONYMS as text substitutions. If a key is itself a
        generic word, then normalize("good") would replace "good" with
        whatever canonical value the map points to, which is almost
        certainly not the intent and will produce false matches at
        query time. This check runs once at load time and logs a
        warning for each offending key. It does not raise, so a bad
        map does not prevent the app from starting.
        """
        for map_name, alias_map in (
            ("BRAND_ALIASES", BrandNormalizer.BRAND_ALIASES),
            ("BRAND_SYNONYMS", BrandNormalizer.BRAND_SYNONYMS),
        ):
            for key in alias_map.keys():
                if key in GENERIC_WORDS:
                    logger.warning(
                        f"{map_name} contains generic word key {key!r} "
                        f"-> {alias_map[key]!r}. This will cause normalize() "
                        f"to substitute the canonical value for any occurrence "
                        f"of {key!r}, producing false matches at query time."
                    )

    def _extract_certifications(self, row, columns) -> Dict[str, bool]:
        """Extract certifications from a row"""
        cert_mapping = {
            "b_corp": ["B_Corp", "B Corp", "b_corp", "B Corp Certification", "bcorp"],
            "fair_trade": [
                "Fair_Trade",
                "Fair Trade",
                "fair_trade",
                "Fair Trade Certified",
                "fairtrade",
            ],
            "rainforest_alliance": [
                "Rainforest_Alliance",
                "Rainforest Alliance",
                "rainforest_alliance",
                "Rainforest Alliance Certified",
                "rainforest",
            ],
            "leaping_bunny": [
                "Leaping_Bunny",
                "Leaping Bunny",
                "leaping_bunny",
                "Cruelty Free",
                "leapingbunny",
            ],
            "research_complete": [
                "Research_Complete",
                "Research Complete",
                "research_complete",
                "Research Done",
                "research_done",
            ],
        }

        pd = get_pandas()

        certifications = {}
        for cert_type, possible_names in cert_mapping.items():
            value = False
            for col_name in possible_names:
                if col_name in columns:
                    cell_value = row.get(col_name)
                    if pd.isna(cell_value):
                        value = False
                    elif isinstance(cell_value, bool):
                        value = cell_value
                    elif isinstance(cell_value, (int, float)):
                        value = bool(cell_value)
                    elif isinstance(cell_value, str):
                        cell_value_lower = cell_value.strip().lower()
                        if cell_value_lower in ["true", "yes", "y", "1", "t"]:
                            value = True
                        elif cell_value_lower in ["false", "no", "n", "0", "f"]:
                            value = False
                    break
            certifications[cert_type] = value

        return certifications

    def _build_search_indices(self) -> None:
        """Build inverted indices for /sheet-search endpoints.

        category_index: token -> [(brand_key, product_key), ...]
            Tokens from Column B (Category). One per row. Uses the
            en: taxonomy code verbatim, lowercased. Serves the
            researcher-mode /sheet-search/taxonomy endpoint.

        cert_cat_index: token -> [(brand_key, product_key), ...]
            Tokens from Column H (Certification_Categories). Split on
            comma, trimmed, lowercased. Column H is now clean plain
            text (no parentheticals, no en: prefix, no hyphens, no
            uppercase). Serves the shopper-mode /sheet-search
            endpoint.

        Both indices point into self.data, so they stay consistent
        with it by construction. Rebuilt on every load; never mutated
        incrementally.
        """
        self.category_index = {}
        self.cert_cat_index = {}

        if self.data is None:
            return

        for brand_key, products in self.data.items():
            for product_key, product_data in products.items():
                if product_key == "_default":
                    continue
                ref = (brand_key, product_key)

                # Column B token (one per row)
                cat_token = product_key.strip().lower()
                if cat_token:
                    self.category_index.setdefault(cat_token, []).append(ref)

                # Column H tokens (many per row)
                row = product_data.get("row_data") or {}
                raw_h = row.get("Certification_Categories")
                if raw_h is None:
                    continue
                try:
                    if raw_h != raw_h:  # NaN check without importing pandas
                        continue
                except Exception:
                    pass
                h_str = str(raw_h).strip().lower()
                if not h_str:
                    continue
                for token in h_str.split(","):
                    token = token.strip()
                    if token:
                        self.cert_cat_index.setdefault(token, []).append(ref)

        logger.info(
            f"Search indices built: {len(self.category_index)} taxonomy tokens, "
            f"{len(self.cert_cat_index)} product-form tokens"
        )
    def load_product_form_categories(self) -> None:
        """Load product_form_categories.csv into the three dicts.

        The CSV is generated once by extract_product_forms.py and
        reviewed by a human before commit. Missing file is not fatal;
        the Browse tab's ambiguous handling falls through to the
        free-form fallback in that case.
        """
        path = "product_form_categories.csv"
        if not os.path.exists(path):
            logger.warning(
                "product_form_categories.csv not found; "
                "ambiguous-form handling will use the free-form fallback"
            )
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    form = (row.get("product_form") or "").strip().lower()
                    if not form:
                        continue
                    self.product_form_categories[form] = (
                        row.get("category") or ""
                    ).strip()
                    self.product_form_notes[form] = (
                        row.get("notes") or ""
                    ).strip()
                    self.product_form_suggestions[form] = (
                        row.get("suggestions") or ""
                    ).strip()
            logger.info(
                "Loaded product_form_categories.csv: %d forms",
                len(self.product_form_categories),
            )
        except Exception as e:
            logger.error("Error loading product_form_categories.csv: %s", e)

    def _find_best_category_match(self, brand_normalized: str, category: str) -> Optional[Dict]:
        """Find best category match for barcode scans when exact match fails"""
        category_lower = category.strip().lower()
        best_match = None
        best_score = 0
        match_confidence = "low"  # Track confidence level

        # Check exact brand match (HIGHEST CONFIDENCE)
        if brand_normalized in self.data:
            match_confidence = "high"
            for product_key, product_data in self.data[brand_normalized].items():
                if product_key == "_default":
                    continue

                product_key_lower = product_key.lower()
                if category_lower in product_key_lower or product_key_lower in category_lower:
                    score = len(category_lower) if category_lower in product_key_lower else len(product_key_lower)
                    if score > best_score:
                        best_score = score
                        best_match = product_data
                        logger.info(f"Found exact brand category match: '{category}' → '{product_key}'")

        # Check partial brand matches (LOWER CONFIDENCE - ONLY if exact brand not found)
        if not best_match:
            for stored_brand, products in self.data.items():
                if self._improved_partial_match(brand_normalized, stored_brand):
                    # Check if this is a high-confidence partial match (2+ words).
                    # Uses the module-level GENERIC_WORDS set so this filter
                    # stays in sync with _improved_partial_match.
                    search_words = set(brand_normalized.split())
                    stored_words = set(stored_brand.split())
                    common_words = search_words & stored_words
                    meaningful_common = [w for w in common_words if w not in GENERIC_WORDS]

                    # Only use partial match if at least 2 meaningful words match.
                    if len(meaningful_common) >= 2:
                        match_confidence = "medium"
                    else:
                        # A single shared word is not enough to establish
                        # brand identity in this code path either. Skip.
                        match_confidence = "low"
                        continue  # Skip low-confidence partial matches

                    for product_key, product_data in products.items():
                        if product_key == "_default":
                            continue

                        product_key_lower = product_key.lower()
                        if category_lower in product_key_lower or product_key_lower in category_lower:
                            score = len(category_lower) if category_lower in product_key_lower else len(product_key_lower)
                            if score > best_score:
                                best_score = score
                                best_match = product_data
                                logger.info(f"Found partial category match via partial brand: '{category}' → '{product_key}'")

        # ===== SAFETY CHECK: Only return match if confidence is high enough =====
        if best_match and match_confidence in ["high", "medium"]:
            return best_match

        # If match_confidence is "low", don't return anything (prevents false positives)
        logger.info(f"Rejected low-confidence brand match for '{brand_normalized}' → category '{category}'")
        return None

    @staticmethod
    def _improved_partial_match(search_brand: str, stored_brand: str) -> bool:
        """Partial brand match honoring the "two content words"
        principle.

        A single generic word is not an identifier, but two content
        words together are — even if both are generic. "Honest
        Company" matches "The Honest Company" because they share the
        content words "honest" and "company". "Honest" alone does
        not match, because it shares only one content word with the
        stored name and that word is generic.

        Rule:
          1. Exact match                                -> True
          2. Two or more shared content words           -> True
          3. One shared content word, non-generic       -> True only if
             substring containment or similarity >= 0.85
          4. One shared content word, generic           -> False
          5. Zero shared content words                  -> False
        """
        if search_brand == stored_brand:
            return True

        search_words = set(search_brand.split())
        stored_words = set(stored_brand.split())
        common = search_words & stored_words

        # Content words are words that aren't function words
        # (articles, conjunctions, prepositions, pronouns).
        content_common = {w for w in common if w not in FUNCTION_WORDS}

        # Two or more content words shared -> match.
        # This handles "honest company" vs "the honest company".
        if len(content_common) >= 2:
            return True

        # Exactly one content word shared.
        if len(content_common) == 1:
            shared = next(iter(content_common))
            # A single generic word is not distinctive.
            if shared in GENERIC_WORDS:
                return False
            # Otherwise require near-identity or substring containment.
            if search_brand in stored_brand or stored_brand in search_brand:
                return True
            similarity = SequenceMatcher(None, search_brand, stored_brand).ratio()
            return similarity >= 0.85

        # No content words shared -> no match.
        return False

    def get_certifications(self, brand: str, category: str = None, source: str = "manual") -> Dict[str, Any]:
        """
        Get certifications for a brand from Excel data, filtered by category.

        Args:
            brand: Brand name to look up
            category: Product category (required for multi-category brands unless source is "barcode")
            source: "barcode" (OFF provided category) or "manual" (user selected)
        """
        # ===== CHECK DATA WITH LOCK (prevents reload failures) =====
        with self._lock:
            need_load = (
                self.data is None
                or self.last_loaded is None
                or (datetime.now() - self.last_loaded).seconds > 3600
            )

            if need_load:
                logger.info("🔄 Loading/Reloading certification data...")
                self.load_certification_data()

                if self.data is None:
                    logger.error("❌ Data still None after load attempt")
                    return self._get_default_response(
                        found=False,
                        match_type="data_not_loaded",
                        note="Certification data is loading. Please try again in a moment."
                    )

        if not brand or brand.lower() in ["unknown", "n/a", ""]:
            logger.info("Empty brand requested, returning default certifications")
            return self._get_default_response(found=False, match_type="no_brand")

        brand_normalized = BrandNormalizer.normalize(brand)
        logger.info(f"Looking for certifications for brand: '{brand_normalized}', category: '{category}', source: '{source}'")

        # ===== STEP 1: Try exact brand + category match if category provided =====
        #
        # Gather ALL matching (stored_brand, product_data) pairs for the
        # requested category, not just the first. If exactly one match,
        # return it as exact_product. If more than one, the user's search
        # is a parent brand name that spans multiple products in the same
        # category (e.g., "Chobani" with en:dairies when both Chobani
        # Yogurt and Chobani Oat Milk exist). Return ambiguous_brand with
        # the candidate list, mirroring STEP 2's behavior for the
        # no-category case.
        if category and category.strip():
            category_lower = category.strip().lower()
            category_matches = []
            for stored_brand, products in self.data.items():
                if not self._improved_partial_match(brand_normalized, stored_brand):
                    continue
                for product_key, product_data in products.items():
                    if product_key != "_default" and product_key.lower() == category_lower:
                        category_matches.append((stored_brand, product_data))
                        break  # only one row per brand/category pair

            if len(category_matches) == 1:
                stored_brand, product_data = category_matches[0]
                logger.info(
                    f"Found exact brand+category match: "
                    f"brand='{stored_brand}', category='{category}'"
                )
                return self._format_response(
                    found=True,
                    data=product_data,
                    search_brand=brand,
                    match_type="exact_product"
                )
            elif len(category_matches) > 1:
                candidates = [m[0] for m in category_matches]
                logger.info(
                    f"Ambiguous brand+category match for '{brand_normalized}' "
                    f"with category '{category}': {candidates}"
                )
                return self._get_default_response(
                    found=False,
                    match_type="ambiguous_brand",
                    note=f"Multiple brands match '{brand}' in {category}: {', '.join(sorted(candidates))}. Please be more specific."
                )
            # else: zero matches; fall through to STEP 2

        # ===== STEP 2: Check if brand exists in our data =====
        brand_exists = False
        matched_brand = None
        matched_products = None

        # Check exact brand match
        if brand_normalized in self.data:
            brand_exists = True
            matched_brand = brand_normalized
            matched_products = self.data[brand_normalized]  # This is now a dict of products by category
        else:
            # Check partial brand match - gather ALL matches to detect ambiguity
            partial_matches = []
            for stored_brand, products in self.data.items():
                if self._improved_partial_match(brand_normalized, stored_brand):
                    partial_matches.append((stored_brand, products))

            if len(partial_matches) == 1:
                # Exactly one match - safe to use
                matched_brand, matched_products = partial_matches[0]
                brand_exists = True
                logger.info(f"Single partial match found: '{brand_normalized}' -> '{matched_brand}'")
            elif len(partial_matches) > 1:
                # Multiple matches - try to narrow down by category
                if category and category.strip():
                    category_lower = category.strip().lower()
                    category_matches = []
                    for stored_brand, products in partial_matches:
                        has_category = any(
                            product_key != "_default" and product_key.lower() == category_lower
                            for product_key in products.keys()
                        )
                        if has_category:
                            category_matches.append((stored_brand, products))

                    if len(category_matches) == 1:
                        matched_brand, matched_products = category_matches[0]
                        brand_exists = True
                        logger.info(f"Category '{category}' narrowed partial match to: '{matched_brand}'")
                    else:
                        candidates = [m[0] for m in partial_matches]
                        logger.info(f"Ambiguous brand match for '{brand_normalized}' with category '{category}': {candidates}")
                        return self._get_default_response(
                            found=False,
                            match_type="ambiguous_brand",
                            note=f"Multiple brands match '{brand}': {', '.join(sorted(candidates))}. Please be more specific."
                        )
                else:
                    candidates = [m[0] for m in partial_matches]
                    logger.info(f"Ambiguous brand match for '{brand_normalized}': {candidates}")
                    return self._get_default_response(
                        found=False,
                        match_type="ambiguous_brand",
                        note=f"Multiple brands match '{brand}': {', '.join(sorted(candidates))}. Please be more specific."
                    )
            # else: brand_exists remains False - fall through to parent company handling

        if brand_exists and matched_products:
            # Get categories this brand appears in
            categories = [k for k in matched_products.keys() if k != "_default"]
            is_single_category = len(categories) == 1

            # Get the first product's category for single-category case
            first_category = categories[0] if categories else None
            first_product_data = matched_products.get(first_category) if first_category else matched_products.get("_default")

            # CASE A: Single-category brand - safe to use brand-only match
            if is_single_category:
                single_category = categories[0]
                product_data = matched_products[single_category]
                logger.info(f"Brand '{matched_brand}' appears only in category '{single_category}' - using brand-only match")
                return self._format_response(
                    found=True,
                    data=product_data,
                    search_brand=brand,
                    match_type="brand_only_single_category",
                    matched_category=single_category,
                    note=f"This brand only produces {single_category} products."
                )

            # CASE B: Barcode scan with category from OFF
            elif source == "barcode" and category:
                # Try to find best category match
                best_match = self._find_best_category_match(brand_normalized, category)
                if best_match:
                    # Get the category from the matched product data
                    best_category = best_match.get("category", "")
                    if not best_category:
                        # Fallback to row_data
                        best_row_data = best_match.get("row_data", {})
                        best_category = best_row_data.get("Category", "") if best_row_data else ""

                    logger.info(f"Found category match for barcode: '{category}' → '{best_category}'")
                    return self._format_response(
                        found=True,
                        data=best_match,
                        search_brand=brand,
                        match_type="barcode_category_match",
                        matched_category=best_category,
                        note=f"Matched to {best_category} category based on product information."
                    )
                else:
                    # No category match found - return unknown
                    logger.info(f"No category match for '{brand}' with category '{category}'")
                    return self._get_default_response(
                        found=False,
                        match_type="no_category_match",
                        note=f"Product category '{category}' not found in database for this brand."
                    )

            # CASE C: Manual search with category provided but not exact match
            elif category and category.strip():
                # Try partial category match
                best_match = self._find_best_category_match(brand_normalized, category)
                if best_match:
                    # Get the category from the matched product data
                    best_category = best_match.get("category", "")
                    if not best_category:
                        best_row_data = best_match.get("row_data", {})
                        best_category = best_row_data.get("Category", "") if best_row_data else ""

                    logger.info(f"Found partial category match for manual search: '{category}' → '{best_category}'")
                    return self._format_response(
                        found=True,
                        data=best_match,
                        search_brand=brand,
                        match_type="partial_category_match",
                        matched_category=best_category,
                        note=f"Best match found in {best_category} category. Please verify."
                    )
                else:
                    # No match - require category selection
                    return self._get_default_response(
                        found=False,
                        match_type="category_required",
                        note=f"This brand makes multiple product types. Please select a category: {', '.join(categories)}"
                    )

            # CASE D: Manual search without category for multi-category brand
            else:
                logger.info(f"Brand '{matched_brand}' appears in {len(categories)} categories. Category required.")
                return self._get_default_response(
                    found=False,
                    match_type="category_required",
                    note=f"This brand makes multiple product types. Please select a category: {', '.join(categories)}"
                )

        # ===== STEP 3: Check parent company =====
        parent_company = BrandNormalizer.find_parent_company(brand)
        if parent_company:
            parent_normalized = BrandNormalizer.normalize(parent_company)
            if parent_normalized in self.data:
                logger.info(f"Using parent company '{parent_company}' for '{brand}'")
                # For parent company, get the first product
                parent_products = self.data[parent_normalized]
                parent_categories = [k for k in parent_products.keys() if k != "_default"]
                if parent_categories:
                    parent_data = parent_products[parent_categories[0]]
                else:
                    parent_data = parent_products.get("_default")

                if parent_data:
                    return self._format_response(
                        found=True,
                        data=parent_data,
                        search_brand=brand,
                        match_type="parent_company",
                        note=f"Using parent company '{parent_company}' certifications."
                    )

        # ===== STEP 4: No match found =====
        logger.info(f"No match found for brand: '{brand}' with category: '{category}'")
        return self._get_default_response(
            found=False,
            match_type="no_match",
            note="Brand not found in certification database."
        )

    def _get_default_response(self, found: bool = False, match_type: str = "no_match", note: str = None) -> Dict[str, Any]:
        """Get default certification response with match metadata"""
        return {
            "found": found,
            "match_type": match_type,
            "note": note,
            "certifications": {
                "b_corp": False,
                "fair_trade": False,
                "rainforest_alliance": False,
                "leaping_bunny": False,
                "research_complete": False,
            },
            "details": None,
        }

    def _format_response(
        self,
        found: bool,
        data: Dict,
        search_brand: str = None,
        match_type: str = "exact_match",
        matched_category: str = None,
        note: str = None
    ) -> Dict[str, Any]:
        """Format certification response with match metadata"""
        response = {
            "found": found,
            "match_type": match_type,
            "note": note,
            "certifications": data["certifications"],
            "details": {
                "original_brand": data["original_brand"],
                "row_data": data.get("row_data", {}),
            },
        }

        if matched_category:
            response["matched_category"] = matched_category

        # If we found a match and the original brand differs from search, include canonical
        if (
            found
            and search_brand
            and data["original_brand"].lower() != search_brand.lower()
        ):
            response["canonical_brand"] = data["original_brand"]
            response["search_brand_used"] = search_brand

        return response

# ==================== SCORING MANAGER ====================

class ScoringManager:
    """Manage all scoring-related operations"""

    @staticmethod
    def calculate_brand_scores(
        brand: str,
        category: str = None,
        cert_result: Dict[str, Any] = None,
    ) -> BrandData:
        """
        Calculate scores for a brand using priority order:
        1. Parent company identification (for product search)
        2. Dynamic calculation from certifications (Excel)

        If cert_result is provided, it is reused instead of performing
        a second certification lookup inside the scoring path. Callers
        that have already fetched certifications (e.g. scan_product and
        get_product_info) should pass it in to avoid double work.
        """
        # Handle empty/unknown brand
        if not brand or brand == "Unknown":
            return BrandData(
                brand=brand,
                social=safe_float(ScoringConfig.BASE_SCORE),
                environmental=safe_float(ScoringConfig.BASE_SCORE),
                economic=safe_float(ScoringConfig.BASE_SCORE),
                certifications=[],
                scoring_method="base_score_only",
                notes="Base score of 5.0 (no brand identified)",
            )

        brand_normalized = BrandNormalizer.normalize(brand)

        # Step 1: Check if this is a product that should inherit parent company scores
        parent_company = BrandNormalizer.find_parent_company(brand)
        if parent_company:
            parent_normalized = BrandNormalizer.normalize(parent_company)

            # Only skip inheritance for specific brands that should NOT inherit
            # (like Digiorno, which has its own empty entry)
            non_inheriting_brands = ["digiorno", "bai", "nestle pure life", "pure life"]

            if brand_normalized in non_inheriting_brands:
                logger.info(f"Brand {brand} in non-inheriting list, skipping parent inheritance")
                # Fall through to dynamic calculation
            else:
                logger.info(f"Found parent company '{parent_company}' for '{brand}' - will use dynamic calculation")
                # Fall through to dynamic calculation

        # Step 2: Dynamic calculation from certifications - PASS THE CATEGORY
        logger.info(
            f"Brand '{brand_normalized}' calculating dynamically"
        )
        return ScoringManager._calculate_dynamic_scores(brand, category, cert_result)

    @staticmethod
    def _calculate_dynamic_scores(
        brand: str,
        category: str = None,
        cert_result: Dict[str, Any] = None,
    ) -> BrandData:
        """Calculate scores dynamically from certifications"""
        # Start with base score
        social_score = ScoringConfig.BASE_SCORE
        environmental_score = ScoringConfig.BASE_SCORE
        economic_score = ScoringConfig.BASE_SCORE

        # Get all certifications from combined sources
        all_certifications = ScoringManager._get_all_certifications(brand, category, cert_result)

        # Apply certification bonuses
        bonus_applied = False
        for cert in all_certifications:
            if cert in ScoringConfig.CERTIFICATION_BONUSES:
                bonus = ScoringConfig.CERTIFICATION_BONUSES[cert]
                social_score += bonus["social"]
                environmental_score += bonus["environmental"]
                economic_score += bonus["economic"]
                bonus_applied = True

        # Apply multi-certification bonus if applicable
        if bonus_applied and len(all_certifications) > 1:
            multi_bonus = (len(all_certifications) - 1) * \
                ScoringConfig.MULTI_CERT_BONUS
            social_score += multi_bonus
            environmental_score += multi_bonus
            economic_score += multi_bonus

        # Cap scores at 10.0
        social_score = min(10.0, social_score)
        environmental_score = min(10.0, environmental_score)
        economic_score = min(10.0, economic_score)

        return BrandData(
            brand=brand,
            social=safe_float(social_score),
            environmental=safe_float(environmental_score),
            economic=safe_float(economic_score),
            certifications=all_certifications,
            scoring_method="dynamic_calculation",
            multi_cert_bonus=safe_float(
                (len(all_certifications) - 1) * ScoringConfig.MULTI_CERT_BONUS
                if bonus_applied and len(all_certifications) > 1
                else 0.0
            ),
            notes="Base 5.0 + certification bonuses + multi-cert bonus (capped at 10.0)",
        )

    @staticmethod
    def _get_all_certifications(
        brand: str,
        category: str = None,
        cert_result: Dict[str, Any] = None,
    ) -> List[str]:
        """Get all certifications from Excel database only.

        If cert_result is provided, it is used directly. Otherwise a
        fresh lookup is performed via certification_manager.
        cert_result is the dict returned by
        certification_manager.get_certifications, with a
        "certifications" sub-dict keyed by b_corp, fair_trade,
        rainforest_alliance, leaping_bunny, research_complete.
        """
        brand_normalized = BrandNormalizer.normalize(brand)

        # Use the caller-provided result if available; otherwise look up.
        if cert_result is not None:
            excel_certs = cert_result
        else:
            excel_certs = certification_manager.get_certifications(brand, category)

        # Build certification list from Excel data only
        excel_cert_list = []
        if excel_certs["certifications"]["b_corp"]:
            excel_cert_list.append("B Corp")
        if excel_certs["certifications"]["fair_trade"]:
            excel_cert_list.append("Fair Trade")
        if excel_certs["certifications"]["rainforest_alliance"]:
            excel_cert_list.append("Rainforest Alliance")
        if excel_certs["certifications"]["leaping_bunny"]:
            excel_cert_list.append("Leaping Bunny")

        # Return Excel certifications only
        return excel_cert_list


# ==================== OPEN FOOD FACTS CLIENT ====================


class OpenFoodFactsClient:
    """Client for Open Food Facts API"""

    @staticmethod
    async def search_by_name(
        product_name: str, max_results: int = 20
    ) -> Dict[str, Any]:
        """Enhanced search Open Food Facts by product name with better brand extraction"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                encoded_name = quote(product_name)
                url = f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={encoded_name}&search_simple=1&action=process&json=1&page_size={max_results}"

                response = await client.get(
                    url, headers={"User-Agent": "TBLGroceryScanner/1.0"}
                )

                if response.status_code == 200:
                    data = response.json()
                    products = data.get("products", [])

                    if not products:
                        return {
                            "found": False,
                            "message": "No products found",
                            "products": [],
                            "brand_analysis": {},
                        }

                    return OpenFoodFactsClient._analyze_products(products)
                else:
                    return {
                        "found": False,
                        "message": f"Open Food Facts API error: {response.status_code}",
                        "products": [],
                        "brand_analysis": {},
                    }
        except Exception as e:
            logger.error(
                f"Open Food Facts search error for '{product_name}': {e}")
            return {
                "found": False,
                "message": f"Search error: {str(e)}",
                "products": [],
                "brand_analysis": {},
            }

    @staticmethod
    def _analyze_products(products: List[Dict]) -> Dict[str, Any]:
        """Analyze products to extract brand information"""
        brand_candidates = []
        brand_details = {}

        for product in products:
            # Extract brand from multiple fields with priority
            brand_fields_priority = [
                ("brands", 2.0),
                ("brand", 1.8),
                ("brand_owner", 1.5),
                ("manufacturer", 1.3),
            ]

            for field, weight in brand_fields_priority:
                if field in product and product[field]:
                    field_value = str(product[field]).strip()
                    if field_value and field_value.lower() not in [
                        "",
                        "unknown",
                        "n/a",
                        "none",
                    ]:
                        # Split by common separators
                        for separator in [",", ";", "/", "|", "&", "+"]:
                            if separator in field_value:
                                parts = [
                                    p.strip()
                                    for p in field_value.split(separator)
                                    if p.strip()
                                ]
                                for part in parts:
                                    if part and len(part) > 1:
                                        # Add multiple times based on weight
                                        for _ in range(int(weight)):
                                            brand_candidates.append(part)
                                break
                        else:
                            # No separator, add the whole value
                            for _ in range(int(weight)):
                                brand_candidates.append(field_value)

                        # Store details for the first occurrence
                        normalized_brand = BrandNormalizer.normalize(
                            field_value.split(",")[0]
                            if "," in field_value
                            else field_value
                        )
                        if normalized_brand not in brand_details:
                            brand_details[normalized_brand] = {
                                "original_brand": field_value, "product_name": product.get(
                                    "product_name", ""), "product_id": product.get(
                                    "code", ""), "categories": product.get(
                                    "categories", ""), "countries": product.get(
                                    "countries", ""), "source_field": field, }
                        break

        return OpenFoodFactsClient._analyze_brand_candidates(
            brand_candidates, brand_details, products
        )

    @staticmethod
    def _analyze_brand_candidates(
        brand_candidates: List[str], brand_details: Dict, products: List[Dict]
    ) -> Dict[str, Any]:
        """Analyze brand candidates to determine best match"""
        total_candidates = len(brand_candidates)

        if total_candidates == 0:
            return {
                "found": False,
                "message": "No brands found in search results",
                "products": products[:5],
                "brand_analysis": {},
            }

        # Calculate brand distribution
        brand_counts = Counter(brand_candidates)
        total_with_brands = sum(brand_counts.values())

        # Calculate percentages
        brand_percentages = {}
        for brand, count in brand_counts.items():
            if count > 0:
                percentage = (count / total_with_brands) * 100
                brand_percentages[brand] = round(percentage, 1)

        # Sort brands by frequency
        sorted_brands = sorted(
            brand_counts.items(),
            key=lambda x: x[1],
            reverse=True)
        top_brand = sorted_brands[0][0] if sorted_brands else None

        return {
            "found": True,
            "message": f"Found {len(products)} products",
            "products": products[: min(10, len(products))],
            "brand_analysis": {
                "total_products": len(products),
                "total_brand_candidates": total_candidates,
                "brand_counts": dict(sorted_brands[:10]),
                "brand_percentages": brand_percentages,
                "top_brand": top_brand,
                "brand_details": brand_details,
            },
        }

    @staticmethod
    async def lookup_barcode(barcode: str) -> Dict[str, Any]:
        """Lookup product from Open Food Facts with comprehensive data extraction"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # List of APIs to check in order
                apis = [
                    {
                        "url": f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json",
                        "name": "Open Food Facts"
                    },
                    {
                        "url": f"https://world.openpetfoodfacts.org/api/v0/product/{barcode}.json",
                        "name": "Open Pet Food Facts"
                    },
                    {
                        "url": f"https://world.openproductsfacts.org/api/v0/product/{barcode}.json",
                        "name": "Open Products Facts"
                    },
                    {
                        "url": f"https://world.openbeautyfacts.org/api/v0/product/{barcode}.json",
                        "name": "Open Beauty Facts"
                    }
                ]

                for api in apis:
                    response = await client.get(
                        api["url"],
                        headers={"User-Agent": "TBLGroceryScanner/1.0"},
                    )

                    if response.status_code == 200:
                        data = response.json()
                        if data.get("status") == 1:
                            product = data.get("product", {})
                            logger.info(f"Product found in {api['name']} for barcode: {barcode}")
                            return OpenFoodFactsClient._extract_product_info(
                                barcode, product
                            )

        except Exception as e:
            logger.error(
                f"Open Food Facts lookup error for barcode {barcode}: {e}")

        return {
            "barcode": barcode,
            "found": False,
            "brand": "Unknown",
            "name": "Unknown",
            "category": "Unknown",
        }

    @staticmethod
    def _extract_product_info(barcode: str, product: Dict) -> Dict[str, Any]:
        """Extract product information from Open Food Facts data"""
        # Enhanced brand extraction
        brand = "Unknown"
        brand_fields = ["brands", "brand", "brand_owner", "manufacturer"]

        for field in brand_fields:
            if field in product and product[field]:
                brand_value = str(product[field]).strip()
                if brand_value and brand_value.lower() not in [
                        "", "unknown", "n/a"]:
                    brand = (
                        brand_value.split(",")[0].strip()
                        if "," in brand_value
                        else brand_value
                    )
                    break

        # Try to extract brand from product name if still unknown
        if brand == "Unknown":
            name = product.get("product_name", "")
            if name:
                extracted = BrandNormalizer.extract_brand_from_product_text(
                    name)
                if extracted:
                    brand = extracted

        # Extract product name
        name = product.get("product_name", "")
        if not name:
            name = product.get(
                "product_name_en", product.get("product_name_fr", "Unknown")
            )

        # Extract category
        categories = product.get("categories", "")
        category = "Unknown"
        if categories:
            category_list = [c.strip()
                             for c in categories.split(",") if c.strip()]
            if category_list:
                category = category_list[-1]

        product_info = {
            "barcode": barcode,
            "name": name if name else "Unknown",
            "brand": brand,
            "category": category,
            "eco_score": product.get("ecoscore_grade", "Unknown"),
            "eco_score_value": product.get("ecoscore_score", None),
            "nutriscore": product.get("nutriscore_grade", "Unknown"),
            "nutriscore_value": product.get("nutriscore_score", None),
            "found": True,
            "ingredients": product.get("ingredients_text", ""),
            "allergens": product.get("allergens", ""),
            "image_url": product.get("image_url", ""),
            "countries": product.get("countries", ""),
            "energy_kcal": product.get("nutriments", {}).get("energy-kcal_100g", None),
            "fat": product.get("nutriments", {}).get("fat_100g", None),
            "carbohydrates": product.get("nutriments", {}).get(
                "carbohydrates_100g", None
            ),
            "proteins": product.get("nutriments", {}).get("proteins_100g", None),
            "salt": product.get("nutriments", {}).get("salt_100g", None),
            "last_updated": product.get("last_modified_t"),
        }

        return product_info


# ==================== BRAND EXTRACTION MANAGER ====================


class BrandExtractionManager:
    """Manager for brand extraction from product names"""

    @staticmethod
    async def extract_brand_from_product_name(
            product_name: str) -> Dict[str, Any]:
        """Main function to extract brand from product name using multiple strategies"""
        logger.info(
            f"Attempting to extract brand from product name: '{product_name}'")

        # Strategy 1: Direct brand name check
        result = BrandExtractionManager._check_direct_brand_match(product_name)
        if result["success"]:
            return result

        # Strategy 2: Try to extract brand directly from product name text
        direct_brand = BrandNormalizer.extract_brand_from_product_text(
            product_name)
        if direct_brand:
            logger.info(
                f"Direct extraction found brand: '{direct_brand}' from product name"
            )
            return BrandExtractionManager._format_result(
                success=True,
                message=f"Brand '{direct_brand}' extracted directly from input",
                extracted_brand=direct_brand,
                confidence=85,
                method="direct_extraction",
                reason=f"Found '{direct_brand}' directly in input text",
            )

        # Strategy 3: Search Open Food Facts for product-like names
        words = product_name.split()
        if len(words) > 1:
            return await BrandExtractionManager._search_open_food_facts(product_name)

        # Strategy 4: Single word input - likely a brand name
        return await BrandExtractionManager._handle_single_word_input(product_name)

    @staticmethod
    def _check_direct_brand_match(product_name: str) -> Dict[str, Any]:
        """Check if input is already a known brand (from Excel data only)"""
        brand_normalized = BrandNormalizer.normalize(product_name)

        # Check if the input is in the Excel data
        if certification_manager.data is not None:
            # Check exact match
            if brand_normalized in certification_manager.data:
                first_product = next(iter(certification_manager.data[brand_normalized].values()))
                original_brand = first_product.get("original_brand", brand_normalized.title())
                logger.info(
                    f"Input is a known brand in Excel: '{original_brand}'"
                )
                return BrandExtractionManager._format_result(
                    success=True,
                    message=f"Input recognized as brand: '{original_brand}'",
                    extracted_brand=original_brand,
                    confidence=90,
                    method="direct_brand_recognition",
                    reason=f"'{original_brand}' found in Excel certification database",
                )

            # Check for brand synonyms and aliases
            if brand_normalized in BrandNormalizer.BRAND_SYNONYMS:
                canonical_brand = BrandNormalizer.BRAND_SYNONYMS[brand_normalized]
                # Only use if canonical brand exists in Excel
                if canonical_brand in certification_manager.data:
                    logger.info(
                        f"Input matches brand synonym: '{brand_normalized}' → '{canonical_brand}'"
                    )
                    return BrandExtractionManager._format_result(
                        success=True,
                        message=f"Brand synonym recognized: '{canonical_brand}'",
                        extracted_brand=canonical_brand.title(),
                        confidence=85,
                        method="brand_synonym_match",
                        reason=f"'{brand_normalized}' is a synonym for '{canonical_brand}'",
                    )

            # Check for brand aliases
            for alias, canonical in BrandNormalizer.BRAND_ALIASES.items():
                if alias == brand_normalized and canonical in certification_manager.data:
                    logger.info(
                        f"Input matches brand alias: '{brand_normalized}' → '{canonical}'"
                    )
                    return BrandExtractionManager._format_result(
                        success=True,
                        message=f"Brand alias recognized: '{canonical}'",
                        extracted_brand=canonical.title(),
                        confidence=85,
                        method="brand_alias_match",
                        reason=f"'{brand_normalized}' is an alias for '{canonical}'",
                    )

            # Check if the input contains a known brand name - find the longest match
            longest_match = None
            longest_match_key = None
            for brand_key in certification_manager.data.keys():
                brand_key_normalized = brand_key  # Already normalized from Excel
                if brand_key_normalized and len(brand_key_normalized) > 2:
                    if brand_key_normalized in brand_normalized:
                        if longest_match is None or len(brand_key_normalized) > len(longest_match):
                            longest_match = brand_key_normalized
                            longest_match_key = brand_key

            if longest_match_key:
                first_product = next(iter(certification_manager.data[longest_match_key].values()))
                original_brand = first_product.get("original_brand", longest_match_key.title())
                logger.info(
                    f"Found brand '{original_brand}' in input: '{product_name}'")
                return BrandExtractionManager._format_result(
                    success=True,
                    message=f"Brand '{original_brand}' found in input",
                    extracted_brand=original_brand,
                    confidence=80,
                    method="brand_in_input",
                    reason=f"Brand '{original_brand}' found within input text",
                )

        return BrandExtractionManager._format_result(
            success=False,
            message="No direct brand match found",
            extracted_brand=None,
            confidence=0,
            method="direct_check",
        )

    @staticmethod
    async def _search_open_food_facts(product_name: str) -> Dict[str, Any]:
        """Search Open Food Facts for brand information"""
        search_result = await OpenFoodFactsClient.search_by_name(
            product_name, max_results=20
        )

        if not search_result["found"]:
            # Fallback to parent company mapping
            parent_company = BrandNormalizer.find_parent_company(product_name)
            if parent_company:
                logger.info(
                    f"Fallback to parent company: '{parent_company}' for product '{product_name}'"
                )
                return BrandExtractionManager._format_result(
                    success=True,
                    message=f"Using parent company '{parent_company}' for product",
                    extracted_brand=parent_company.title(),
                    confidence=75,
                    method="parent_company_mapping",
                    parent_company=parent_company,
                    warning="Using parent company mapping (not from Open Food Facts)",
                    reason=f"Product '{product_name}' belongs to parent company '{parent_company}'",
                )

            return BrandExtractionManager._format_result(
                success=False,
                message=search_result["message"],
                extracted_brand=None,
                confidence=0,
                method="search_failed",
                search_results=search_result,
            )

        # Get top brand from search results
        brand_analysis = search_result["brand_analysis"]
        top_brand = brand_analysis.get("top_brand")

        if not top_brand:
            # Try parent company as fallback
            parent_company = BrandNormalizer.find_parent_company(product_name)
            if parent_company:
                return BrandExtractionManager._format_result(
                    success=True,
                    message=f"Using parent company '{parent_company}' (no clear brand from search)",
                    extracted_brand=parent_company.title(),
                    confidence=70,
                    method="parent_company_fallback",
                    parent_company=parent_company,
                    warning="Using parent company as fallback",
                    reason=f"Product '{product_name}' belongs to parent company '{parent_company}'",
                )

            return BrandExtractionManager._format_result(
                success=False,
                message="No brand could be determined from search results",
                extracted_brand=None,
                confidence=0,
                method="search_failed",
                parent_company=None,
                search_results=search_result,
            )

        # Successfully extracted brand from search
        return BrandExtractionManager._process_search_result(
            product_name, top_brand, search_result
        )

    @staticmethod
    def _process_search_result(
        product_name: str, extracted_brand: str, search_result: Dict
    ) -> Dict[str, Any]:
        """Process search result to determine final brand"""
        parent_company = BrandNormalizer.find_parent_company(product_name)

        if parent_company:
            normalized_extracted = BrandNormalizer.normalize(extracted_brand)
            normalized_parent = BrandNormalizer.normalize(parent_company)

            # If parent company is different, consider using it
            if normalized_extracted != normalized_parent:
                # Check if parent company is a known national brand
                if normalized_parent in BrandNormalizer.NATIONAL_BRANDS:
                    logger.info(
                        f"Using parent company '{parent_company}' instead of extracted '{extracted_brand}'"
                    )
                    extracted_brand = parent_company.title()
                    confidence = 80
                    method = "parent_company_override"
                    reason = (
                        f"Parent company '{parent_company}' is a known national brand"
                    )
                else:
                    confidence = 70
                    method = "search_extraction"
                    reason = f"Brand '{extracted_brand}' extracted from search results"
            else:
                confidence = 75
                method = "search_extraction"
                reason = f"Brand '{extracted_brand}' extracted from search results"
        else:
            confidence = 70
            method = "search_extraction"
            reason = f"Brand '{extracted_brand}' extracted from search results"

        return BrandExtractionManager._format_result(
            success=True,
            message=f"Brand '{extracted_brand}' extracted from Open Food Facts",
            extracted_brand=extracted_brand,
            confidence=confidence,
            method=method,
            parent_company=parent_company,
            reason=reason,
            search_results={
                "total_products": search_result["brand_analysis"].get(
                    "total_products",
                    0),
                "total_brand_candidates": search_result["brand_analysis"].get(
                    "total_brand_candidates",
                    0),
            },
        )

    @staticmethod
    async def _handle_single_word_input(product_name: str) -> Dict[str, Any]:
        """Handle single word input - likely a brand name (from Excel data only)"""
        brand_normalized = BrandNormalizer.normalize(product_name)

        # Check for fuzzy matches with brands from Excel
        best_match = None
        best_score = 0.0

        if certification_manager.data is not None:
            for brand_key in certification_manager.data.keys():
                brand_key_normalized = brand_key  # Already normalized from Excel
                similarity = SequenceMatcher(
                    None, brand_normalized, brand_key_normalized
                ).ratio()

                if (
                    similarity > best_score and similarity >= 0.8
                ):  # 70% similarity threshold
                    best_score = similarity
                    best_match = brand_key

            if best_match:
                first_product = next(iter(certification_manager.data[best_match].values()))
                original_brand = first_product.get("original_brand", best_match.title())
                logger.info(
                    f"Fuzzy match found: '{brand_normalized}' → '{original_brand}' ({best_score:.1%} similarity)"
                )
                confidence = int(best_score * 100)
                return BrandExtractionManager._format_result(
                    success=True,
                    message=f"Fuzzy match found for '{product_name}'",
                    extracted_brand=original_brand,
                    confidence=confidence,
                    method="fuzzy_match",
                    warning=f"Using fuzzy match ({best_score:.1%} similarity)",
                    reason=f"'{product_name}' closely matches known brand '{original_brand}'",
                )

        # If we get here, we couldn't identify the brand
        return BrandExtractionManager._format_result(
            success=False,
            message=f"Could not identify brand from '{product_name}'",
            extracted_brand=None,
            confidence=0,
            method="failed",
            warning="Input could not be identified as a brand or product",
            reason="No matches found in Excel certification database or product search",
        )

    @staticmethod
    def _format_result(
        success: bool,
        message: str,
        extracted_brand: Optional[str],
        confidence: int,
        method: str,
        parent_company: Optional[str] = None,
        warning: Optional[str] = None,
        reason: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Format brand extraction result"""
        result = {
            "success": success,
            "message": message,
            "extracted_brand": extracted_brand,
            "original_extracted_brand": extracted_brand,
            "confidence": confidence,
            "method": method,
            "parent_company": parent_company,
            "alternative_brands": [],
            "warning": warning,
            "reason": reason,
        }

        # Add any additional keyword arguments
        result.update(kwargs)
        return result


# ==================== PASSWORD UTILITIES ====================


def hash_password(password: str) -> str:
    """Hash password using bcrypt"""
    bcrypt = get_bcrypt()  # Use your cached lazy import
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    bcrypt = get_bcrypt()  # Use your cached lazy import
    return bcrypt.checkpw(password.encode(), hashed_password.encode())


# ==================== GLOBAL STATE ====================

# Initialize managers
brand_normalizer = BrandNormalizer()
certification_manager = CertificationManager()
scoring_manager = ScoringManager()
food_facts_client = OpenFoodFactsClient()
brand_extraction_manager = BrandExtractionManager()

# Initialize in-memory caches
USERS_DB = {}
PURCHASE_HISTORY_DB = {}

# ==================== OPEN FOOD FACTS CACHE ====================

# Initialize Redis connection for persistent caching
redis_client = None
try:
    redis_url = os.getenv('REDIS_URL')
    if redis_url:
        redis_client = redis.Redis.from_url(redis_url)
        redis_client.ping()
        logger.info("✅ Redis connection established successfully")
    else:
        logger.info("ℹ️ REDIS_URL not set, using in-memory cache only")
except Exception as e:
    logger.warning(f"⚠️ Redis connection failed: {e}, using in-memory cache only")
    redis_client = None

OFF_CACHE = {}

async def get_cached_off_product(barcode: str) -> Dict[str, Any]:
    """Cache Open Food Facts lookups to avoid repeated API calls"""
    # Normalize barcode for consistent caching
    normalized_barcode = normalize_barcode(barcode)

    # Log what happened
    if normalized_barcode != barcode:
        logger.info(f"🔍 Normalized barcode: '{barcode}' → '{normalized_barcode}'")

    # Check Redis first (persistent cache)
    if redis_client:
        try:
            cached = redis_client.get(f'product:{normalized_barcode}')
            if cached:
                logger.info(f"🔄 Redis Cache HIT for barcode: {normalized_barcode}")
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Redis read error: {e}")

    # Check in-memory cache (fallback)
    if normalized_barcode in OFF_CACHE:
        logger.info(f"🔄 OFF Cache HIT for barcode: {normalized_barcode}")
        return OFF_CACHE[normalized_barcode]

    # Cache miss - perform the actual API lookup
    logger.info(f"🔄 OFF Cache MISS for barcode: {normalized_barcode} - calling API...")
    result = await OpenFoodFactsClient.lookup_barcode(normalized_barcode)

    # Store in Redis first (persistent)
    if redis_client:
        try:
            # Store with 30-day expiration (2592000 seconds)
            redis_client.setex(
                f'product:{normalized_barcode}',
                2592000,  # 30 days in seconds
                json.dumps(result)
            )
            logger.info(f"✅ Redis Cached result for barcode: {normalized_barcode}")
        except Exception as e:
            logger.warning(f"Redis write error: {e}")

    # Also store in in-memory cache (fast access)
    OFF_CACHE[normalized_barcode] = result
    logger.info(f"✅ OFF Cached result for barcode: {normalized_barcode}")

    return result

# ==================== SEARCH CACHE ====================
SEARCH_CACHE = {}


def get_cached_certifications(
    brand: str,
    category: str = None,
    source: str = "manual",
    barcode: str = None,
) -> Dict[str, Any]:
    """Cache certification lookups to avoid repeated expensive operations.

    Cache key granularity:
      - barcode provided  → "<brand>_<category>_<source>_<barcode>"
        Distinct barcodes get distinct cache entries so two products
        from the same brand and category cannot collide.
      - barcode absent    → "<brand>_<category>_<source>" (or "<brand>_<source>")
        Brand-level lookup; shared across all products of that brand.

    The barcode is normalized before being included in the key so that
    the same physical product scanned as UPC-A vs. GTIN-14 does not
    produce two entries.
    """
    # Normalize barcode if present so different physical encodings of
    # the same product collapse to one cache entry.
    normalized_barcode = normalize_barcode(barcode) if barcode else None

    # Build cache key with correct granularity.
    if normalized_barcode:
        cache_key = f"{brand}_{category or ''}_{source}_{normalized_barcode}"
    elif category:
        cache_key = f"{brand}_{category}_{source}"
    else:
        cache_key = f"{brand}_{source}"

    # Check if we have a cached result
    if cache_key in SEARCH_CACHE:
        logger.info(f"🔄 Cache hit for '{cache_key}'")
        return SEARCH_CACHE[cache_key]

    # Cache miss - perform the actual lookup
    logger.info(f"🔄 Cache miss for '{cache_key}' - performing lookup...")
    result = certification_manager.get_certifications(brand, category, source)

    # Store in cache (limit cache size to prevent memory issues)
    if len(SEARCH_CACHE) > 1000:
        # Remove oldest entry (first item)
        oldest_key = next(iter(SEARCH_CACHE))
        del SEARCH_CACHE[oldest_key]
        logger.info(f"🧹 Cache size limit reached, removed oldest entry: '{oldest_key}'")

    SEARCH_CACHE[cache_key] = result
    logger.info(f"✅ Cached result for '{cache_key}' (cache size: {len(SEARCH_CACHE)})")

    return result

# ==================== BRAND EXTRACTION CACHE ====================
BRAND_EXTRACTION_CACHE = {}

async def get_cached_brand_extraction(product_name: str) -> Dict[str, Any]:
    """Cache brand extraction results to avoid repeated expensive API calls"""
    # Check if we have a cached result
    if product_name in BRAND_EXTRACTION_CACHE:
        logger.info(f"🔄 Brand extraction cache hit for: '{product_name}'")
        return BRAND_EXTRACTION_CACHE[product_name]

    # Cache miss - perform the actual extraction
    logger.info(f"🔄 Brand extraction cache miss for: '{product_name}' - calling API...")
    result = await brand_extraction_manager.extract_brand_from_product_name(product_name)

    # Store in cache
    BRAND_EXTRACTION_CACHE[product_name] = result
    logger.info(f"✅ Brand extraction cached for: '{product_name}' (cache size: {len(BRAND_EXTRACTION_CACHE)})")

    return result

# Simple user database class (optional)
class UserDatabase:
    def __init__(self):
        self.users = {}
        self.history = {}

# ==================== PERSISTENT STORAGE SETUP ====================


# JSON file for persistent storage
USER_DATA_FILE = "user_data.json"


def load_user_data():
    """Load user data from JSON file"""
    try:
        if os.path.exists(USER_DATA_FILE):
            with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"Loaded user data from {USER_DATA_FILE}")
                return data
    except Exception as e:
        logger.error(f"Error loading user data: {e}")

    # Return empty structure if file doesn't exist or has errors
    return {"users": {}, "purchases": {}}


def save_user_data():
    """Save current user data to JSON file"""
    try:
        data = {
            "users": USERS_DB,
            "purchases": PURCHASE_HISTORY_DB
        }
        with open(USER_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"Saved user data to {USER_DATA_FILE}")
    except Exception as e:
        logger.error(f"Error saving user data: {e}")


# Load existing data or initialize
user_data = load_user_data()
USERS_DB.update(user_data.get("users", {}))
PURCHASE_HISTORY_DB.update(user_data.get("purchases", {}))

# Initialize ALB user if not exists
if "ALB" not in USERS_DB:
    USERS_DB["ALB"] = {
        "username": "ALB",
        "email": "test@example.com",
        "password_hash": hash_password("Oranges#155"),
        "created_at": datetime.utcnow().isoformat(),
    }
    PURCHASE_HISTORY_DB["ALB"] = []
    save_user_data()  # Save the new user
    logger.info("Created ALB user")

# ==================== SCRIPT EXECUTION FUNCTIONS ====================


def run_create_excel_script() -> Dict[str, Any]:
    """Execute the create_excel.py script"""
    try:
        # Check if script exists
        if not os.path.exists(FileConfig.CREATE_EXCEL_SCRIPT):
            return {
                "success": False,
                "message": f"Script file not found: {FileConfig.CREATE_EXCEL_SCRIPT}",
                "output": "",
            }

        # Import and run the script directly
        spec = importlib.util.spec_from_file_location(
            "create_excel", FileConfig.CREATE_EXCEL_SCRIPT
        )
        create_excel_module = importlib.util.module_from_spec(spec)

        output_capture = io.StringIO()

        with redirect_stdout(output_capture), redirect_stderr(output_capture):
            spec.loader.exec_module(create_excel_module)
            # Run the main function if it exists
            if hasattr(create_excel_module, "create_sample_excel_file"):
                create_excel_module.create_sample_excel_file()

        output = output_capture.getvalue()

        # Reload certification data
        certification_manager.load_certification_data()

        return {
            "success": True,
            "message": f"Successfully executed {FileConfig.CREATE_EXCEL_SCRIPT}",
            "output": output,
            "excel_file_created": os.path.exists(
                FileConfig.CERTIFICATION_EXCEL_FILE),
            "excel_file_size": (
                os.path.getsize(
                    FileConfig.CERTIFICATION_EXCEL_FILE) if os.path.exists(
                    FileConfig.CERTIFICATION_EXCEL_FILE) else 0),
        }
    except Exception as e:
        logger.error(f"Error executing {FileConfig.CREATE_EXCEL_SCRIPT}: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Error executing script: {str(e)}",
            "output": str(e),
        }


def verify_excel_script() -> Dict[str, Any]:
    """Verify the create_excel.py script and Excel file"""
    try:
        # Check if script exists
        script_exists = os.path.exists(FileConfig.CREATE_EXCEL_SCRIPT)
        script_size = (
            os.path.getsize(
                FileConfig.CREATE_EXCEL_SCRIPT) if script_exists else 0)

        # Check if Excel file exists
        excel_exists = os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE)
        excel_size = (
            os.path.getsize(
                FileConfig.CERTIFICATION_EXCEL_FILE) if excel_exists else 0)

        # Try to read Excel file
        excel_data = None
        if excel_exists:
            try:
                # LAZY load pandas before using it
                pd = get_pandas()
                df = pd.read_excel(FileConfig.CERTIFICATION_EXCEL_FILE)
                excel_data = {
                    "rows": len(df),
                    "columns": len(df.columns),
                    "columns_list": list(df.columns),
                    "first_few_rows": df.head(5).to_dict("records"),
                }
            except Exception as e:
                excel_data = {"error": str(e)}
        return {
            "script": {
                "exists": script_exists,
                "size_bytes": script_size,
                "path": (
                    os.path.abspath(FileConfig.CREATE_EXCEL_SCRIPT)
                    if script_exists
                    else None
                ),
            },
            "excel_file": {
                "exists": excel_exists,
                "size_bytes": excel_size,
                "path": (
                    os.path.abspath(FileConfig.CERTIFICATION_EXCEL_FILE)
                    if excel_exists
                    else None
                ),
                "data": excel_data,
            },
            "certification_data_loaded": certification_manager.data is not None,
            "certification_records": (
                len(certification_manager.data) if certification_manager.data else 0
            ),
        }
    except Exception as e:
        logger.error(f"Error verifying script: {e}")
        return {
            "error": str(e),
            "script": {"exists": False},
            "excel_file": {"exists": False},
        }


def get_data():
    """Get certification data from the manager"""
    if certification_manager.data is None:
        certification_manager.load_certification_data()
    # You might need to return a pandas DataFrame or the raw data
    # For now, returning the certification manager's data
    return certification_manager.data


# ==================== TEMPLATE FUNCTIONS ====================


def render_scoring_methodology() -> str:
    """Render scoring methodology HTML"""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>TBL Grocery Scanner - Scoring Methodology</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
                min-height: 100vh;
                margin: 0;
                padding: 20px;
                color: #333;
            }}
            .container {{
                max-width: 900px;
                margin: 0 auto;
                background: white;
                border-radius: 20px;
                box-shadow: 0 20px 40px rgba(0,0,0,0.15);
                padding: 40px;
                margin-top: 20px;
                margin-bottom: 40px;
            }}
            h1 {{
                color: #2e7d32;
                text-align: center;
                margin-bottom: 10px;
            }}
            .subtitle {{
                text-align: center;
                color: #666;
                margin-bottom: 30px;
                font-size: 18px;
            }}
            .section {{
                margin-bottom: 40px;
                padding: 25px;
                border-radius: 15px;
                background: #f8f9fa;
                border-left: 5px solid #2e7d32;
            }}
            .section h2 {{
                color: #2e7d32;
                margin-top: 0;
                border-bottom: 2px solid #e9ecef;
                padding-bottom: 10px;
            }}
            .principle-box {{
                background: white;
                padding: 20px;
                border-radius: 10px;
                margin: 15px 0;
                border: 1px solid #e9ecef;
                box-shadow: 0 4px 6px rgba(0,0,0,0.05);
            }}
            .certification-box {{
                background: white;
                padding: 20px;
                border-radius: 10px;
                margin: 15px 0;
                border-left: 4px solid #ff9800;
            }}
            .score-breakdown {{
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 15px;
                margin: 20px 0;
            }}
            .score-pillar {{
                background: #e8f5e9;
                padding: 20px;
                border-radius: 10px;
                text-align: center;
            }}
            .score-value {{
                font-size: 32px;
                font-weight: bold;
                color: #2e7d32;
                margin: 10px 0;
            }}
            .back-button {{
                display: inline-block;
                padding: 12px 24px;
                background: linear-gradient(135deg, #2e7d32 0%, #1b5e20 100%);
                color: white;
                text-decoration: none;
                border-radius: 8px;
                margin: 10px 5px;
                font-weight: 600;
                transition: all 0.3s ease;
                border: none;
                cursor: pointer;
                font-size: 16px;
            }}
            .back-button:hover {{
                transform: translateY(-2px);
                box-shadow: 0 10px 20px rgba(46, 125, 50, 0.3);
            }}
            .example {{
                background: #fff3e0;
                padding: 20px;
                border-radius: 10px;
                margin: 20px 0;
                border: 1px solid #ffcc80;
            }}
            .badge {{
                display: inline-block;
                background: #2e7d32;
                color: white;
                padding: 5px 12px;
                border-radius: 20px;
                font-size: 14px;
                font-weight: bold;
                margin-right: 10px;
                margin-bottom: 10px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin: 20px 0;
            }}
            th, td {{
                padding: 12px;
                text-align: left;
                border-bottom: 1px solid #e9ecef;
            }}
            th {{
                background: #e8f5e9;
                color: #2e7d32;
                font-weight: bold;
            }}
            tr:hover {{
                background: #f8f9fa;
            }}
            .grade-box {{
                display: inline-block;
                padding: 8px 16px;
                border-radius: 8px;
                font-weight: bold;
                margin: 5px;
            }}
            .excellent {{ background: #d4edda; color: #155724; }}
            .great {{ background: #d1ecf1; color: #0c5460; }}
            .good {{ background: #fff3cd; color: #856404; }}
            .poor {{ background: #f8d7da; color: #721c24; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📊 TBL Grocery Scanner Scoring Methodology</h1>
            <div class="subtitle">Version 2.4.0 • Weighted Certification Bonuses with Cap at 10.0</div>

            <div class="section">
                <h2>🎯 Core Principles</h2>
                <div class="principle-box">
                    <h3>Consistency First</h3>
                    <p>Every brand gets the exact same score regardless of search method (barcode, brand name, or product name).</p>
                </div>
                <div class="principle-box">
                    <h3>Single Scoring Function</h3>
                    <p>One function calculates all scores - no duplication or inconsistency.</p>
                </div>
                <div class="principle-box">
                    <h3>Multi-Certification Bonus Always Applied</h3>
                    <p>Brands with multiple certifications always get the appropriate bonus.</p>
                </div>
            </div>

            <div class="section">
                <h2>📈 How Scores Are Calculated</h2>

                <div class="score-breakdown">
                    <div class="score-pillar">
                        <div>👥 Social Score</div>
                        <div class="score-value">{ScoringConfig.BASE_SCORE} +</div>
                        <div>Base + Certification Bonuses</div>
                    </div>
                    <div class="score-pillar">
                        <div>🌱 Environmental Score</div>
                        <div class="score-value">{ScoringConfig.BASE_SCORE} +</div>
                        <div>Base + Certification Bonuses</div>
                    </div>
                    <div class="score-pillar">
                        <div>💰 Economic Score</div>
                        <div class="score-value">{ScoringConfig.BASE_SCORE} +</div>
                        <div>Base + Certification Bonuses</div>
                    </div>
                </div>

                <h3>Base Score: {ScoringConfig.BASE_SCORE} in Each Pillar</h3>
                <p>Every brand starts with {ScoringConfig.BASE_SCORE} in Social, Environmental, and Economic pillars. This represents "average" performance - meeting basic legal requirements.</p>

                <h3>Certification Bonuses</h3>
                <p>Points are added ONLY for verified third-party certifications:</p>

                <table>
                    <tr>
                        <th>Certification</th>
                        <th>👥 Social Bonus</th>
                        <th>🌱 Environmental Bonus</th>
                        <th>💰 Economic Bonus</th>
                        <th>Focus Area</th>
                    </tr>
                    <tr>
                    <tr>
                        <td><strong>B Corp</strong></td>
                        <td><strong>+3.5</strong></td>
                        <td><strong>+3.5</strong></td>
                        <td><strong>+3.5</strong></td>
                        <td>Holistic (5 areas: Governance, Workers, Community, Environment, Customers)</td>
                    </tr>
                    <tr>
                        <td><strong>Fair Trade</strong></td>
                        <td><strong>+2.0</strong></td>
                        <td><strong>+1.0</strong></td>
                        <td><strong>+2.0</strong></td>
                        <td>Multi-dimensional (Labor rights, Fair pricing, Some environmental)</td>
                    </tr>
                    <tr>
                        <td><strong>Rainforest Alliance</strong></td>
                        <td><strong>+1.0</strong></td>
                        <td><strong>+2.5</strong></td>
                        <td><strong>+1.0</strong></td>
                        <td>Environmental focus (Deforestation, Biodiversity, Agrochemicals)</td>
                    </tr>
                    <tr>
                        <td><strong>Leaping Bunny</strong></td>
                        <td><strong>+1.0</strong></td>
                        <td><strong>0.0</strong></td>
                        <td><strong>0.0</strong></td>
                        <td>Single-issue (No animal testing only)</td>
                    </tr>
                </table>

                <h3>Multi-Certification Bonus</h3>
                <p>Brands with multiple certifications get an additional +{ScoringConfig.MULTI_CERT_BONUS} to each pillar for each certification beyond the first.</p>
                <p><strong>Example:</strong> A brand with 3 certifications gets +{(3-1) * ScoringConfig.MULTI_CERT_BONUS} to each pillar for the multi-cert bonus.</p>
            </div>

            <div class="section">
                <h2>⭐ Grade Thresholds</h2>
                <p>Overall TBL Score = (Social + Environmental + Economic) ÷ 3</p>

                <div style="margin: 20px 0;">
                    <div class="grade-box excellent">EXCELLENT: {ScoringConfig.GRADE_THRESHOLDS['EXCELLENT']}+</div>
                    <p>Multiple verified certifications covering different aspects of sustainability</p>

                    <div class="grade-box great">GREAT: {ScoringConfig.GRADE_THRESHOLDS['GREAT']}-{ScoringConfig.GRADE_THRESHOLDS['EXCELLENT'] - 0.1}</div>
                    <p>Strong certifications in one or two key areas</p>

                    <div class="grade-box good">GOOD: {ScoringConfig.GRADE_THRESHOLDS['GOOD']}-{ScoringConfig.GRADE_THRESHOLDS['GREAT'] - 0.1}</div>
                    <p>Meets basic requirements but lacks significant third-party verification</p>

                    <div class="grade-box poor">POOR: Below {ScoringConfig.GRADE_THRESHOLDS['GOOD']}</div>
                    <p>May have issues or lacks transparency</p>
                </div>
            </div>

            <div class="example">
                <h2>🧪 Example Calculation: Nespresso</h2>
                                <p><strong>Certifications:</strong> B Corp + Fair Trade + Rainforest Alliance (All stack with cap at 10.0)</p>

                <table>
                    <tr>
                        <th>Step</th>
                        <th>👥 Social</th>
                        <th>🌱 Environmental</th>
                        <th>💰 Economic</th>
                    </tr>
                    <tr>
                        <td>Base Score</td>
                        <td>{ScoringConfig.BASE_SCORE}</td>
                        <td>{ScoringConfig.BASE_SCORE}</td>
                        <td>{ScoringConfig.BASE_SCORE}</td>
                    </tr>
                    <tr>
                        <td>+ B Corp Certification</td>
                        <td>+3.5</td>
                        <td>+3.5</td>
                        <td>+3.5</td>
                    </tr>
                    <tr>
                        <td>+ Fair Trade Certification</td>
                        <td>+2.0</td>
                        <td>+1.0</td>
                        <td>+2.0</td>
                    </tr>
                    <tr>
                        <td>+ Rainforest Alliance Certification</td>
                        <td>+1.0</td>
                        <td>+2.5</td>
                        <td>+1.0</td>
                    </tr>
                    <tr>
                        <td>+ Multi-Cert Bonus (2 additional certs × {ScoringConfig.MULTI_CERT_BONUS})</td>
                        <td>+1.0</td>
                        <td>+1.0</td>
                        <td>+1.0</td>
                    </tr>
                    <tr style="font-weight: bold; background: #e8f5e9;">
                        <td>Final Scores (capped at 10.0)</td>
                        <td><strong>10.0</strong></td>
                        <td><strong>10.0</strong></td>
                        <td><strong>10.0</strong></td>
                    </tr>
                </table>

                <p><strong>Overall TBL Score:</strong> (8.5 + 8.5 + 8.0) ÷ 3 = <strong>8.3</strong></p>
                <p><strong>Grade:</strong> <span class="grade-box great">GREAT</span></p>
            </div>

            <div class="section">
                <h2>🔍 Consistent Scoring Across All Search Methods</h2>
                <div class="principle-box">
                    <h3>Single Source of Truth</h3>
                    <p>One function (<code>calculate_brand_scores()</code>) handles all scoring</p>
                    <p>Uses certifications from Excel database only</p>
                    <p>Always applies multi-certification bonus correctly</p>
                </div>

                <div class="principle-box">
                    <h3>Brand Mapping vs Scoring</h3>
                    <p><strong>Brand Identification:</strong> Uses parent company mapping to find the right brand</p>
                    <p><strong>Scoring:</strong> Once brand is identified, uses the same scoring function regardless of search method</p>
                    <p><strong>Result:</strong> Dannon products always get the same score as searching "Dannon" directly</p>
                </div>
            </div>

            <div style="text-align: center; margin-top: 40px;">
                <a href="/" class="back-button">🏠 Back to Scanner</a>
                <a href="/health" class="back-button">❤️ Health Check</a>
                <button onclick="window.history.back()" class="back-button" style="background: linear-gradient(135deg, #6c757d 0%, #495057 100%);">⬅️ Go Back</button>
            </div>
        </div>

        <script>
            document.addEventListener('DOMContentLoaded', function() {{
                console.log('Methodology page loaded');
            }});
        </script>
    </body>
    </html>
    """


def render_score_breakdown(
    brand: str, scores: BrandData, tbl: Dict[str, Any], excel_result: Dict[str, Any]
) -> str:
    """Render score breakdown HTML"""
    brand_normalized = BrandNormalizer.normalize(brand)

    # Calculate how the score was derived
    base_score = ScoringConfig.BASE_SCORE
    total_social_bonus = scores.social - base_score
    total_env_bonus = scores.environmental - base_score
    total_econ_bonus = scores.economic - base_score

    # Get certifications from Excel only (hardcoded DB removed)
    excel_cert_list = []
    if excel_result["certifications"]["b_corp"]:
        excel_cert_list.append("B Corp")
    if excel_result["certifications"]["fair_trade"]:
        excel_cert_list.append("Fair Trade")
    if excel_result["certifications"]["rainforest_alliance"]:
        excel_cert_list.append("Rainforest Alliance")
    if excel_result["certifications"]["leaping_bunny"]:
        excel_cert_list.append("Leaping Bunny")

    # Use Excel certifications only
    all_certs = excel_cert_list

    # Generate certification badges HTML
    cert_badges = (
        "".join([f'<span class="cert-badge">{cert}</span>' for cert in all_certs])
        if all_certs
        else '<p style="color: #666; font-style: italic;">No verified certifications found</p>'
    )

    # Generate certification bonus rows HTML
    cert_rows = "".join(
        [
            f"""
    <div class="bonus-row">
        <span>+ {cert} Certification</span>
        <span>+{ScoringConfig.CERTIFICATION_BONUSES[cert]['social']:.1f} social,
              +{ScoringConfig.CERTIFICATION_BONUSES[cert]['environmental']:.1f} environmental,
              +{ScoringConfig.CERTIFICATION_BONUSES[cert]['economic']:.1f} economic</span>
    </div>
    """
            for cert in all_certs
            if cert in ScoringConfig.CERTIFICATION_BONUSES
        ]
    )

    # Generate multi-cert bonus row HTML
    multi_cert_row = (
        f"""
    <div class="bonus-row">
        <span>+ Multi-Certification Bonus ({len(all_certs)-1} additional cert{'s' if len(all_certs)-1 != 1 else ''} x {ScoringConfig.MULTI_CERT_BONUS})</span>
        <span>+{(len(all_certs)-1) * ScoringConfig.MULTI_CERT_BONUS:.1f} to each pillar</span>
    </div>
    """
        if len(all_certs) > 1
        else '<p style="color: #666; font-style: italic;">No multi-certification bonus (only one or no certifications)</p>'
    )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Score Breakdown: {brand}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
                min-height: 100vh;
                margin: 0;
                padding: 20px;
                color: #333;
            }}
            .container {{
                max-width: 800px;
                margin: 0 auto;
                background: white;
                border-radius: 20px;
                box-shadow: 0 20px 40px rgba(0,0,0,0.15);
                padding: 40px;
                margin-top: 20px;
            }}
            h1 {{
                color: #2e7d32;
                text-align: center;
                margin-bottom: 10px;
            }}
            .brand-header {{
                text-align: center;
                background: #e8f5e9;
                padding: 20px;
                border-radius: 15px;
                margin-bottom: 30px;
            }}
            .score-display {{
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 20px;
                margin: 30px 0;
            }}
            .pillar {{
                background: #f8f9fa;
                padding: 25px;
                border-radius: 15px;
                text-align: center;
                border: 2px solid #e9ecef;
            }}
            .pillar-score {{
                font-size: 42px;
                font-weight: bold;
                color: #2e7d32;
                margin: 10px 0;
            }}
            .overall-score {{
                text-align: center;
                background: linear-gradient(135deg, #2e7d32 0%, #1b5e20 100%);
                color: white;
                padding: 30px;
                border-radius: 15px;
                margin: 30px 0;
            }}
            .overall-value {{
                font-size: 64px;
                font-weight: bold;
                margin: 10px 0;
            }}
            .grade {{
                display: inline-block;
                background: white;
                color: #2e7d32;
                padding: 10px 25px;
                border-radius: 25px;
                font-size: 24px;
                font-weight: bold;
                margin-top: 10px;
            }}
            .breakdown {{
                background: #fff3e0;
                padding: 25px;
                border-radius: 15px;
                margin: 30px 0;
                border: 2px solid #ffcc80;
            }}
            .cert-badge {{
                display: inline-block;
                background: #2e7d32;
                color: white;
                padding: 8px 16px;
                border-radius: 20px;
                margin: 5px;
                font-weight: bold;
            }}
            .bonus-row {{
                display: flex;
                justify-content: space-between;
                padding: 12px 0;
                border-bottom: 1px solid #dee2e6;
            }}
            .bonus-row:last-child {{
                border-bottom: none;
            }}
            .total-row {{
                font-weight: bold;
                background: #e8f5e9;
                padding: 15px;
                border-radius: 10px;
                margin-top: 15px;
            }}
            .back-button {{
                display: inline-block;
                padding: 14px 28px;
                background: linear-gradient(135deg, #2e7d32 0%, #1b5e20 100%);
                color: white;
                text-decoration: none;
                border-radius: 12px;
                margin: 10px 5px;
                font-weight: 600;
                transition: all 0.3s ease;
                border: none;
                cursor: pointer;
                font-size: 16px;
            }}
            .back-button:hover {{
                transform: translateY(-2px);
                box-shadow: 0 10px 20px rgba(46, 125, 50, 0.3);
            }}
            .excel-status {{
                padding: 15px;
                border-radius: 10px;
                margin: 20px 0;
                text-align: center;
                font-weight: bold;
            }}
            .excel-found {{
                background: #d4edda;
                color: #155724;
                border: 2px solid #c3e6cb;
            }}
            .excel-notfound {{
                background: #f8d7da;
                color: #721c24;
                border: 2px solid #f5c6cb;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📊 Score Breakdown</h1>
            <div class="brand-header">
                <h2 style="margin-top: 0;">{brand}</h2>
                <p>Normalized as: {brand_normalized}</p>
                {f'<p style="color: #666; font-size: 14px;">Category: {excel_result["matched_category"]}</p>' if excel_result.get("matched_category") else ''}
            </div>

        <div class="excel-status {'excel-found' if excel_result['found'] else 'excel-notfound'}">
            {'✓ Found in Excel Database' + (' (Category: ' + excel_result['matched_category'] + ')' if excel_result.get('matched_category') else '') if excel_result['found'] else '❌ Not in Excel Database'}
        </div>

            <div class="score-display">
                <div class="pillar">
                    <div>👥 Social Impact</div>
                    <div class="pillar-score">{scores.social:.1f}</div>
                    <div>Base {ScoringConfig.BASE_SCORE} + {total_social_bonus:.1f} bonus</div>
                </div>
                <div class="pillar">
                    <div>🌱 Environmental Impact</div>
                    <div class="pillar-score">{scores.environmental:.1f}</div>
                    <div>Base {ScoringConfig.BASE_SCORE} + {total_env_bonus:.1f} bonus</div>
                </div>
                <div class="pillar">
                    <div>💰 Economic Impact</div>
                    <div class="pillar-score">{scores.economic:.1f}</div>
                    <div>Base {ScoringConfig.BASE_SCORE} + {total_econ_bonus:.1f} bonus</div>
                </div>
            </div>

            <div class="overall-score">
                <div>Overall TBL Score</div>
                <div class="overall-value">{tbl["overall_score"]:.1f}</div>
                <div class="grade">{tbl["grade"]}</div>
            </div>

            <div class="breakdown">
                <h3 style="color: #e65100; margin-top: 0;">🔍 How This Score Was Calculated</h3>

                <h4>Base Scores (All Brands Start Here)</h4>
                <div class="bonus-row">
                    <span>Social Base Score</span>
                    <span>{ScoringConfig.BASE_SCORE}</span>
                </div>
                <div class="bonus-row">
                    <span>Environmental Base Score</span>
                    <span>{ScoringConfig.BASE_SCORE}</span>
                </div>
                <div class="bonus-row">
                    <span>Economic Base Score</span>
                    <span>{ScoringConfig.BASE_SCORE}</span>
                </div>

                <h4 style="margin-top: 25px;">Certification Bonuses</h4>
                {cert_rows}
                {multi_cert_row}

                <div class="total-row">
                    <span>Total Bonuses Added</span>
                    <span>Social: +{total_social_bonus:.1f}, Environmental: +{total_env_bonus:.1f}, Economic: +{total_econ_bonus:.1f}</span>
                </div>
            </div>

            <div style="margin: 30px 0;">
                <h3>✅ Verified Certifications</h3>
                {cert_badges}
                <p style="font-size: 12px; color: #666; margin-top: 10px;">
                    From Excel certification database only
                </p>
            </div>

            <div style="text-align: center; margin-top: 40px;">
                <a href="/" class="back-button">🏠 Back to Scanner</a>
                <a href="/scoring-methodology" class="back-button" style="background: linear-gradient(135deg, #ff9800 0%, #e65100 100%);">📚 Full Methodology</a>
                <button onclick="window.history.back()" class="back-button" style="background: linear-gradient(135deg, #6c757d 0%, #495057 100%);">⬅️ Go Back</button>
            </div>
        </div>
    </body>
    </html>
    """

# ==================== API ENDPOINTS ====================


@app.get("/scoring-methodology")
async def get_scoring_methodology():
    """Explain the scoring methodology transparently to users"""
    return HTMLResponse(content=render_scoring_methodology())

# ✅ ADD THIS NEW ROUTE RIGHT HERE:


@app.get("/data-sources", response_class=HTMLResponse)
async def get_data_sources():
    """Serve the data sources information page."""
    with open("data-sources.html", "r", encoding="utf-8") as file:
        html_content = file.read()
    return HTMLResponse(content=html_content)


@app.get("/test/scoring/{brand}")
async def test_scoring_methodology(brand: str, category: str = None):
    """Test the scoring methodology for a specific brand - returns HTML"""
    # Try to get certifications with provided category
    excel_result = certification_manager.get_certifications(brand, category)

    # If category is required, find the best one
    if not excel_result.get("found") and excel_result.get("match_type") == "category_required":
        brand_normalized = BrandNormalizer.normalize(brand)
        categories = certification_manager.brand_categories.get(brand_normalized, [])

        if categories:
            # Prefer categories with certifications
            best_category = None
            for cat in categories:
                test_result = certification_manager.get_certifications(brand, cat)
                if test_result.get("found") and any(test_result.get("certifications", {}).values()):
                    best_category = cat
                    break

            # If no certified category found, use the first one
            if not best_category:
                best_category = categories[0]

            logger.info(f"Auto-selected category '{best_category}' for brand '{brand}'")
            excel_result = certification_manager.get_certifications(brand, best_category)

    # Calculate scores with the category that was used (if any)
    used_category = excel_result.get("matched_category") if excel_result.get("found") else category
    scores = scoring_manager.calculate_brand_scores(brand, used_category, excel_result)
    tbl = calculate_overall_score(
        scores.social,
        scores.environmental,
        scores.economic)

    return HTMLResponse(
        content=render_score_breakdown(brand, scores, tbl, excel_result)
    )


@app.post("/auth/register")
async def register_user(user: UserRegistration) -> Dict[str, Any]:
    """Register new user"""
    if user.username in USERS_DB:
        raise HTTPException(status_code=400, detail="Username already exists")

    USERS_DB[user.username] = {
        "username": user.username,
        "email": user.email,
        "password_hash": hash_password(user.password),
        "created_at": datetime.utcnow().isoformat(),
    }
    PURCHASE_HISTORY_DB[user.username] = []

    # ✅ ADD THIS LINE: Save to persistent storage
    save_user_data()

    logger.info(f"New user registered: {user.username}")
    return {
        "message": "User registered successfully",
        "username": user.username}


@app.post("/auth/login")
async def login_user(login_data: LoginRequest) -> Dict[str, Any]:
    """Login user"""
    user = USERS_DB.get(login_data.username)
    if not user or not verify_password(
            login_data.password,
            user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    logger.info(f"User logged in: {login_data.username}")
    return {
        "message": "Login successful",
        "username": login_data.username,
        "token": "token_" + login_data.username,
    }


@app.post("/scan")
async def scan_product(product: Product) -> Dict[str, Any]:
    """Scan product and return TBL scores with verified certifications"""
    try:
        logger.info(
            f"Scan request: barcode={product.barcode}, brand={product.brand}, name={product.product_name}"
        )

        # Initialize with default values
        brand_extraction_info = {
            "extracted_from_name": False,
            "reason": "Brand provided or insufficient product name",
        }

        product_name = product.product_name or "Unknown Product"
        brand = product.brand or "Unknown"
        barcode = product.barcode or ""
        category = product.category or ""

        # If barcode provided, try to get product info from Open Food Facts (with caching)
        if barcode and barcode.strip() != "":
            try:
                product_info = await get_cached_off_product(barcode)
                if product_info.get("found"):
                    # Use data from Open Food Facts
                    brand = product_info.get("brand", brand)
                    product_name = product_info.get("name", product_name)
                    category = product_info.get("category", category)
            except Exception as e:
                logger.error(f"Barcode lookup error: {e}")
                # Continue with original values

        # If brand is empty/Unknown but product_name is provided, try to
        # extract brand
        if (not brand or brand == "Unknown") and product_name and product_name != "Generic Product":
            logger.info(
                f"Attempting to extract brand from product name: {product_name}")
            try:
                brand_extraction = await get_cached_brand_extraction(product_name)

                if brand_extraction["success"]:
                    extracted_brand = brand_extraction["extracted_brand"]
                    logger.info(
                        f"Successfully extracted brand '{extracted_brand}' from product name '{product_name}'")

                    brand = extracted_brand

                    # Update extraction info
                    brand_extraction_info = {
                        "extracted_from_name": True,
                        "confidence": brand_extraction.get("confidence", 0.5),
                        "method": brand_extraction.get("method", "unknown"),
                        "parent_company": brand_extraction.get("parent_company"),
                        "warning": brand_extraction.get("warning"),
                        "alternative_brands": brand_extraction.get("alternative_brands", []),
                        "search_results": brand_extraction.get("search_results", {}),
                    }
                else:
                    logger.warning(
                        f"Failed to extract brand from product name: {brand_extraction.get('message', 'Unknown error')}")
                    # Fallback: use product name as brand
                    brand = product_name
                    brand_extraction_info = {
                        "extracted_from_name": False, "error": brand_extraction.get(
                            "message", "Brand extraction failed"), }
            except Exception as e:
                logger.error(f"Brand extraction error: {e}")
                brand = product_name if product_name != "Generic Product" else "Unknown"

        # ===== STEP 1: Get certifications FIRST (to get the correct category) =====
        try:
            # Determine source based on whether barcode was used
            source = "barcode" if (barcode and barcode.strip() != "") else "manual"
            cert_result = get_cached_certifications(brand, category, source=source, barcode=barcode)

            # ===== DEBUG: Log the certification result =====
            logger.info(f"🔍 CERT_RESULT from get_certifications: {cert_result}")
            logger.info(f"🔍 cert_result.get('certifications'): {cert_result.get('certifications')}")
            logger.info(f"🔍 b_corp value: {cert_result.get('certifications', {}).get('b_corp')}")
            logger.info(f"🔍 research_complete value: {cert_result.get('certifications', {}).get('research_complete')}")
            # ===== END DEBUG =====

        except Exception as e:
            logger.error(f"Certification lookup error: {e}")
            cert_result = {
                "found": False,
                "details": {},
                "search_brand_used": brand
            }

        # ===== STEP 2: Update category if a matched category was returned =====
        if cert_result.get("matched_category"):
            category = cert_result.get("matched_category")
            logger.info(f"Updated category to matched category: '{category}'")

        # ===== STEP 3: Calculate scores with the CORRECT category =====
        try:
            category_for_scores = category or None
            scores = scoring_manager.calculate_brand_scores(
                brand,
                category_for_scores,
                cert_result,
            )
            logger.info(f"🔍 Scores calculated with category: {category_for_scores}")
            logger.info(f"🔍 Scores: social={scores.social}, env={scores.environmental}, econ={scores.economic}")
            logger.info(f"🔍 Certifications from scores: {scores.certifications}")
        except Exception as e:
            logger.error(f"Score calculation error for brand '{brand}': {e}")
            # Return default scores
            scores = BrandData(
                brand=brand,
                social=safe_float(ScoringConfig.BASE_SCORE),
                environmental=safe_float(ScoringConfig.BASE_SCORE),
                economic=safe_float(ScoringConfig.BASE_SCORE),
                certifications=[],
                scoring_method="error_fallback",
                notes=f"Error calculating scores: {str(e)}"
            )

        # ===== STEP 4: Calculate overall score =====
        tbl = calculate_overall_score(
            scores.social,
            scores.environmental,
            scores.economic)

        # Use canonical brand if available
        original_brand = brand
        canonical_brand = cert_result.get("canonical_brand")
        if canonical_brand:
            brand = canonical_brand
            logger.info(f"Using canonical brand: '{original_brand}' → '{brand}'")

        logger.info(
            f"Scan result for {brand}: score={tbl['overall_score']}, certs={scores.certifications}")

        # Build response - ensure all values are not None
        certifications = list(getattr(scores, 'certifications', []))

        # ===== DEBUG: Log what's going into the response =====
        logger.info(f"🔍 certifications list: {certifications}")
        logger.info(f"🔍 'B Corp' in certifications: {'B Corp' in certifications}")
        # ===== END DEBUG =====

        response_data = {
            "barcode": barcode or "",
            "brand": brand or "Unknown",
            "brand_display": f"{brand} ({category})" if category else brand,
            "product_name": product_name or "Unknown Product",
            "category": category or "",
            "social_score": safe_float(getattr(scores, 'social', 0.0)),
            "environmental_score": safe_float(getattr(scores, 'environmental', 0.0)),
            "economic_score": safe_float(getattr(scores, 'economic', 0.0)),
            "overall_tbl_score": safe_float(tbl.get("overall_score", 0.0)),
            "grade": tbl.get("grade", "UNKNOWN"),
            "rating": tbl.get("grade", "UNKNOWN"),
            "certifications": certifications,
            "certifications_detailed": {
                "b_corp": "B Corp" in certifications,
                "fair_trade": "Fair Trade" in certifications,
                "rainforest_alliance": "Rainforest Alliance" in certifications,
                "leaping_bunny": "Leaping Bunny" in certifications,
                "research_complete": cert_result.get("certifications", {}).get("research_complete", False) if cert_result.get("certifications") else False,
            },
            "certification_source": "Excel Database (single source of truth)",
            "scoring_method": getattr(scores, 'scoring_method', 'error_fallback'),
            "notes": getattr(scores, 'notes', 'Error processing request'),
            "found_in_excel": cert_result.get("found", False),
            "excel_details": cert_result.get("details", {}),
            "match_type": cert_result.get("match_type"),
            "note": cert_result.get("note"),
            "matched_category": cert_result.get("matched_category"),
            "certification_verified_date": datetime.utcnow().isoformat(),
            "certification_sources": FileConfig.CERT_SOURCES,
            "scoring_methodology": f"Base {ScoringConfig.BASE_SCORE} + Objective Certification Bonuses Only + Multi-Cert Bonus",
            "methodology_explanation": "See /scoring-methodology for detailed breakdown",
            "brand_extraction_info": brand_extraction_info,
            "original_search_brand": cert_result.get("search_brand_used", original_brand),
            "brand_was_corrected": canonical_brand is not None,
            "brand_correction_note": f"Corrected to canonical brand: '{canonical_brand}'" if canonical_brand else "No brand correction needed",
            "success": True,
            "timestamp": datetime.utcnow().isoformat()
        }

        return sanitize_for_json(response_data)

    except Exception as e:
        logger.error(f"Unhandled error in scan_product: {e}", exc_info=True)
        error_response = {
            "success": False,
            "error": str(e),
            "barcode": str(
                getattr(
                    product,
                    'barcode',
                    '')) if getattr(
                product,
                'barcode',
                '') else "",
            "brand": str(
                getattr(
                    product,
                    'brand',
                    'Unknown')) if getattr(
                product,
                'brand',
                'Unknown') else "Unknown",
            "product_name": str(
                getattr(
                    product,
                    'product_name',
                    'Unknown Product')) if getattr(
                product,
                'product_name',
                'Unknown Product') else "Unknown Product",
            "category": str(
                getattr(
                    product,
                    'category',
                    '')) if getattr(
                product,
                'category',
                '') else "",
            "social_score": 0.0,
            "environmental_score": 0.0,
            "economic_score": 0.0,
            "overall_tbl_score": 0.0,
            "grade": "ERROR",
            "rating": "ERROR",
            "certifications": [],
            "certifications_detailed": {
                "b_corp": False,
                "fair_trade": False,
                "rainforest_alliance": False,
                "leaping_bunny": False,
            },
            "scoring_method": "error_fallback",
            "notes": f"Error processing request: {str(e)}",
            "timestamp": datetime.utcnow().isoformat()}
        return sanitize_for_json(error_response)


@app.post("/extract-brand")
async def extract_brand_endpoint(search: ProductSearch) -> Dict[str, Any]:
    """Extract brand name from product name using enhanced methods"""
    logger.info(f"Extract brand request for product: {search.product_name}")

    result = await brand_extraction_manager.extract_brand_from_product_name(
        search.product_name
    )

    return {"product_name": search.product_name, "result": result}


@app.get("/test/brand-extraction/{product_name}")
async def test_brand_extraction_endpoint(product_name: str):
    """Test endpoint for brand extraction"""
    result = await brand_extraction_manager.extract_brand_from_product_name(
        product_name
    )
    parent_company = BrandNormalizer.find_parent_company(product_name)

    return {
        "product_name": product_name,
        "extraction_result": result,
        "parent_company": parent_company,
        "normalized_product_name": BrandNormalizer.normalize(product_name),
    }


@app.get("/search-brand")
async def search_brand(q: str = Query(...), category: str = Query(None)):
    """Search for a brand with fuzzy matching and OFF discovery fallback"""
    # Load certification data if not already loaded
    if certification_manager.data is None:
        certification_manager.load_certification_data()

    # Check if we have data
    if certification_manager.data is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    best_match = None
    best_score = 0
    search_query = q.lower().strip()

    if len(search_query) < 2:
        return {"suggestions": [], "source": "local", "query": q}

    # 1. Try local Excel search for auto-suggest
    try:
        pd = get_pandas()
        if not os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE):
            logger.warning(f"Excel file not found: {FileConfig.CERTIFICATION_EXCEL_FILE}")
            df = None
        else:
            df = pd.read_excel(FileConfig.CERTIFICATION_EXCEL_FILE)

            if df is not None and 'Product_Brand' in df.columns:
                matches = []
                seen_brands = set()

                for _, row in df.iterrows():
                    brand_name = str(row['Product_Brand'])
                    brand_lower = brand_name.lower()

                    # Simple starts-with check for auto-suggest
                    if brand_lower.startswith(search_query):
                        if brand_name not in seen_brands:
                            seen_brands.add(brand_name)
                            matches.append({
                                "brand": brand_name,
                                "category": row.get('Category', ''),
                                "confidence": 100
                            })

                    # Limit results for performance
                    if len(matches) >= 10:
                        break

                if matches:
                    return {
                        "suggestions": matches,
                        "source": "local_database",
                        "query": q,
                        "success": True
                    }
    except Exception as e:
        logger.error(f"Error reading Excel file for search: {e}")
        df = None

    # 2. IF local search score is low, query OFF for discovery
    if not best_match or best_score < 60:
        logger.info(
            f"Low local match score ({round(best_score, 1)}%). Discovering via OFF...")

        off_url = f"https://world.openfoodfacts.org/api/v2/search?brands_tags={quote(q)}&fields=product_name,brands,image_small_url,code&page_size=5"

        async with httpx.AsyncClient() as client:
            try:
                headers = {"User-Agent": "TBLGroceryScanner/1.0"}
                response = await client.get(off_url, headers=headers, timeout=10.0)

                if response.status_code == 200:
                    products = response.json().get("products", [])
                    if products:
                        return {
                            "source": "open_food_facts",
                            "query": q,
                            "message": "Brand not found in local records. Showing web matches:",
                            "discovered_products": [
                                {
                                    "name": p.get(
                                        "product_name",
                                        "Unknown Product"),
                                    "brand": p.get(
                                        "brands",
                                        "Unknown Brand"),
                                    "image": p.get("image_small_url"),
                                    "barcode": p.get("code")} for p in products],
                            "success": True}
            except Exception as e:
                logger.error(f"OFF Search Error: {str(e)}")

    # 3. Return local result if a good match was found
    if best_match and best_score >= 60:
        parent_company = BrandNormalizer.find_parent_company(best_match)
        target_brand = parent_company or best_match

        scores = scoring_manager.calculate_brand_scores(target_brand, category)  # Pass category if availabl
        tbl = calculate_overall_score(
            scores.social,
            scores.environmental,
            scores.economic)

        return {
            "source": "local_database",
            "match_quality": round(best_score, 2),
            "brand": best_match,
            "parent_company": parent_company,
            "overall_tbl_score": safe_float(tbl.get("overall_score")),
            "grade": tbl.get("grade", "N/A"),
            "social_score": safe_float(scores.social),
            "environmental_score": safe_float(scores.environmental),
            "economic_score": safe_float(scores.economic),
            "certifications": list(scores.certifications),
            "success": True
        }

    return UTF8JSONResponse(
        status_code=404,
        content={
            "success": False,
            "message": "No matching brands found locally or on the web."})

# ==================== SHEET SEARCH ENDPOINTS ====================

_VALID_CERT_KEYS = frozenset({
    "b_corp", "fair_trade", "rainforest_alliance", "leaping_bunny",
})


def _parse_cert_filter(certs: str) -> set:
    """Parse the certs query param into a validated set."""
    cert_filter = {c.strip() for c in (certs or "").split(",") if c.strip()}
    unknown = cert_filter - _VALID_CERT_KEYS
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown cert key(s): {sorted(unknown)}. "
                   f"Valid: {sorted(_VALID_CERT_KEYS)}",
        )
    return cert_filter


def _score_and_format_sheet_row(
    brand_key: str,
    product_key: str,
    pd_data: dict,
    matched_term: Optional[str] = None,
) -> dict:
    """Score one row via the shared scoring path and build a result dict.

    Reuses certification_manager._format_response and
    scoring_manager.calculate_brand_scores so the browse tab's
    numbers match every other tab.
    """
    cert_result = certification_manager._format_response(
        found=True, data=pd_data, match_type="sheet_search"
    )
    scores = scoring_manager.calculate_brand_scores(
        pd_data.get("original_brand", brand_key),
        product_key,
        cert_result,
    )
    tbl = calculate_overall_score(
        scores.social, scores.environmental, scores.economic
    )
    true_category = certification_manager.product_form_categories.get(
        (matched_term or "").strip().lower()
    )
    return {
        "brand": pd_data.get("original_brand", brand_key),
        "category": product_key,
        "certifications": scores.certifications,
        "overall_tbl_score": tbl["overall_score"],
        "grade": tbl["grade"],
        "social_score": scores.social,
        "environmental_score": scores.environmental,
        "economic_score": scores.economic,
        "matched_term": matched_term,
        "true_category": true_category,
    }


def _run_sheet_search(
    refs: list,
    matched_terms: dict,
    cert_filter: set,
    certified_only: bool,
    limit: int,
    offset: int,
    q: str,
    source: str,
) -> dict:
    """Common logic for /sheet-search and /sheet-search/taxonomy.

    refs: list of (brand_key, product_key) to consider
    matched_terms: (brand_key, product_key) -> token that matched
    """
    results = []
    for ref in refs:
        brand_key, product_key = ref
        pd_data = certification_manager.data[brand_key][product_key]
        certs_bool = pd_data.get("certifications") or {}

        # Strict filtering by the exact product form searched.
        # Without this, /sheet-search falls back to "any row whose
        # brand matches any brand in the cert_cat_index prefix
        # match," which returns rows that never had the searched
        # form in Column H. Example: searching "baby food jars"
        # was returning an en:nutritional-supplements row because
        # the same brand also sells baby food jars.
        if q:
            row = pd_data.get("row_data") or {}
            raw_h = row.get("Certification_Categories")
            if raw_h is None:
                continue
            try:
                if raw_h != raw_h:  # NaN check without importing pandas
                    continue
            except Exception:
                pass
            categories_list = [c.strip().lower() for c in str(raw_h).split(",")]
            if q.strip().lower() not in categories_list:
                continue

        if cert_filter:
            if not all(certs_bool.get(c) for c in cert_filter):
                continue
        elif certified_only:
            if not any(certs_bool.get(k) for k in _VALID_CERT_KEYS):
                continue

        results.append(_score_and_format_sheet_row(
            brand_key, product_key, pd_data,
            matched_term=matched_terms.get(ref),
        ))

    # Deduplicate: keep only the highest-scoring row per brand.
    # A brand can have multiple rows in the Excel (different en: categories
    # that share a product form). Showing the same brand twice in the
    # results is confusing even when the data is accurate.
    seen_brands = {}
    for r in results:
        brand_name = r["brand"]
        if brand_name not in seen_brands or r["overall_tbl_score"] > seen_brands[brand_name]["overall_tbl_score"]:
            seen_brands[brand_name] = r
    results = list(seen_brands.values())

    # Sort BEFORE slice: desc by score, then ascending brand for
    # stable pagination.
    results.sort(key=lambda r: (-r["overall_tbl_score"], r["brand"]))

    total = len(results)
    page = results[offset:offset + limit]

    return {
        "query": q,
        "source": source,
        "certs": sorted(cert_filter),
        "total_matches": total,
        "showing": len(page),
        "offset": offset,
        "results": page,
    }


@app.get("/sheet-search")
async def sheet_search(
    q: str = Query("", max_length=100),
    certs: str = Query(""),
    certified_only: bool = True,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Product-form search (Column H tokens). Shopper default.

    q is an exact match against a Column H token, case-insensitive.
    Results are all rows whose Column H contains that token, sorted
    desc by overall TBL score, then ascending by brand.

    When q is empty and no certs filter is given, returns 400 —
    there's nothing useful to show.
    """
    if certification_manager.data is None:
        certification_manager.load_certification_data()
    if certification_manager.data is None:
        raise HTTPException(status_code=503, detail="Certification data not loaded")

    q_norm = q.strip().lower()
    cert_filter = _parse_cert_filter(certs)
    # Ambiguous-form handling cascade (guaranteed no dead end).
    ambiguous_fallback = False
    ambiguous_notes = ""
    if q_norm in certification_manager.product_form_categories:
        cat = certification_manager.product_form_categories[q_norm]
        if cat == "":
            # Steps 1 and 2: auto-detected + manually listed sub-forms,
            # both stored in product_form_suggestions.
            sugg_raw = certification_manager.product_form_suggestions.get(q_norm, "")
            suggestions = [s.strip() for s in sugg_raw.split(",") if s.strip()]
            if suggestions:
                return {
                    "ambiguous_form": True,
                    "input": q_norm,
                    "notes": certification_manager.product_form_notes.get(q_norm, "")
                             or f"{q_norm} is too generic. Pick a specific type.",
                    "suggestions": suggestions,
                }
            # Steps 3 and 4: no suggestions; fall through to the normal
            # search with a warning banner.
            ambiguous_fallback = True
            ambiguous_notes = (
                certification_manager.product_form_notes.get(q_norm, "")
                or f"{q_norm} may span multiple categories."
            )
    if not q_norm and not cert_filter:
        raise HTTPException(
            status_code=400,
            detail="Provide a product-form term (q) or at least one cert filter (certs).",
        )

    if q_norm:
        refs = list(certification_manager.cert_cat_index.get(q_norm, []))
        matched_terms = {r: q_norm for r in refs}
    else:
        # No q — scan everything, cert filter narrows it
        refs = [
            (bk, pk)
            for bk, products in certification_manager.data.items()
            for pk in products
            if pk != "_default"
        ]
        matched_terms = {}

    result = _run_sheet_search(
        refs=refs,
        matched_terms=matched_terms,
        cert_filter=cert_filter,
        certified_only=certified_only,
        limit=limit,
        offset=offset,
        q=q,
        source="product-form",
    )
    if ambiguous_fallback:
        result["ambiguous_fallback"] = True
        result["ambiguous_notes"] = ambiguous_notes

    return result


@app.get("/sheet-search/taxonomy")
async def sheet_search_taxonomy(
    q: str = Query("", max_length=100),
    certs: str = Query(""),
    certified_only: bool = True,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Taxonomy search (Column B en: codes). Researcher path.

    q is an exact match against a Column B value, case-insensitive.
    The en: prefix is expected (e.g. "en:coffees"). Results are all
    rows categorized under that taxonomy, sorted desc by overall TBL
    score, then ascending by brand.

    When q is empty and no certs filter is given, returns 400.
    """
    if certification_manager.data is None:
        certification_manager.load_certification_data()
    if certification_manager.data is None:
        raise HTTPException(status_code=503, detail="Certification data not loaded")

    q_norm = q.strip().lower()
    cert_filter = _parse_cert_filter(certs)

    if not q_norm and not cert_filter:
        raise HTTPException(
            status_code=400,
            detail="Provide a taxonomy code (q) or at least one cert filter (certs).",
        )

    if q_norm:
        refs = list(certification_manager.category_index.get(q_norm, []))
        matched_terms = {r: q_norm for r in refs}
    else:
        refs = [
            (bk, pk)
            for bk, products in certification_manager.data.items()
            for pk in products
            if pk != "_default"
        ]
        matched_terms = {}

    return _run_sheet_search(
        refs=refs,
        matched_terms=matched_terms,
        cert_filter=cert_filter,
        certified_only=certified_only,
        limit=limit,
        offset=offset,
        q=q,
        source="taxonomy",
    )


@app.get("/sheet-search/tokens")
async def sheet_search_tokens(
    prefix: str = Query("", max_length=100),
    limit: int = Query(50, ge=1, le=200),
):
    """Autocomplete for /sheet-search (Column H product-form tokens).

    prefix may be empty. Empty prefix returns the full list (up to
    limit), sorted by brand count desc then alphabetically. Non-empty
    prefix returns tokens that start with prefix, same sort.

    Result items are { token, count } where count is the number of
    rows carrying that token.
    """
    if certification_manager.data is None:
        certification_manager.load_certification_data()
    if certification_manager.data is None:
        raise HTTPException(status_code=503, detail="Certification data not loaded")

    p = prefix.strip().lower()
    counts = {}
    for token, refs in certification_manager.cert_cat_index.items():
        if not p or token.startswith(p):
            counts[token] = len(refs)
    # Fallback: if prefix match returned nothing, retry as word-boundary
    # prefix. Catches "oil" matching "cooking oils" (via oils.startswith
    # ("oil")) but not "toilet paper" (via paper.startswith("oil") = False).
    if p and not counts:
        for token, refs in certification_manager.cert_cat_index.items():
            if any(word.startswith(p) for word in token.split()):
                counts[token] = len(refs)

    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "prefix": prefix,
        "source": "product-form",
        "tokens": [
            {"token": t, "count": c}
            for t, c in ordered[:limit]
        ],
    }


@app.get("/sheet-search/taxonomies")
async def sheet_search_taxonomies(
    prefix: str = Query("", max_length=100),
    limit: int = Query(50, ge=1, le=200),
):
    """Autocomplete for /sheet-search/taxonomy (Column B en: codes).

    prefix may be empty. Empty prefix returns the full list (up to
    limit), sorted by row count desc then alphabetically. Non-empty
    prefix returns taxonomy codes starting with prefix.

    Result items are { token, count } where token is the en: code and
    count is the number of rows in that taxonomy.
    """
    if certification_manager.data is None:
        certification_manager.load_certification_data()
    if certification_manager.data is None:
        raise HTTPException(status_code=503, detail="Certification data not loaded")

    p = prefix.strip().lower()
    counts = {}
    for token, refs in certification_manager.category_index.items():
        if not p or token.startswith(p):
            counts[token] = len(refs)

    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "prefix": prefix,
        "source": "taxonomy",
        "tokens": [
            {"token": t, "count": c}
            for t, c in ordered[:limit]
        ],
    }

# ==================== EXCEL MANAGEMENT ENDPOINTS ====================


@app.get("/categories")
async def get_categories():
    """Get all unique categories from Excel"""
    if certification_manager.data is None:
        return {"categories": []}

    all_categories = set()
    for brand, products in certification_manager.data.items():
        for product_key in products.keys():
            if product_key != "_default":
                all_categories.add(product_key)

    return {
        "categories": sorted(list(all_categories))
    }

@app.get("/certifications/status")
async def get_certification_status():
    """Get status of certification data"""
    certification_manager.load_certification_data()

    if certification_manager.data is None:
        return {
            "status": "error",
            "message": "Certification data not loaded",
            "excel_file": FileConfig.CERTIFICATION_EXCEL_FILE,
            "exists": os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE),
        }

    # Get sample brands with their certifications
    sample_brands = []
    for i, (brand_key, data) in enumerate(certification_manager.data.items()):
        if i >= 5:
            break
        sample_brands.append(
            {
                "original_brand": data["original_brand"],
                "normalized": brand_key,
                "certifications": data["certifications"],
            }
        )

    return {
        "status": "success",
        "excel_file": FileConfig.CERTIFICATION_EXCEL_FILE,
        "total_records": len(certification_manager.data),
        "last_loaded": (
            certification_manager.last_loaded.isoformat()
            if certification_manager.last_loaded
            else None
        ),
        "sample_brands": sample_brands,
    }


@app.post("/certifications/upload")
async def upload_certifications(file: UploadFile = File(...)):
    """Upload new Excel file with certification data"""
    try:
        # Read the uploaded file
        contents = await file.read()

        # Save to the certification file
        with open(FileConfig.CERTIFICATION_EXCEL_FILE, "wb") as f:
            f.write(contents)

        # Reload data
        certification_manager.load_certification_data()

        return {
            "status": "success",
            "message": "Certification data uploaded successfully",
            "filename": file.filename,
            "total_records": (
                len(certification_manager.data) if certification_manager.data else 0
            ),
        }

    except Exception as e:
        logger.error(f"Error uploading certification file: {e}")
        raise HTTPException(status_code=500,
                            detail=f"Error uploading file: {str(e)}")


@app.get("/certifications/search/{brand}")
async def search_certifications(brand: str):
    """Search for a brand in the certification database"""
    result = certification_manager.get_certifications(brand)

    # Sanitize the result
    sanitized_result = sanitize_for_json(result)

    return {
        "brand": brand,
        "found": sanitized_result.get("found", False),
        "certifications": sanitized_result.get("certifications", {}),
        "details": sanitized_result.get("details", {}),
        "match_type": sanitized_result.get("match_type"),
        "note": sanitized_result.get("note")
    }


@app.get("/certifications/export")
async def export_certifications():
    """Export certification data as JSON"""
    if certification_manager.data is None:
        certification_manager.load_certification_data()

    if certification_manager.data is None:
        raise HTTPException(status_code=404,
                            detail="No certification data available")

    return UTF8JSONResponse(content=certification_manager.data)


@app.get("/excel/verify")
async def verify_excel_data():
    """Verify Excel data is loaded correctly and provide detailed statistics"""
    try:
        # Check if data is loaded
        if certification_manager.data is None:
            logger.info("Excel data not loaded, attempting to load...")
            certification_manager.load_certification_data()

        if certification_manager.data is None:
            return {
                "status": "error",
                "message": "No Excel data loaded",
                "path": FileConfig.CERTIFICATION_EXCEL_FILE,
                "file_exists": os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE)
            }

        # Get file info
        file_exists = os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE)
        file_size = os.path.getsize(FileConfig.CERTIFICATION_EXCEL_FILE) if file_exists else 0

        # Count certifications
        cert_counts = {
            "b_corp": 0,
            "fair_trade": 0,
            "rainforest_alliance": 0,
            "leaping_bunny": 0,
            "research_complete": 0
        }

        # Brand categories stats
        brands_with_categories = 0
        total_categories = set()

        for brand_key, products in certification_manager.data.items():
            for product_key, product_data in products.items():
                if product_key != "_default":
                    brands_with_categories += 1
                    total_categories.add(product_key)
                    # Count certifications
                    certs = product_data.get("certifications", {})
                    for cert in cert_counts.keys():
                        if certs.get(cert, False):
                            cert_counts[cert] += 1

        # Get a few sample brands
        sample_brands = []
        for i, (brand_key, products) in enumerate(certification_manager.data.items()):
            if i >= 5:
                break
            first_product = next(iter(products.values()))
            sample_brands.append({
                "brand": brand_key,
                "original_brand": first_product.get("original_brand"),
                "certifications": first_product.get("certifications", {}),
                "categories": list(products.keys())[:3]  # Show up to 3 categories
            })

        return {
            "status": "success",
            "timestamp": datetime.utcnow().isoformat(),
            "file_info": {
                "path": FileConfig.CERTIFICATION_EXCEL_FILE,
                "exists": file_exists,
                "size_bytes": file_size,
                "size_mb": round(file_size / (1024 * 1024), 2) if file_exists else 0
            },
            "data_stats": {
                "total_brands": len(certification_manager.data),
                "brands_with_categories": brands_with_categories,
                "total_categories": len(total_categories),
                "categories": sorted(list(total_categories))[:20],  # Show first 20
                "certification_counts": cert_counts
            },
            "last_loaded": certification_manager.last_loaded.isoformat() if certification_manager.last_loaded else None,
            "sample_brands": sample_brands,
            "is_healthy": len(certification_manager.data) > 0
        }

    except Exception as e:
        logger.error(f"Error verifying Excel data: {e}")
        import traceback
        return {
            "status": "error",
            "message": str(e),
            "traceback": traceback.format_exc()
        }


# ==================== SCRIPT EXECUTION ENDPOINTS ====================


@app.post("/certifications/create-excel")
async def create_excel_file():
    """Execute the create_excel.py script to generate Excel file"""
    result = run_create_excel_script()

    if result["success"]:
        return {
            "status": "success",
            "message": result["message"],
            "output": result["output"],
            "excel_file_created": result["excel_file_created"],
            "excel_file_size": result["excel_file_size"],
            "excel_file_path": (
                os.path.abspath(FileConfig.CERTIFICATION_EXCEL_FILE)
                if os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE)
                else None
            ),
        }
    else:
        raise HTTPException(status_code=500, detail=result["message"])


@app.get("/certifications/verify-script")
async def verify_script_status():
    """Verify the status of create_excel.py script and Excel file"""
    result = verify_excel_script()

    return {
        "status": "success",
        "timestamp": datetime.utcnow().isoformat(),
        **result}


@app.post("/certifications/reset")
async def reset_excel_file():
    """Reset Excel file by running create_excel.py script"""
    # Backup old file if it exists
    backup_file = None
    if os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE):
        backup_file = f"{FileConfig.CERTIFICATION_EXCEL_FILE}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            import shutil

            shutil.copy2(FileConfig.CERTIFICATION_EXCEL_FILE, backup_file)
            logger.info(f"Backed up old Excel file to: {backup_file}")
        except Exception as e:
            logger.warning(f"Could not backup old Excel file: {e}")

    # Run create_excel script
    result = run_create_excel_script()

    if result["success"]:
        response = {
            "status": "success",
            "message": result["message"],
            "output": result["output"],
            "excel_file_created": result["excel_file_created"],
            "excel_file_size": result["excel_file_size"],
            "backup_created": backup_file is not None and os.path.exists(backup_file),
        }

        if backup_file and os.path.exists(backup_file):
            response["backup_file"] = backup_file

        return response
    else:
        raise HTTPException(status_code=500, detail=result["message"])


# ==================== BARCODE VALIDATION ENDPOINT ====================


@app.get("/validate/barcode/{barcode}")
async def validate_barcode_format(barcode: str):
    """Validate barcode format and provide compatibility info for Html5Qrcode"""
    # Html5Qrcode supported formats
    patterns = {
        "EAN-13": r"^\d{13}$",
        "EAN-8": r"^\d{8}$",
        "UPC-A": r"^\d{12}$",
        "UPC-E": r"^\d{6,8}$",
        "Code 128": r"^[\x00-\x7F]+$",
        "Code 39": r"^[A-Z0-9\-\.\ \$\/\+\%]+$",
        "Code 93": r"^[A-Z0-9\-\.\ \$\/\+\%]+$",
        "Codabar": r"^[0-9\-\$\:\.\+\/]+$",
        "ITF": r"^\d+$",  # Interleaved 2 of 5
        "PDF417": r"^.+$",
        "Data Matrix": r"^.+$",
        "QR Code": r"^.+$",
    }

    detected_formats = []
    for format_name, pattern in patterns.items():
        if re.match(pattern, barcode):
            detected_formats.append(format_name)

    return {
        "barcode": barcode,
        "length": len(barcode),
        "detected_formats": detected_formats,
        "is_numeric": barcode.isdigit(),
        "html5qrcode_compatible": len(detected_formats) > 0,
        "library": "Html5Qrcode v2.3.8",
        "suggested_action": (
            "âœ“ Compatible with Html5Qrcode scanner"
            if detected_formats
            else "âš ï¸ This format may not be supported. Try manual entry."
        ),
    }


# ==================== TEST ENDPOINTS ====================


@app.get("/test/excel/{brand}")
async def test_excel_lookup(brand: str):
    """Test endpoint to check Excel lookup for a specific brand"""
    result = certification_manager.get_certifications(brand)

    # Sanitize the result to handle NaN/NaT values
    sanitized_result = sanitize_for_json(result)

    # Also get brand categories for debugging
    brand_normalized = BrandNormalizer.normalize(brand)
    categories = certification_manager.brand_categories.get(brand_normalized, set())

    return {
        "test_brand": brand,
        "normalized_brand": brand_normalized,
        "brand_categories": list(categories),
        "result": sanitized_result,
        "total_brands_in_excel": len(certification_manager.data) if certification_manager.data else 0
    }


# ==================== OTHER ENDPOINTS ====================


@app.post("/compare")
async def compare_brands(brands: List[BrandInput]) -> Dict[str, Any]:
    """Compare multiple brands with verified certifications"""
    comparison = []

    for brand_obj in brands:
        brand = brand_obj.brand
        scores = scoring_manager.calculate_brand_scores(brand)
        tbl = calculate_overall_score(
            scores.social, scores.environmental, scores.economic
        )
        cert_result = certification_manager.get_certifications(brand)  # Remove category parameter

        comparison.append(
            {
                "brand": brand,
                "social_score": scores.social,
                "environmental_score": scores.environmental,
                "economic_score": scores.economic,
                "overall_score": tbl["overall_score"],
                "grade": tbl["grade"],
                "certifications": scores.certifications,
                "scoring_method": scores.scoring_method,
                "notes": scores.notes,
                "found_in_excel": cert_result["found"],
                "multi_cert_applied": scores.multi_cert_applied,
                "multi_cert_bonus": scores.multi_cert_bonus,
            }
        )

    if not comparison:
        raise HTTPException(status_code=400, detail="No valid brands provided")

    comparison.sort(key=lambda x: x["overall_score"], reverse=True)

    logger.info(f"Compared {len(brands)} brands")
    return sanitize_for_json({"comparison": comparison})


@app.post("/purchase")
async def record_purchase(
    username: str = Query(...), product: Optional[Product] = None
) -> Dict[str, Any]:
    """Record user purchase"""
    if username not in USERS_DB:
        raise HTTPException(status_code=404, detail="User not found")

    if not product:
        raise HTTPException(status_code=400, detail="Product data required")

    scores = scoring_manager.calculate_brand_scores(product.brand)
    tbl = calculate_overall_score(
        scores.social,
        scores.environmental,
        scores.economic)

    purchase = {
        "barcode": product.barcode,
        "brand": product.brand,
        "product_name": product.product_name,
        "category": product.category,
        "price": product.price or 0,
        "tbl_score": tbl["overall_score"],
        "certifications": scores.certifications,
        "scoring_method": scores.scoring_method,
        "timestamp": datetime.utcnow().isoformat(),
        "scoring_methodology": f"Base {ScoringConfig.BASE_SCORE} + Certification Bonuses + Multi-Cert Bonus",
    }

    if username not in PURCHASE_HISTORY_DB:
        PURCHASE_HISTORY_DB[username] = []
    PURCHASE_HISTORY_DB[username].append(purchase)

    # ✅ ADD THIS LINE: Save to persistent storage
    save_user_data()

    logger.info(f"Purchase recorded for {username}: {product.product_name}")
    return {"message": "Purchase recorded", "purchase": purchase}


@app.get("/history/{username}")
async def get_purchase_history(
        username: str, limit: int = 50) -> Dict[str, Any]:
    """Get user purchase history"""
    if username not in USERS_DB:
        raise HTTPException(status_code=404, detail="User not found")

    history = PURCHASE_HISTORY_DB.get(username, [])

    # Calculate average TBL score
    avg_score = 0
    if history:
        avg_score = sum(p.get("tbl_score", 0) for p in history) / len(history)

    return {
        "username": username,
        "total_purchases": len(history),
        "average_tbl_score": round(avg_score, 2),
        "purchases": history[-limit:],
    }


@app.get("/debug/users")
async def debug_users():
    """Debug endpoint to check users"""
    return {
        "users": list(USERS_DB.keys()),
        "test_user_exists": "Test123" in USERS_DB,
        "alb_exists": "ALB" in USERS_DB,
        "total_users": len(USERS_DB),
        "purchase_history_counts": {user: len(PURCHASE_HISTORY_DB.get(user, []))
                                    for user in USERS_DB.keys()}
    }


@app.get("/debug/storage")
async def debug_storage():
    """Debug endpoint to check storage status"""
    file_exists = os.path.exists(USER_DATA_FILE)
    file_size = os.path.getsize(USER_DATA_FILE) if file_exists else 0

    return {
        "storage_file": USER_DATA_FILE,
        "file_exists": file_exists,
        "file_size_bytes": file_size,
        "total_users": len(USERS_DB),
        "total_purchases": sum(
            len(purchases) for purchases in PURCHASE_HISTORY_DB.values()),
        "users": list(
            USERS_DB.keys()),
    }


@app.get("/product/{barcode}")
async def get_product_info(barcode: str) -> Dict[str, Any]:
    """Get comprehensive product info by barcode with verified certifications"""

    # ===== SAFETY CHECK: Check if data is ready =====
    if certification_manager.data is None:
        logger.warning("⚠️ Data not ready, attempting to load...")
        certification_manager.load_certification_data()

        # Wait and retry if still not ready
        retries = 3
        while certification_manager.data is None and retries > 0:
            logger.info(f"⏳ Waiting for data to load... ({retries} retries left)")
            time.sleep(1)
            retries -= 1

        if certification_manager.data is None:
            return {
                "barcode": barcode,
                "found": False,
                "brand": "Loading",
                "name": "Please Wait",
                "category": "Loading",
                "message": "Product database is still loading. Please try again in a moment.",
                "overall_tbl_score": 5.0,
                "grade": "GOOD",
                "certifications": [],
                "loading": True,
                "retry": True
            }

    # Add Html5Qrcode validation
    if not barcode or barcode.strip() == "":
        raise HTTPException(
            status_code=400,
            detail="Empty barcode. Please try scanning again or enter manually.",
        )

    # Check if barcode looks valid
    if len(barcode) < 6:
        logger.warning(f"Short barcode detected: {barcode}. May be misread.")

    # ===== GET PRODUCT FROM OFF FIRST =====
    product = await get_cached_off_product(barcode)

    # Enhanced logging for debugging scanner issues
    logger.info(
        f"Html5Qrcode scan -> Barcode: {barcode}, Length: {len(barcode)}, Found in OFF: {product.get('found', False)}"
    )

    # ===== HANDLE CASE WHERE OFF DOESN'T FIND THE PRODUCT =====
    if not product.get("found", False):
        return {
            "barcode": barcode,
            "found": False,
            "brand": "Unknown",
            "name": "Not Found in Open Food Facts",
            "category": "Unknown",
            "message": "This barcode was not found in Open Food Facts. Try searching by brand name instead.",
            "overall_tbl_score": 5.0,
            "grade": "GOOD",
            "certifications": [],
            "social_score": 5.0,
            "environmental_score": 5.0,
            "economic_score": 5.0,
            "scoring_method": "default_fallback",
            "notes": "Product not found in Open Food Facts. Using default score of 5.0.",
            "found_in_excel": False,
            "scanner_notes": "Try scanning again with better lighting or enter the barcode manually.",
        }

    brand_name = product.get("brand", "Unknown")
    if brand_name != "Unknown":
        brand_name = brand_name.replace("The ", "").strip()

    # ===== GET CERTIFICATIONS FROM EXCEL (if available) =====
    cert_result = get_cached_certifications(
        brand_name,
        product.get("category"),
        source="barcode",
        barcode=barcode,
    )
    found_in_excel = cert_result.get("found", False)

    # ===== ALWAYS USE OFF BRAND AND PRODUCT NAME =====
    # Use the brand from OFF (not Excel canonical)
    display_brand = brand_name if brand_name != "Unknown" else product.get("brand", "Unknown")

    # ===== CALCULATE SCORES =====
    if found_in_excel:
        scores = scoring_manager.calculate_brand_scores(
            display_brand,
            product.get("category") or None,
            cert_result,
        )
    else:
        # Use default scores but mark as "Not in database"
        scores = BrandData(
            brand=display_brand,
            social=5.0,
            environmental=5.0,
            economic=5.0,
            certifications=[],
            scoring_method="default_fallback",
            notes="Brand not found in certification database. Using default score of 5.0."
        )

    tbl = calculate_overall_score(
        scores.social,
        scores.environmental,
        scores.economic
    )

    # ===== BUILD RESULT =====
    result = {
        "barcode": barcode,
        "found": True,  # Always true since we found it in OFF
        "name": product.get("name", "Unknown Product"),
        "brand": display_brand,
        "category": product.get("category", "Unknown"),
        "social_score": scores.social,
        "environmental_score": scores.environmental,
        "economic_score": scores.economic,
        "overall_tbl_score": tbl["overall_score"],
        "grade": tbl["grade"],
        "rating": tbl["grade"],
        "certifications": scores.certifications,
        "certifications_detailed": {
            "b_corp": "B Corp" in scores.certifications,
            "fair_trade": "Fair Trade" in scores.certifications,
            "rainforest_alliance": "Rainforest Alliance" in scores.certifications,
            "leaping_bunny": "Leaping Bunny" in scores.certifications,
            "research_complete": cert_result.get("certifications", {}).get("research_complete", False) if cert_result.get("certifications") else False,
        },
        "scoring_method": scores.scoring_method,
        "notes": scores.notes,
        "multi_cert_applied": scores.multi_cert_applied,
        "multi_cert_bonus": scores.multi_cert_bonus,
        "certification_source": "Excel Database" if found_in_excel else "No certifications found",
        "found_in_excel": found_in_excel,
        "excel_details": cert_result.get("details", {}),
        "match_type": cert_result.get("match_type"),
        "certification_verified_date": datetime.utcnow().isoformat() if found_in_excel else None,
        "certification_sources": FileConfig.CERT_SOURCES,
        "scoring_methodology": "Base 5.0 + Objective Certification Bonuses Only + Multi-Cert Bonus",
        "methodology_explanation": "See /scoring-methodology for detailed breakdown",
        "scanner_notes": "Scanned with Html5Qrcode v2.3.8. Lightweight, mobile-optimized barcode scanner.",
        "off_data": {  # Include OFF data
            "eco_score_grade": product.get("eco_score"),
            "eco_score_value": product.get("eco_score_value"),
            "nutriscore_grade": product.get("nutriscore"),
            "nutriscore_value": product.get("nutriscore_value"),
            "nutrition": {
                "energy_kcal": product.get("energy_kcal"),
                "fat_g": product.get("fat"),
                "carbohydrates_g": product.get("carbohydrates"),
                "proteins_g": product.get("proteins"),
                "salt_g": product.get("salt"),
            },
            "ingredients": product.get("ingredients"),
            "allergens": product.get("allergens"),
            "countries": product.get("countries"),
            "image_url": product.get("image_url"),
            "last_updated": product.get("last_updated"),
        }
    }

    logger.info(f"Product lookup for barcode: {barcode} - Found in OFF: True, Found in Excel: {found_in_excel}")
    return sanitize_for_json(result)


# ==================== SCANNER HEALTH ENDPOINT ====================


@app.get("/scanner/health")
async def scanner_health():
    """Check scanner system health and compatibility"""
    # Get Redis status
    redis_status = "not_configured"
    if redis_client:
        try:
            redis_client.ping()
            redis_status = "connected"
            keys_count = len(redis_client.keys("product:*"))
        except Exception as e:
            redis_status = f"error: {str(e)}"
            keys_count = 0
    else:
        keys_count = 0

    return {
        "scanner_system": "Html5Qrcode (Lightweight JavaScript Scanner)",
        "backend_integration": "✅ Ready",
        "library": "Html5Qrcode v2.3.8 - actively maintained",
        "redis": {
            "status": redis_status,
            "cached_products": keys_count,
            "url_configured": bool(os.getenv('REDIS_URL'))
        },
        "api_endpoints": {
            "scan": "/scan (POST) - Main scanning endpoint",
            "product_lookup": "/product/{barcode} (GET)",
            "barcode_validation": "/validate/barcode/{barcode} (GET)",
            "health": "/scanner/health (GET)",
            "cache_stats": "/cache/stats (GET)",
        },
        "supported_formats": [
            "EAN-13",
            "EAN-8",
            "UPC-A",
            "UPC-E",
            "Code 128",
            "Code 39",
            "Code 93",
            "Codabar",
            "ITF (Interleaved 2 of 5)",
            "PDF417",
            "Data Matrix",
            "QR Code",
            "Aztec",
            "MaxiCode"
        ],
        "camera_requirements": "User media permission required",
        "mobile_compatible": "Yes - optimized for Android and iOS",
        "https_required": "Required for camera access on most browsers",
        "fallback_methods": [
            "Manual barcode entry",
            "Brand name search via /extract-brand",
            "Product name search",
        ],
        "troubleshooting": {
            "no_camera": "Check browser permissions and ensure HTTPS",
            "poor_scanning": "Ensure good lighting and hold steady",
            "small_barcodes": "Move camera closer to small barcodes",
            "performance": "WASM provides near-native speed",
        },
    }


# ==================== OTHER ENDPOINTS ====================


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint"""
    excel_exists = os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE)
    excel_status = "found" if excel_exists else "not found"

    script_exists = os.path.exists(FileConfig.CREATE_EXCEL_SCRIPT)
    script_status = "found" if script_exists else "not found"

    # Check Redis status
    redis_status = "not_configured"
    redis_url_set = bool(os.getenv('REDIS_URL'))
    if redis_client:
        try:
            redis_client.ping()
            redis_status = "connected"
        except Exception as e:
            redis_status = f"error: {str(e)}"

    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "total_brands": len(certification_manager.data) if certification_manager.data else 0,
        "total_users": len(USERS_DB),
        "off_cache_size": len(OFF_CACHE),
        "scoring_methodology": f"Base {ScoringConfig.BASE_SCORE} + Weighted Certification Bonuses + Multi-Cert Bonus (capped at 10.0)",
        "scoring_priority": "Brand Synonyms → Parent Company → Dynamic Calculation",
        "scoring_consistency": "Single scoring function ensures identical results across all search methods",
        "certification_bonuses": ScoringConfig.CERTIFICATION_BONUSES,
        "multi_cert_bonus": ScoringConfig.MULTI_CERT_BONUS,
        "excel_file": FileConfig.CERTIFICATION_EXCEL_FILE,
        "excel_file_status": excel_status,
        "excel_file_size": (
            os.path.getsize(FileConfig.CERTIFICATION_EXCEL_FILE) if excel_exists else 0
        ),
        "excel_data_loaded": certification_manager.data is not None,
        "excel_records": (
            len(certification_manager.data) if certification_manager.data else 0
        ),
        "create_excel_script": FileConfig.CREATE_EXCEL_SCRIPT,
        "create_excel_script_status": script_status,
        "create_excel_script_size": (
            os.path.getsize(FileConfig.CREATE_EXCEL_SCRIPT) if script_exists else 0
        ),
        "brand_extraction_enhanced": True,
        "parent_company_mappings": len(BrandNormalizer.PARENT_COMPANY_MAPPING),
        "brand_aliases": len(BrandNormalizer.BRAND_ALIASES),
        "brand_synonyms": len(BrandNormalizer.BRAND_SYNONYMS),
        "scoring_methodology_endpoint": "/scoring-methodology (HTML)",
        "version": "2.4.0",
        "message": "TBL Grocery Scanner API with Consistent Scoring Across All Search Methods (Excel-only data)",
        # 🔥 NEW Redis fields
        "redis_status": redis_status,
        "redis_url_set": redis_url_set,
        "redis_connected": redis_client is not None
    }


# ==================== ADD THIS: Root endpoint to serve frontend =========


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the frontend HTML file"""
    try:
        # Read the index.html file
        with open("index.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content, status_code=200)
    except FileNotFoundError:
        # If index.html doesn't exist, serve a basic page with instructions from file
        try:
            with open("backend_status.html", "r", encoding="utf-8") as f:
                html_content = f.read()
            return HTMLResponse(content=html_content, status_code=200)
        except FileNotFoundError:
            # Ultimate fallback - minimal HTML
            return HTMLResponse(
                content="<h1>Backend Running</h1><p>Place index.html in the directory</p>",
                status_code=200
            )


# ==================== ALSO ADD THIS: Favicon endpoint ====================


@app.get("/favicon.ico")
async def favicon():
    """Serve favicon (return empty to avoid 404 errors)"""
    # Return a minimal transparent PNG to avoid 404 errors
    from fastapi.responses import Response
    import base64

    # A 1x1 transparent PNG
    transparent_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )

    return Response(content=transparent_png, media_type="image/png")

@app.post("/cache/clear")
async def clear_cache():
    """Clear all caches - useful after updating Excel data"""
    global SEARCH_CACHE, OFF_CACHE, BRAND_EXTRACTION_CACHE
    search_size = len(SEARCH_CACHE)
    off_size = len(OFF_CACHE)
    brand_extraction_size = len(BRAND_EXTRACTION_CACHE)
    SEARCH_CACHE = {}
    OFF_CACHE = {}
    BRAND_EXTRACTION_CACHE = {}
    logger.info(f"🧹 Caches cleared (removed {search_size} search, {off_size} OFF, {brand_extraction_size} brand extraction entries)")
    return {
        "status": "success",
        "message": "All caches cleared",
        "search_cache_cleared": search_size,
        "off_cache_cleared": off_size,
        "brand_extraction_cache_cleared": brand_extraction_size
    }

@app.get("/cache/stats")
async def cache_stats():
    """Get all cache statistics including Redis"""
    # Get Redis info if available
    redis_info = {}
    if redis_client:
        try:
            # Get Redis stats
            keys = redis_client.keys("product:*")
            redis_info = {
                "connected": True,
                "keys_count": len(keys),
                "sample_keys": [k.decode('utf-8') for k in keys[:5]] if keys else []
            }
        except Exception as e:
            redis_info = {"connected": False, "error": str(e)}
    else:
        redis_info = {"connected": False, "reason": "REDIS_URL not set or connection failed"}

    return {
        "search_cache_size": len(SEARCH_CACHE),
        "search_cache_keys_sample": list(SEARCH_CACHE.keys())[:10] if SEARCH_CACHE else [],
        "off_cache_size": len(OFF_CACHE),
        "off_cache_keys_sample": list(OFF_CACHE.keys())[:10] if OFF_CACHE else [],
        "brand_extraction_cache_size": len(BRAND_EXTRACTION_CACHE),
        "brand_extraction_keys_sample": list(BRAND_EXTRACTION_CACHE.keys())[:10] if BRAND_EXTRACTION_CACHE else [],
        "redis": redis_info
    }

# ==================== STARTUP EVENT ====================
# ✅ ADD THIS: Load data before accepting requests

@app.on_event("startup")
async def startup_event():
    """Initialize Redis and load certification data on startup"""
    logger.info("🚀 STARTUP_EVENT IS RUNNING!")
    logger.info("🚀 Application starting up...")
    logger.info("📊 Loading certification data...")

    # 🔥 Initialize Redis connection
    global redis_client
    try:
        redis_url = os.getenv('REDIS_URL')
        if redis_url:
            # Log the URL (without password)
            safe_url = redis_url.split(':')[0] + '://' + redis_url.split('@')[-1] if '@' in redis_url else redis_url
            logger.info(f"🔗 Connecting to Redis: {safe_url}")
            redis_client = redis.Redis.from_url(redis_url)
            redis_client.ping()
            logger.info("✅ Redis connection established successfully")
        else:
            logger.info("ℹ️ REDIS_URL not set, using in-memory cache only")
            redis_client = None
    except Exception as e:
        logger.error(f"❌ Redis connection failed: {e}")
        logger.warning("⚠️ Using in-memory cache only")
        redis_client = None

    # Load certification data with retry
    max_retries = 5
    for attempt in range(max_retries):
        success = certification_manager.load_certification_data()
        if success and certification_manager.data is not None:
            logger.info(f"✅ Successfully loaded {len(certification_manager.data)} certification records")
            logger.info(f"✅ Category index ready for {len(certification_manager.brand_categories)} brands")
            logger.info("🚀 Application startup complete!")
            return
        else:
            logger.warning(f"⚠️ Attempt {attempt + 1}/{max_retries} failed, retrying in 1 second...")
            time.sleep(1)

    logger.warning("⚠️ Certification data did not load on startup. Will retry on first request.")
    logger.info("🚀 Application startup complete!")

if __name__ == "__main__":

    # Check if Excel data is loaded
    if certification_manager.data is not None:
        logger.info(
            f"Excel certification database loaded with {len(certification_manager.data)} brands"
        )
    else:
        logger.info("Excel certification database not yet loaded (will load on first request)")
    logger.info(
        "Scoring Consistency: Weighted certification bonuses with cap at 10.0"
    )
    logger.info("Multi-certification bonus always applied correctly")
    logger.info(
        f"Certification Bonuses: {ScoringConfig.CERTIFICATION_BONUSES}")
    logger.info(
        f"Multi-certification bonus: {ScoringConfig.MULTI_CERT_BONUS} per additional cert"
    )

    logger.info(
        f"Parent company mappings: {len(BrandNormalizer.PARENT_COMPANY_MAPPING)}"
    )
    logger.info(f"Brand aliases: {len(BrandNormalizer.BRAND_ALIASES)}")
    logger.info(f"Brand synonyms: {len(BrandNormalizer.BRAND_SYNONYMS)}")

    # Test some product mappings
    test_products = [
        "Cheerios",
        "Oreo Cookies",
        "Pringles Chips",
        "Dove Chocolate",
        "Tide Detergent",
    ]
    for product in test_products:
        parent = BrandNormalizer.find_parent_company(product)
        if parent:
            logger.info(f"Test mapping: '{product}' → '{parent}'")

    logger.info("🎯 Scanner System: Html5Qrcode integrated")
    logger.info("🌐 Open http://localhost:8000 in your browser")
    logger.info(
        "📱 For mobile: Use your computer's IP address with port 8000")
    logger.info(
        "🔧 Key endpoint: GET /scoring-methodology for complete transparency")
    logger.info("📊 Scanner health: GET /scanner/health")

    # Only run uvicorn directly when executing the script locally
    # This block WON'T run when gunicorn imports the module
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
