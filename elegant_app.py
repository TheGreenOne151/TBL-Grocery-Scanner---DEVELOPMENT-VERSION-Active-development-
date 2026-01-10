import os
PORT = int(os.getenv("PORT", 8000))

from urllib.parse import quote
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Set, ClassVar
from datetime import datetime, timedelta
# REMOVED: import bcrypt           # Will load inside functions
import httpx
from difflib import SequenceMatcher
import asyncio
import logging
import json
# REMOVED: import pandas as pd     # Will load inside functions
from io import BytesIO
import re
from collections import Counter
from contextlib import redirect_stdout, redirect_stderr
import io
import importlib.util

from fastapi import FastAPI, Query, Request, HTTPException, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

# ADD THIS HELPER FUNCTION
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
        "B Corp": {"social": 1.0, "environmental": 1.0, "economic": 1.0},
        "Fair Trade": {"social": 1.0, "environmental": 0.5, "economic": 0.5},
        "Rainforest Alliance": {"social": 0.5, "environmental": 1.0, "economic": 0.5},
        "Leaping Bunny": {"social": 1.0, "environmental": 0.5, "economic": 0.0},
    }
    GRADE_THRESHOLDS: ClassVar[Dict[str, float]] = {
        "EXCELLENT": 8.5,
        "GREAT": 7.0,
        "GOOD": 5.0,
        "POOR": 0.0
    }

@dataclass
class FileConfig:
    """Configuration for file paths"""
    CERTIFICATION_EXCEL_FILE: ClassVar[str] = "certifications.xlsx"
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
    social: float
    environmental: float
    economic: float
    certifications: List[str]
    scoring_method: str = "dynamic_calculation"
    multi_cert_applied: bool = False
    multi_cert_bonus: float = 0.0
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "social": round(self.social, 2),
            "environmental": round(self.environmental, 2),
            "economic": round(self.economic, 2),
            "certifications": self.certifications,
            "scoring_method": self.scoring_method,
            "multi_cert_applied": self.multi_cert_applied,
            "multi_cert_bonus": self.multi_cert_bonus,
            "notes": self.notes
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
        logger.debug(f"Executing {func.__name__} with args={args}, kwargs={kwargs}")
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

def calculate_overall_score(social: float, environmental: float, economic: float) -> Dict[str, Any]:
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

    return {
        "overall_score": round(overall, 2),
        "grade": grade
    }

# ==================== FASTAPI APP ====================

app = FastAPI(title="TBL Grocery Scanner", version="2.3.0")

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

    @field_validator('password')
    @classmethod
    def validate_password(cls, password: str) -> str:
        if len(password) < 6:
            raise ValueError('Password must be at least 6 characters')
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

    @field_validator('brand', 'product_name', 'category')
    @classmethod
    def validate_fields(cls, value: str, info) -> str:
        field_name = info.field_name

        if not value or value.strip() == "":
            return {
                "brand": "Unknown",
                "product_name": "Generic Product",
                "category": "General"
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

    @field_validator('social', 'environmental', 'economic')
    @classmethod
    def validate_scores(cls, score: float) -> float:
        if not 0 <= score <= 10:
            raise ValueError('Scores must be between 0 and 10')
        return score

class ProductSearch(BaseModel):
    product_name: str
    max_results: int = 10

# ==================== BRAND NORMALIZER ====================

class BrandNormalizer:
    """Encapsulate all brand normalization logic"""

    # Known national brands for prioritization
    NATIONAL_BRANDS: ClassVar[Set[str]] = {
        "coca cola", "pepsi", "nestle", "kraft", "heinz", "unilever", "procter gamble",
        "general mills", "kelloggs", "campbells", "hershey", "mars", "mondelez",
        "danone", "coca-cola", "pepsico", "johnson johnson", "kimberly clark",
        "colgate palmolive", "p g", "kellogg", "general electric", "dannon",
        "quaker", "conagra", "tyson", "smithfield", "hormel", "jbs", "perdue",
        "cargill", "adm", "bunge", "land olakes", "dairy farmers of america",
        "dean foods", "saputo", "frontera", "chobani", "stonyfield", "organic valley",
        "horizon organic", "lifeway", "kefir", "yoplait", "activia", "siggi",
        "noosa", "liberte", "brown cow", "wallaby", "alexander", "maple hill",
        "clover organic", "straus", "berkley", "jensen", "organic meadows"
    }

    # Store brands (to deprioritize)
    STORE_BRANDS: ClassVar[Set[str]] = {
        "great value", "kirkland signature", "market pantry", "up up", "equate",
        "good gather", "good good", "simply nature", "open nature", "whole foods",
        "trader joes", "365 everyday value", "365", "aldi", "happy farms",
        "friendly farms", "burmans", "benton", "bakers corner", "clancy",
        "friendly", "specially selected", "simply", "private selection",
        "everyday essentials", "essentials", "value", "store brand", "generic",
        "private label", "house brand", "own brand"
    }

    # Parent company mapping for brand identification ONLY
    PARENT_COMPANY_MAPPING: ClassVar[Dict[str, str]] = {
        # General Mills products
        "cheerios": "general mills", "chex": "general mills", "lucky charms": "general mills",
        "cocoa puffs": "general mills", "trix": "general mills", "reeses puffs": "general mills",
        "cinnamon toast crunch": "general mills", "gold medal": "general mills",
        "betty crocker": "general mills", "pillsbury": "general mills", "haagen dazs": "general mills",
        "yoplait": "general mills", "totinos": "general mills", "annanies": "general mills",
        "progresso": "general mills", "green giant": "general mills", "old el paso": "general mills",
        "fibre one": "general mills", "nature valley": "general mills",

        # Kellogg's products
        "frosted flakes": "kelloggs", "corn flakes": "kelloggs", "special k": "kelloggs",
        "raisin bran": "kelloggs", "rice krispies": "kelloggs", "fruit loops": "kelloggs",
        "apple jacks": "kelloggs", "cocoa krispies": "kelloggs", "pop tarts": "kelloggs",
        "egg": "kelloggs", "nutri grain": "kelloggs", "morningstar farms": "kelloggs",
        "veggie": "kelloggs",

        # Mondelez products
        "oreo": "mondelez", "chips ahoy": "mondelez", "ritz": "mondelez",
        "wheat thins": "mondelez", "triscuit": "mondelez", "belvita": "mondelez",
        "halloween": "mondelez", "milka": "mondelez", "cadbury": "mondelez",
        "toblerone": "mondelez", "sour patch kids": "mondelez", "tang": "mondelez",

        # PepsiCo products
        "lays": "pepsico", "doritos": "pepsico", "cheetos": "pepsico",
        "fritos": "pepsico", "tostitos": "pepsico", "ruffles": "pepsico",
        "sun chips": "pepsico", "quaker": "pepsico", "tropicana": "pepsico",
        "gatorade": "pepsico", "mountain dew": "pepsico", "pepsi": "pepsico",
        "7up": "pepsico", "aquafina": "pepsico", "lipton": "pepsico", "brisk": "pepsico",

        # Coca-Cola products
        "coca cola": "coca cola", "coke": "coca cola", "diet coke": "coca cola",
        "sprite": "coca cola", "fanta": "coca cola", "minute maid": "coca cola",
        "powerade": "coca cola", "dasani": "coca cola", "smartwater": "coca cola",
        "fairlife": "coca cola",

        # Nestlé products
        "nescafe": "nestle", "nesquik": "nestle", "stouffers": "nestle",
        "lean cuisine": "nestle", "digiorno": "nestle", "tombstone": "nestle",
        "butterfinger": "nestle", "baby ruth": "nestle", "100 grand": "nestle",
        "raisinets": "nestle", "sno caps": "nestle", "wonka": "nestle",
        "purina": "nestle", "friskies": "nestle",

        # Unilever products
        "dove": "unilever", "axe": "unilever", "rexona": "unilever",
        "vaseline": "unilever", "lipton": "unilever", "ben jerrys": "unilever",
        "magnum": "unilever", "breyers": "unilever", "klondike": "unilever",
        "hellmanns": "unilever", "best foods": "unilever", "knorr": "unilever",

        # Kraft Heinz products
        "kraft": "kraft heinz", "heinz": "kraft heinz", "oscar mayer": "kraft heinz",
        "philadelphia": "kraft heinz", "velveeta": "kraft heinz", "cool whip": "kraft heinz",
        "jell o": "kraft heinz", "kool aid": "kraft heinz", "capri sun": "kraft heinz",
        "lunchables": "kraft heinz",

        # Mars products
        "mms": "mars", "snickers": "mars", "twix": "mars", "milky way": "mars",
        "skittles": "mars", "starburst": "mars", "orbit": "mars", "extra": "mars",
        "dove chocolate": "mars", "pedigree": "mars", "whiskas": "mars", "royal canin": "mars",

        # Procter & Gamble products
        "tide": "procter gamble", "pampers": "procter gamble", "gillette": "procter gamble",
        "oral b": "procter gamble", "crest": "procter gamble", "head shoulders": "procter gamble",
        "olay": "procter gamble", "pantene": "procter gamble", "downy": "procter gamble",
        "bounty": "procter gamble", "charmin": "procter gamble", "puffs": "procter gamble",
        "vicks": "procter gamble", "metamucil": "procter gamble",

        # Johnson & Johnson products
        "band aid": "johnson johnson", "tylenol": "johnson johnson", "motrin": "johnson johnson",
        "benadryl": "johnson johnson", "zyrtec": "johnson johnson", "neutrogena": "johnson johnson",
        "aveeno": "johnson johnson", "listerine": "johnson johnson", "reach": "johnson johnson",
        "splenda": "johnson johnson",

        # Campbell Soup products
        "campbells": "campbell soup", "prego": "campbell soup", "pepperidge farm": "campbell soup",
        "v8": "campbell soup", "swanson": "campbell soup", "pace": "campbell soup",
        "snyder of hanover": "campbell soup",

        # Conagra Brands products
        "healthy choice": "conagra", "chef boyardee": "conagra", "hunt": "conagra",
        "pam": "conagra", "reddi wip": "conagra", "duncan hines": "conagra",
        "slim jim": "conagra", "egg beater": "conagra",

        # Tyson Foods products
        "tyson": "tyson foods", "jimmy dean": "tyson foods", "hillshire farm": "tyson foods",
        "ball park": "tyson foods", "sara lee": "tyson foods", "state fair": "tyson foods",

        # Hormel products
        "spam": "hormel", "jennie o": "hormel", "applegate": "hormel",
        "wholly guacamole": "hormel", "herdez": "hormel", "skipper": "hormel",

        # Danone products
        "dannon": "danone", "oikos": "danone", "activia": "danone",
        "international delight": "danone", "silk": "danone", "so delicious": "danone",
        "vega": "danone",

        # Other common mappings
        "hershey": "hershey", "reese": "hershey", "kitkat": "hershey",
        "jolly rancher": "hershey", "ice breaker": "hershey", "barkthins": "hershey",

        "starbucks": "starbucks", "seattle best": "starbucks", "teavana": "starbucks",
        "evolution fresh": "starbucks",

        "cholula": "cholula", "frank redhot": "mccormick", "french": "mccormick",
        "old bay": "mccormick",

        "goya": "goya", "badia": "badia",
    }

    # Common brand abbreviations and aliases
    BRAND_ALIASES: ClassVar[Dict[str, str]] = {
        "gm": "general mills", "p&g": "procter gamble", "pg": "procter gamble",
        "j&j": "johnson johnson", "jj": "johnson johnson", "k": "kelloggs",
        "kmart": "kmart", "walmart": "walmart", "target": "target",
        "costco": "costco", "sams": "sams club", "aldi": "aldi",
        "trader joes": "trader joes", "whole foods": "whole foods",
        "tjs": "trader joes", "wf": "whole foods",
    }

    # Common brand name variations
    BRAND_VARIATIONS: ClassVar[Dict[str, List[str]]] = {
        "general mills": ["gm", "general mills inc", "generalmills", "g mills"],
        "kelloggs": ["kellogg", "kellogg company", "kellogg's"],
        "mondelez": ["mondelez international", "kraft foods"],
        "pepsico": ["pepsi", "pepsi co", "pepsico inc"],
        "coca cola": ["coca-cola", "coke", "coca cola company"],
        "nestle": ["nestlé", "nestle sa"],
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
        "starbucks": ["starbucks coffee"],
    }

    # Brand synonyms for matching
    BRAND_SYNONYMS: ClassVar[Dict[str, str]] = {
        "generalmills": "general mills", "g mills": "general mills",
        "gm": "general mills", "kellogg": "kelloggs", "kelloggs": "kelloggs",
        "kraft": "kraft heinz", "heinz": "kraft heinz", "p g": "procter gamble",
        "p&g": "procter gamble", "procter": "procter gamble",
        "johnson": "johnson johnson", "campbell": "campbell soup",
        "campbells": "campbell soup", "tyson": "tyson foods",
        "dannon": "danone", "hersheys": "hershey",
        "starbucks coffee": "starbucks",
    }

    # Hardcoded scores database
    HARDCODED_SCORES_DB: ClassVar[Dict[str, Dict[str, Any]]] = {
        "nespresso": {
            "social": 8.5, "environmental": 8.5, "economic": 8.0,
            "certifications": ["B Corp", "Fair Trade", "Rainforest Alliance"],
            "multi_cert_applied": True, "multi_cert_bonus": 1.0
        },
        "ben jerrys": {
            "social": 7.5, "environmental": 7.0, "economic": 7.0,
            "certifications": ["B Corp", "Fair Trade"],
            "multi_cert_applied": True, "multi_cert_bonus": 0.5
        },
        "evian": {
            "social": 6.0, "environmental": 6.0, "economic": 6.0,
            "certifications": ["B Corp"], "multi_cert_applied": False
        },
        "volvic": {
            "social": 6.0, "environmental": 6.0, "economic": 6.0,
            "certifications": ["B Corp"], "multi_cert_applied": False
        },
        "dannon": {
            "social": 6.0, "environmental": 6.0, "economic": 6.0,
            "certifications": ["B Corp"], "multi_cert_applied": False
        },
        "activia": {
            "social": 6.0, "environmental": 6.0, "economic": 6.0,
            "certifications": ["B Corp"], "multi_cert_applied": False
        },
        "oikos": {
            "social": 6.0, "environmental": 6.0, "economic": 6.0,
            "certifications": ["B Corp"], "multi_cert_applied": False
        },
        "starbucks": {
            "social": 6.0, "environmental": 5.5, "economic": 5.5,
            "certifications": ["Fair Trade"], "multi_cert_applied": False
        },
        "cadbury": {
            "social": 6.0, "environmental": 5.5, "economic": 5.5,
            "certifications": ["Fair Trade"], "multi_cert_applied": False
        },
        "dunkin": {
            "social": 7.0, "environmental": 6.5, "economic": 6.5,
            "certifications": ["Fair Trade", "Rainforest Alliance"],
            "multi_cert_applied": True, "multi_cert_bonus": 0.5
        },
        "365 everyday value": {
            "social": 8.0, "environmental": 7.5, "economic": 7.0,
            "certifications": ["Fair Trade", "Rainforest Alliance", "Leaping Bunny"],
            "multi_cert_applied": True, "multi_cert_bonus": 1.0
        },
        "coca cola": {
            "social": 5.5, "environmental": 6.0, "economic": 5.5,
            "certifications": ["Rainforest Alliance"], "multi_cert_applied": False
        },
        "hersheys": {
            "social": 5.5, "environmental": 6.0, "economic": 5.5,
            "certifications": ["Rainforest Alliance"], "multi_cert_applied": False
        },
        "lipton": {
            "social": 5.5, "environmental": 6.0, "economic": 5.5,
            "certifications": ["Rainforest Alliance"], "multi_cert_applied": False
        },
        "magnum": {
            "social": 5.5, "environmental": 6.0, "economic": 5.5,
            "certifications": ["Rainforest Alliance"], "multi_cert_applied": False
        },
        "nestle": {
            "social": 5.5, "environmental": 6.0, "economic": 5.5,
            "certifications": ["Rainforest Alliance"], "multi_cert_applied": False
        },
        "dove": {
            "social": 6.0, "environmental": 5.5, "economic": 5.0,
            "certifications": ["Leaping Bunny"], "multi_cert_applied": False
        },
        "general mills": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "kelloggs": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "pepsico": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "mondelez": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "kraft heinz": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "unilever": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "procter gamble": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "johnson johnson": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "mars": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "danone": {
            "social": 6.0, "environmental": 6.0, "economic": 6.0,
            "certifications": ["B Corp"], "multi_cert_applied": False
        },
        "hershey": {
            "social": 5.5, "environmental": 6.0, "economic": 5.5,
            "certifications": ["Rainforest Alliance"], "multi_cert_applied": False
        },
        "campbell soup": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "conagra": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "tyson foods": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "hormel": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "aquafina": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "colgate palmolive": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "gerber": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        },
        "hellmanns": {
            "social": 5.0, "environmental": 5.0, "economic": 5.0,
            "certifications": [], "multi_cert_applied": False
        }
    }

    # Brand identification database
    BRAND_IDENTIFICATION_DB: ClassVar[Dict[str, Dict[str, Any]]] = {
        "365 everyday value": {"certifications": ["Fair Trade", "Rainforest Alliance", "Leaping Bunny"]},
        "activia": {"certifications": ["B Corp"]},
        "annies homegrown": {"certifications": []},
        "aquafina": {"certifications": []},
        "banquet": {"certifications": []},
        "ben jerrys": {"certifications": ["B Corp", "Fair Trade"]},
        "bens original": {"certifications": []},
        "best foods": {"certifications": []},
        "betty crocker": {"certifications": []},
        "birds eye": {"certifications": []},
        "bisquick": {"certifications": []},
        "blue buffalo": {"certifications": []},
        "breyers": {"certifications": []},
        "butterfinger": {"certifications": []},
        "cadbury": {"certifications": ["Fair Trade"]},
        "campbells": {"certifications": []},
        "capri sun": {"certifications": []},
        "cheerios": {"certifications": []},
        "cheetos": {"certifications": []},
        "cheez it": {"certifications": []},
        "chex": {"certifications": []},
        "chips ahoy": {"certifications": []},
        "coca cola": {"certifications": ["Rainforest Alliance"]},
        "colgate palmolive": {"certifications": []},
        "corn flakes": {"certifications": []},
        "crunch": {"certifications": []},
        "dannon": {"certifications": ["B Corp"]},
        "dasani": {"certifications": []},
        "dentyne": {"certifications": []},
        "digiorno": {"certifications": []},
        "doritos": {"certifications": []},
        "dove": {"certifications": ["Leaping Bunny"]},
        "duncan hines": {"certifications": []},
        "dunkin": {"certifications": ["Fair Trade", "Rainforest Alliance"]},
        "eggo": {"certifications": []},
        "evian": {"certifications": ["B Corp"]},
        "fanta": {"certifications": []},
        "fiber one": {"certifications": []},
        "fritos": {"certifications": []},
        "froot loops": {"certifications": []},
        "frosted flakes": {"certifications": []},
        "gatorade": {"certifications": []},
        "general electric": {"certifications": []},
        "gerber": {"certifications": []},
        "go gurt": {"certifications": []},
        "goldfish": {"certifications": []},
        "good gather": {"certifications": []},
        "great value": {"certifications": []},
        "grey poupon": {"certifications": []},
        "haagen dazs": {"certifications": []},
        "healthy choice": {"certifications": []},
        "heinz": {"certifications": []},
        "hellmanns": {"certifications": []},
        "hersheys": {"certifications": ["Rainforest Alliance"]},
        "hormel": {"certifications": []},
        "hot pockets": {"certifications": []},
        "international delight": {"certifications": []},
        "jimmy dean": {"certifications": []},
        "johnson johnson": {"certifications": []},
        "jolly rancher": {"certifications": []},
        "keebler": {"certifications": []},
        "kelloggs": {"certifications": []},
        "kirkland signature": {"certifications": []},
        "kitkat": {"certifications": []},
        "knorr": {"certifications": []},
        "kool aid": {"certifications": []},
        "kraft": {"certifications": []},
        "lays": {"certifications": []},
        "lipton": {"certifications": ["Rainforest Alliance"]},
        "lucky charms": {"certifications": []},
        "lunchables": {"certifications": []},
        "mms": {"certifications": []},
        "magnum": {"certifications": ["Rainforest Alliance"]},
        "marie callenders": {"certifications": []},
        "milky way": {"certifications": []},
        "minute maid": {"certifications": []},
        "morningstar farms": {"certifications": []},
        "mountain dew": {"certifications": []},
        "nature valley": {"certifications": []},
        "nescafe": {"certifications": []},
        "nespresso": {"certifications": ["B Corp", "Fair Trade", "Rainforest Alliance"]},
        "nestle": {"certifications": ["Rainforest Alliance"]},
        "nutri grain": {"certifications": []},
        "oikos": {"certifications": ["B Corp"]},
        "oreo": {"certifications": []},
        "oscar mayer": {"certifications": []},
        "pedigree": {"certifications": []},
        "pepperidge farm": {"certifications": []},
        "pepsi": {"certifications": []},
        "perdue": {"certifications": []},
        "philadelphia cream cheese": {"certifications": []},
        "pillsbury": {"certifications": []},
        "planters": {"certifications": []},
        "poland spring": {"certifications": []},
        "pop tarts": {"certifications": []},
        "prego": {"certifications": []},
        "pringles": {"certifications": []},
        "procter gamble": {"certifications": []},
        "purina": {"certifications": []},
        "quaker oats": {"certifications": []},
        "reddi wip": {"certifications": []},
        "reeses": {"certifications": []},
        "ritz": {"certifications": []},
        "ruffles": {"certifications": []},
        "simply orange": {"certifications": []},
        "skittles": {"certifications": []},
        "slim jim": {"certifications": []},
        "smart water": {"certifications": []},
        "smithfield": {"certifications": []},
        "snickers": {"certifications": []},
        "sour patch kids": {"certifications": []},
        "special k": {"certifications": []},
        "sprite": {"certifications": []},
        "starbucks": {"certifications": ["Fair Trade"]},
        "starburst": {"certifications": []},
        "stouffers": {"certifications": []},
        "sunchips": {"certifications": []},
        "swanson": {"certifications": []},
        "toblerone": {"certifications": []},
        "tostitos": {"certifications": []},
        "trident": {"certifications": []},
        "trix": {"certifications": []},
        "tropicana": {"certifications": []},
        "twix": {"certifications": []},
        "tyson": {"certifications": []},
        "uncle bens": {"certifications": []},
        "v8": {"certifications": []},
        "velveeta": {"certifications": []},
        "vitaminwater": {"certifications": []},
        "volvic": {"certifications": ["B Corp"]},
        "whiskas": {"certifications": []},
        "yoplait": {"certifications": []},
    }

    @classmethod
    @cache_result
    def normalize(cls, brand: str) -> str:
        """Enhanced brand name normalization with better handling of variations"""
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

        # Replace common symbols and special characters
        replacements = {
            "'": "", "&": "and", "+": "and", ".": "", ",": "",
            "-": " ", "_": " ", ";": " ", ":": " ", "!": "",
            "?": "", "@": "", "#": "", "$": "", "%": "",
            "^": "", "*": "", "=": "", "~": ""
        }

        for old, new in replacements.items():
            normalized = normalized.replace(old, new)

        # Handle brand aliases
        for alias, canonical in cls.BRAND_ALIASES.items():
            if alias == normalized or f" {alias} " in f" {normalized} ":
                normalized = normalized.replace(alias, canonical)

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
        """Find parent company for a product using product name matching"""
        if not product_name:
            return None

        product_normalized = cls.normalize(product_name)

        # Check for exact product matches in parent company mapping
        for product_key, parent in cls.PARENT_COMPANY_MAPPING.items():
            if product_key in product_normalized:
                logger.info(f"Found parent company for '{product_name}': {parent} (via product key: {product_key})")
                return parent

        # Check for partial matches
        for product_key, parent in cls.PARENT_COMPANY_MAPPING.items():
            product_key_parts = product_key.split()
            product_parts = product_normalized.split()

            # Check if any significant word matches
            for key_part in product_key_parts:
                if len(key_part) > 3:  # Only consider significant words
                    for product_part in product_parts:
                        if len(product_part) > 3 and key_part in product_part:
                            logger.info(f"Found partial match for '{product_name}': {parent} (via '{key_part}' in '{product_part}')")
                            return parent

        return None

    @classmethod
    def extract_brand_from_product_text(cls, product_name: str) -> Optional[str]:
        """Enhanced brand extraction from product name using multiple strategies"""
        if not product_name or product_name.lower() in ["unknown", "generic product"]:
            return None

        product_lower = product_name.lower()

        # Strategy 1: Look for parent company mapping
        parent_company = cls.find_parent_company(product_name)
        if parent_company:
            return parent_company.title()

        # Strategy 2: Check for known brand patterns in product name
        for brand in cls.BRAND_IDENTIFICATION_DB.keys():
            brand_normalized = cls.normalize(brand)
            if brand_normalized and len(brand_normalized) > 2:
                # Check if brand appears in product name
                if brand_normalized in product_lower:
                    logger.info(f"Found brand '{brand}' directly in product name '{product_name}'")
                    return brand.title()

                # Check for brand variations
                if brand in cls.BRAND_VARIATIONS:
                    for variation in cls.BRAND_VARIATIONS[brand]:
                        if variation in product_lower:
                            logger.info(f"Found brand variation '{variation}' for '{brand}' in product name")
                            return brand.title()

        # Strategy 3: Extract likely brand from beginning of product name
        words = product_name.split()
        if len(words) > 1:
            first_word = cls.normalize(words[0])
            second_word = cls.normalize(words[1]) if len(words) > 1 else ""

            # Check first word as potential brand
            if first_word and len(first_word) > 2:
                for brand in cls.BRAND_IDENTIFICATION_DB.keys():
                    brand_normalized = cls.normalize(brand)
                    if brand_normalized == first_word or brand_normalized.startswith(first_word):
                        logger.info(f"Extracted brand '{brand}' from first word of product name")
                        return brand.title()

            # Check first two words as potential brand
            if second_word:
                first_two_words = f"{first_word} {second_word}"
                for brand in cls.BRAND_IDENTIFICATION_DB.keys():
                    brand_normalized = cls.normalize(brand)
                    if brand_normalized == first_two_words or brand_normalized.startswith(first_two_words):
                        logger.info(f"Extracted brand '{brand}' from first two words of product name")
                        return brand.title()

        return None

# ==================== CERTIFICATION MANAGER ====================

class CertificationManager:
    """Manage all certification-related operations"""

    def __init__(self):
        self.data = None
        self.last_loaded = None

    def load_certification_data(self) -> bool:
        """Load certification data from Excel file"""
        try:
            if os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE):
                logger.info(f"Loading certification data from {FileConfig.CERTIFICATION_EXCEL_FILE}")

                # First get pandas, then use it
                pd = get_pandas()
                df = pd.read_excel(FileConfig.CERTIFICATION_EXCEL_FILE)
                logger.info(f"Excel file loaded. Columns: {list(df.columns)}")

                cert_data = {}
                for _, row in df.iterrows():
                    # Try different possible column names for brand
                    brand = None
                    for possible_col in ['Brand', 'brand', 'Brand Name', 'brand_name', 'BRAND']:
                        if possible_col in df.columns:
                            brand = str(row.get(possible_col, '')).strip()
                            if brand:
                                break

                    if not brand:
                        continue

                    brand_normalized = BrandNormalizer.normalize(brand)

                    # Get certifications with flexible column name matching
                    certifications = self._extract_certifications(row, df.columns)

                    # Store brand data
                    cert_data[brand_normalized] = {
                        'original_brand': brand,
                        'certifications': certifications,
                        'row_data': row.to_dict()
                    }

                self.data = cert_data
                self.last_loaded = datetime.now()

                logger.info(f"Loaded {len(cert_data)} certification records")

                # Log some sample data for debugging
                sample_brands = list(cert_data.keys())[:3]
                for brand in sample_brands:
                    logger.info(f"Sample brand '{brand}': {cert_data[brand]['certifications']}")

                return True
            else:
                logger.warning(f"Certification Excel file {FileConfig.CERTIFICATION_EXCEL_FILE} not found")
                return False
        except Exception as e:
            logger.error(f"Error loading certification data: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _extract_certifications(self, row, columns) -> Dict[str, bool]:
        """Extract certifications from a row"""
        cert_mapping = {
            'b_corp': ['B Corp', 'b_corp', 'B Corp Certification', 'bcorp'],
            'fair_trade': ['Fair Trade', 'fair_trade', 'Fair Trade Certified', 'fairtrade'],
            'rainforest_alliance': ['Rainforest Alliance', 'rainforest_alliance', 'Rainforest Alliance Certified', 'rainforest'],
            'leaping_bunny': ['Leaping Bunny', 'leaping_bunny', 'Cruelty Free', 'leapingbunny']
        }

        # LAZY load pandas before using it
        pd = get_pandas()

        certifications = {}
        for cert_type, possible_names in cert_mapping.items():
            value = False
            for col_name in possible_names:
                if col_name in columns:
                    cell_value = row.get(col_name)
                    if pd.isna(cell_value):  # Now uses lazy-loaded pd
                        value = False
                    elif isinstance(cell_value, bool):
                        value = cell_value
                    elif isinstance(cell_value, (int, float)):
                        value = bool(cell_value)
                    elif isinstance(cell_value, str):
                        cell_value_lower = cell_value.strip().lower()
                        if cell_value_lower in ['true', 'yes', 'y', '1', 't']:
                            value = True
                        elif cell_value_lower in ['false', 'no', 'n', '0', 'f']:
                            value = False
                    break
            certifications[cert_type] = value

        return certifications

    # ==================== ADD THIS NEW METHOD HERE ====================

    @staticmethod
    def _improved_partial_match(search_brand: str, stored_brand: str) -> bool:
        """Improved brand matching with hybrid approach to prevent generic word mismatches"""
        # Generic words that shouldn't trigger matches alone
        GENERIC_WORDS = {
            "value", "brand", "store", "market", "everyday", "organic",
            "natural", "premium", "select", "choice", "essential", "basic",
            "original", "classic", "traditional", "regular", "quality",
            "fresh", "pure", "simple", "smart", "total", "complete"
        }

        # If one is substring of another (current behavior)
        if stored_brand in search_brand or search_brand in stored_brand:
            # Split into words
            search_words = set(search_brand.split())
            stored_words = set(stored_brand.split())
            common_words = search_words & stored_words

            # Remove generic words from consideration
            meaningful_common = [w for w in common_words if w not in GENERIC_WORDS]

            # Rule 1: At least 2 meaningful words match
            if len(meaningful_common) >= 2:
                return True

            # Rule 2: For single meaningful word match, require it to be significant
            if len(meaningful_common) == 1:
                word = next(iter(meaningful_common))
                # Word must be at least 4 chars and not too common
                if len(word) >= 4:
                    # Check if this is a known brand word from our databases
                    known_brand_words = {
                        "nespresso", "dannon", "activia", "oikos", "evian", "volvic",
                        "starbucks", "cadbury", "dunkin", "hershey", "coca", "cola",
                        "pepsi", "kraft", "heinz", "general", "mills", "kellogg",
                        "mondelez", "unilever", "procter", "gamble", "johnson",
                        "campbell", "tyson", "hormel", "danone", "nestle", "mars"
                    }
                    if word in known_brand_words:
                        return True

                    # For single-word brands, use similarity
                    if len(search_words) == 1 and len(stored_words) == 1:
                        from difflib import SequenceMatcher
                        similarity = SequenceMatcher(None, search_brand, stored_brand).ratio()
                        return similarity >= 0.8  # 80% similarity threshold

            # If we get here but had substring match, it was based on generic words only
            # Don't match based solely on generic words like "value"
            return False

        # Also check for word overlap (for cases like "ben jerry" vs "ben and jerry")
        search_words = set(search_brand.split())
        stored_words = set(stored_brand.split())
        common_words = search_words & stored_words
        meaningful_common = [w for w in common_words if w not in GENERIC_WORDS]

        # Rule 3: At least 2 meaningful words overlap
        if len(meaningful_common) >= 2:
            return True

        # ==================== NEW RULE 3A: Fuzzy word matching ====================

        # For cases like "ben jerry" vs "ben and jerrys" where we have 1 exact match
        # and the other words are similar
        if len(meaningful_common) == 1:
            # Get the remaining meaningful words (excluding generic words)
            search_remaining = [w for w in search_words if w not in GENERIC_WORDS and w not in meaningful_common]
            stored_remaining = [w for w in stored_words if w not in GENERIC_WORDS and w not in meaningful_common]

            # If we have one word remaining in each, check similarity
            if len(search_remaining) == 1 and len(stored_remaining) == 1:
                from difflib import SequenceMatcher
                word1 = search_remaining[0]
                word2 = stored_remaining[0]

                # Check if words are similar (allowing for small differences)
                similarity = SequenceMatcher(None, word1, word2).ratio()
                if similarity >= 0.7:  # 70% similarity for word variations
                    return True

            # Also check if one contains the other (e.g., "jerry" in "jerrys")
            if search_remaining and stored_remaining:
                for s_word in search_remaining:
                    for t_word in stored_remaining:
                        if s_word in t_word or t_word in s_word:
                            # Only allow if the contained part is significant
                            if len(s_word) >= 3 or len(t_word) >= 3:
                                return True

        # ==================== END OF NEW RULE ====================

        # Rule 4: Check if it's a known single-word brand with high similarity
        if len(search_words) == 1 and len(stored_words) == 1:
            from difflib import SequenceMatcher
            similarity = SequenceMatcher(None, search_brand, stored_brand).ratio()
            return similarity >= 0.8

        return False

    # ==================== END OF NEW METHOD ====================

    def get_certifications(self, brand: str) -> Dict[str, Any]:
        """Get certifications for a brand from Excel data"""
        # Reload data if never loaded or if more than 5 minutes old
        if (self.data is None or self.last_loaded is None or
            (datetime.now() - self.last_loaded).seconds > 300):
            logger.info("Reloading certification data...")
            self.load_certification_data()

        if not brand or brand.lower() in ["unknown", "n/a", ""]:
            logger.info("Empty brand requested, returning default certifications")
            return self._get_default_response()

        brand_normalized = BrandNormalizer.normalize(brand)
        logger.info(f"Looking up certifications for brand: '{brand}' (normalized: '{brand_normalized}')")

        # Check for exact match
        if brand_normalized in self.data:
            data = self.data[brand_normalized]
            logger.info(f"Found exact match for '{brand}': {data['certifications']}")
            return self._format_response(True, data)

        # Check for partial matches with improved logic
        for stored_brand, data in self.data.items():
            if self._improved_partial_match(brand_normalized, stored_brand):
                logger.info(f"Found improved partial match for '{brand}': stored as '{stored_brand}'")
                return self._format_response(True, data)

        # Check for brand variations with improved logic
        for stored_brand, data in self.data.items():
            if self._improved_partial_match(brand_normalized, stored_brand):
                logger.info(f"Found brand variation match for '{brand}': stored as '{stored_brand}'")
                return self._format_response(True, data)

        # No match found
        logger.info(f"No match found for brand: '{brand}'")
        return self._get_default_response()

    def _get_default_response(self) -> Dict[str, Any]:
        """Get default certification response"""
        return {
            "found": False,
            "certifications": {
                "b_corp": False,
                "fair_trade": False,
                "rainforest_alliance": False,
                "leaping_bunny": False
            },
            "details": None
        }

    def _format_response(self, found: bool, data: Dict) -> Dict[str, Any]:
        """Format certification response"""
        return {
            "found": found,
            "certifications": data['certifications'],
            "details": {
                "original_brand": data['original_brand'],
                "row_data": data.get('row_data', {})
            }
        }

# ==================== SCORING MANAGER ====================

class ScoringManager:
    """Manage all scoring-related operations"""

    @staticmethod
    def calculate_brand_scores(brand: str) -> BrandData:
        """
        Calculate scores for a brand using priority order:
        1. Hardcoded scores database (pre-calculated with multi-cert bonus)
        2. Brand synonyms mapping
        3. Parent company inheritance
        4. Dynamic calculation from certifications
        """
        # Handle empty/unknown brand
        if not brand or brand == "Unknown":
            return BrandData(
                social=ScoringConfig.BASE_SCORE,
                environmental=ScoringConfig.BASE_SCORE,
                economic=ScoringConfig.BASE_SCORE,
                certifications=[],
                scoring_method="base_score_only",
                notes="Base score of 5.0 (no brand identified)"
            )

        brand_normalized = BrandNormalizer.normalize(brand)

        # Step 1: Check hardcoded scores database FIRST
        if brand_normalized in BrandNormalizer.HARDCODED_SCORES_DB:
            scores = BrandNormalizer.HARDCODED_SCORES_DB[brand_normalized]
            logger.info(f"Using hardcoded scores for '{brand_normalized}'")
            return BrandData(
                social=scores["social"],
                environmental=scores["environmental"],
                economic=scores["economic"],
                certifications=scores.get("certifications", []),
                scoring_method="hardcoded_database",
                multi_cert_applied=scores.get("multi_cert_applied", False),
                multi_cert_bonus=scores.get("multi_cert_bonus", 0.0),
                notes="Pre-calculated score from hardcoded database (includes multi-cert bonus if applicable)"
            )

        # Step 2: Check brand synonyms
        if brand_normalized in BrandNormalizer.BRAND_SYNONYMS:
            synonym_brand = BrandNormalizer.BRAND_SYNONYMS[brand_normalized]
            if synonym_brand in BrandNormalizer.HARDCODED_SCORES_DB:
                scores = BrandNormalizer.HARDCODED_SCORES_DB[synonym_brand]
                logger.info(f"Using hardcoded scores via synonym for '{brand_normalized}' → '{synonym_brand}'")
                return BrandData(
                    social=scores["social"],
                    environmental=scores["environmental"],
                    economic=scores["economic"],
                    certifications=scores.get("certifications", []),
                    scoring_method="hardcoded_database_via_synonym",
                    multi_cert_applied=scores.get("multi_cert_applied", False),
                    multi_cert_bonus=scores.get("multi_cert_bonus", 0.0),
                    notes=f"Pre-calculated score via brand synonym '{synonym_brand}'"
                )

        # Step 3: Check if this is a product that should inherit parent company scores
        parent_company = BrandNormalizer.find_parent_company(brand)
        if parent_company:
            parent_normalized = BrandNormalizer.normalize(parent_company)
            if parent_normalized in BrandNormalizer.HARDCODED_SCORES_DB:
                scores = BrandNormalizer.HARDCODED_SCORES_DB[parent_normalized]
                logger.info(f"Using parent company scores for '{brand_normalized}' → parent '{parent_normalized}'")
                return BrandData(
                    social=scores["social"],
                    environmental=scores["environmental"],
                    economic=scores["economic"],
                    certifications=scores.get("certifications", []),
                    scoring_method="parent_company_inheritance",
                    multi_cert_applied=scores.get("multi_cert_applied", False),
                    multi_cert_bonus=scores.get("multi_cert_bonus", 0.0),
                    notes=f"Inherited score from parent company '{parent_company}'"
                )

        # Step 4: Dynamic calculation from certifications (fallback for unknown brands)
        logger.info(f"Brand '{brand_normalized}' not in hardcoded database, calculating dynamically")
        return ScoringManager._calculate_dynamic_scores(brand)

    @staticmethod
    def _calculate_dynamic_scores(brand: str) -> BrandData:
        """Calculate scores dynamically from certifications"""
        # Start with base score
        social_score = ScoringConfig.BASE_SCORE
        environmental_score = ScoringConfig.BASE_SCORE
        economic_score = ScoringConfig.BASE_SCORE

        # Get all certifications from combined sources
        all_certifications = ScoringManager._get_all_certifications(brand)

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
            multi_bonus = (len(all_certifications) - 1) * ScoringConfig.MULTI_CERT_BONUS
            social_score += multi_bonus
            environmental_score += multi_bonus
            economic_score += multi_bonus

        # Cap scores at 10.0
        social_score = min(10.0, social_score)
        environmental_score = min(10.0, environmental_score)
        economic_score = min(10.0, economic_score)

        return BrandData(
            social=social_score,
            environmental=environmental_score,
            economic=economic_score,
            certifications=all_certifications,
            scoring_method="dynamic_calculation",
            multi_cert_applied=bonus_applied and len(all_certifications) > 1,
            multi_cert_bonus=(len(all_certifications) - 1) * ScoringConfig.MULTI_CERT_BONUS
                            if bonus_applied and len(all_certifications) > 1 else 0.0,
            notes="Base 5.0 + certification bonuses + multi-cert bonus (calculated dynamically)"
        )

    @staticmethod
    def _get_all_certifications(brand: str) -> List[str]:
        """Get all certifications from combined sources"""
        brand_normalized = BrandNormalizer.normalize(brand)

        # Get certifications from Excel database
        excel_certs = certification_manager.get_certifications(brand)

        # Also check hardcoded identification database for certifications
        hardcoded_certs = []
        if brand_normalized in BrandNormalizer.BRAND_IDENTIFICATION_DB:
            hardcoded_certs = BrandNormalizer.BRAND_IDENTIFICATION_DB[brand_normalized].get("certifications", [])

        # Combine certifications from both sources
        excel_cert_list = []
        if excel_certs["certifications"]["b_corp"]:
            excel_cert_list.append("B Corp")
        if excel_certs["certifications"]["fair_trade"]:
            excel_cert_list.append("Fair Trade")
        if excel_certs["certifications"]["rainforest_alliance"]:
            excel_cert_list.append("Rainforest Alliance")
        if excel_certs["certifications"]["leaping_bunny"]:
            excel_cert_list.append("Leaping Bunny")

        # Combine all certifications, removing duplicates
        return list(set(hardcoded_certs + excel_cert_list))

# ==================== OPEN FOOD FACTS CLIENT ====================

class OpenFoodFactsClient:
    """Client for Open Food Facts API"""

    @staticmethod
    async def search_by_name(product_name: str, max_results: int = 20) -> Dict[str, Any]:
        """Enhanced search Open Food Facts by product name with better brand extraction"""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                encoded_name = quote(product_name)
                url = f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={encoded_name}&search_simple=1&action=process&json=1&page_size={max_results}"

                response = await client.get(
                    url,
                    headers={"User-Agent": "TBLGroceryScanner/1.0"}
                )

                if response.status_code == 200:
                    data = response.json()
                    products = data.get("products", [])

                    if not products:
                        return {
                            "found": False,
                            "message": "No products found",
                            "products": [],
                            "brand_analysis": {}
                        }

                    return OpenFoodFactsClient._analyze_products(products)
                else:
                    return {
                        "found": False,
                        "message": f"Open Food Facts API error: {response.status_code}",
                        "products": [],
                        "brand_analysis": {}
                    }
        except Exception as e:
            logger.error(f"Open Food Facts search error for '{product_name}': {e}")
            return {
                "found": False,
                "message": f"Search error: {str(e)}",
                "products": [],
                "brand_analysis": {}
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
                    if field_value and field_value.lower() not in ["", "unknown", "n/a", "none"]:
                        # Split by common separators
                        for separator in [",", ";", "/", "|", "&", "+"]:
                            if separator in field_value:
                                parts = [p.strip() for p in field_value.split(separator) if p.strip()]
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
                            field_value.split(",")[0] if "," in field_value else field_value
                        )
                        if normalized_brand not in brand_details:
                            brand_details[normalized_brand] = {
                                "original_brand": field_value,
                                "product_name": product.get("product_name", ""),
                                "product_id": product.get("code", ""),
                                "categories": product.get("categories", ""),
                                "countries": product.get("countries", ""),
                                "source_field": field
                            }
                        break

        return OpenFoodFactsClient._analyze_brand_candidates(brand_candidates, brand_details, products)

    @staticmethod
    def _analyze_brand_candidates(brand_candidates: List[str], brand_details: Dict, products: List[Dict]) -> Dict[str, Any]:
        """Analyze brand candidates to determine best match"""
        total_candidates = len(brand_candidates)

        if total_candidates == 0:
            return {
                "found": False,
                "message": "No brands found in search results",
                "products": products[:5],
                "brand_analysis": {}
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
        sorted_brands = sorted(brand_counts.items(), key=lambda x: x[1], reverse=True)
        top_brand = sorted_brands[0][0] if sorted_brands else None

        return {
            "found": True,
            "message": f"Found {len(products)} products",
            "products": products[:min(10, len(products))],
            "brand_analysis": {
                "total_products": len(products),
                "total_brand_candidates": total_candidates,
                "brand_counts": dict(sorted_brands[:10]),
                "brand_percentages": brand_percentages,
                "top_brand": top_brand,
                "brand_details": brand_details
            }
        }

    @staticmethod
    async def lookup_barcode(barcode: str) -> Dict[str, Any]:
        """Lookup product from Open Food Facts with comprehensive data extraction"""
        if barcode in PRODUCT_CACHE:
            return PRODUCT_CACHE[barcode]

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json",
                    headers={"User-Agent": "TBLGroceryScanner/1.0"}
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == 1:
                        product = data.get("product", {})
                        return OpenFoodFactsClient._extract_product_info(barcode, product)
        except Exception as e:
            logger.error(f"Open Food Facts lookup error for barcode {barcode}: {e}")

        return {"barcode": barcode, "found": False, "brand": "Unknown", "name": "Unknown", "category": "Unknown"}

    @staticmethod
    def _extract_product_info(barcode: str, product: Dict) -> Dict[str, Any]:
        """Extract product information from Open Food Facts data"""
        # Enhanced brand extraction
        brand = "Unknown"
        brand_fields = ["brands", "brand", "brand_owner", "manufacturer"]

        for field in brand_fields:
            if field in product and product[field]:
                brand_value = str(product[field]).strip()
                if brand_value and brand_value.lower() not in ["", "unknown", "n/a"]:
                    brand = brand_value.split(",")[0].strip() if "," in brand_value else brand_value
                    break

        # Try to extract brand from product name if still unknown
        if brand == "Unknown":
            name = product.get("product_name", "")
            if name:
                extracted = BrandNormalizer.extract_brand_from_product_text(name)
                if extracted:
                    brand = extracted

        # Extract product name
        name = product.get("product_name", "")
        if not name:
            name = product.get("product_name_en", product.get("product_name_fr", "Unknown"))

        # Extract category
        categories = product.get("categories", "")
        category = "Unknown"
        if categories:
            category_list = [c.strip() for c in categories.split(",") if c.strip()]
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
            "carbohydrates": product.get("nutriments", {}).get("carbohydrates_100g", None),
            "proteins": product.get("nutriments", {}).get("proteins_100g", None),
            "salt": product.get("nutriments", {}).get("salt_100g", None),
            "last_updated": product.get("last_modified_t")
        }

        PRODUCT_CACHE[barcode] = product_info
        return product_info

# ==================== BRAND EXTRACTION MANAGER ====================

class BrandExtractionManager:
    """Manager for brand extraction from product names"""

    @staticmethod
    async def extract_brand_from_product_name(product_name: str) -> Dict[str, Any]:
        """Main function to extract brand from product name using multiple strategies"""
        logger.info(f"Attempting to extract brand from product name: '{product_name}'")

        # Strategy 1: Direct brand name check
        result = BrandExtractionManager._check_direct_brand_match(product_name)
        if result["success"]:
            return result

        # Strategy 2: Try to extract brand directly from product name text
        direct_brand = BrandNormalizer.extract_brand_from_product_text(product_name)
        if direct_brand:
            logger.info(f"Direct extraction found brand: '{direct_brand}' from product name")
            return BrandExtractionManager._format_result(
                success=True,
                message=f"Brand '{direct_brand}' extracted directly from input",
                extracted_brand=direct_brand,
                confidence=85,
                method="direct_extraction",
                reason=f"Found '{direct_brand}' directly in input text"
            )

        # Strategy 3: Search Open Food Facts for product-like names
        words = product_name.split()
        if len(words) > 1:
            return await BrandExtractionManager._search_open_food_facts(product_name)

        # Strategy 4: Single word input - likely a brand name
        return await BrandExtractionManager._handle_single_word_input(product_name)

    @staticmethod
    def _check_direct_brand_match(product_name: str) -> Dict[str, Any]:
        """Check if input is already a known brand"""
        brand_normalized = BrandNormalizer.normalize(product_name)

        # Check if the input is already a known brand
        if brand_normalized in BrandNormalizer.BRAND_IDENTIFICATION_DB:
            logger.info(f"Input is already a known brand: '{brand_normalized}'")
            return BrandExtractionManager._format_result(
                success=True,
                message=f"Input recognized as brand: '{brand_normalized}'",
                extracted_brand=brand_normalized.title(),
                confidence=90,
                method="direct_brand_recognition",
                reason=f"'{brand_normalized}' is a known brand in our database"
            )

        # Check for brand synonyms and aliases
        if brand_normalized in BrandNormalizer.BRAND_SYNONYMS:
            canonical_brand = BrandNormalizer.BRAND_SYNONYMS[brand_normalized]
            logger.info(f"Input matches brand synonym: '{brand_normalized}' → '{canonical_brand}'")
            return BrandExtractionManager._format_result(
                success=True,
                message=f"Brand synonym recognized: '{canonical_brand}'",
                extracted_brand=canonical_brand.title(),
                confidence=85,
                method="brand_synonym_match",
                reason=f"'{brand_normalized}' is a synonym for '{canonical_brand}'"
            )

        # Check for brand aliases
        for alias, canonical in BrandNormalizer.BRAND_ALIASES.items():
            if alias == brand_normalized:
                logger.info(f"Input matches brand alias: '{brand_normalized}' → '{canonical}'")
                return BrandExtractionManager._format_result(
                    success=True,
                    message=f"Brand alias recognized: '{canonical}'",
                    extracted_brand=canonical.title(),
                    confidence=85,
                    method="brand_alias_match",
                    reason=f"'{brand_normalized}' is an alias for '{canonical}'"
                )

        # Check if the input contains a known brand name
        for brand_key in BrandNormalizer.BRAND_IDENTIFICATION_DB.keys():
            brand_key_normalized = BrandNormalizer.normalize(brand_key)
            if brand_key_normalized and len(brand_key_normalized) > 2:
                if brand_key_normalized in brand_normalized:
                    logger.info(f"Found brand '{brand_key}' in input: '{product_name}'")
                    return BrandExtractionManager._format_result(
                        success=True,
                        message=f"Brand '{brand_key}' found in input",
                        extracted_brand=brand_key.title(),
                        confidence=80,
                        method="brand_in_input",
                        reason=f"Brand '{brand_key}' found within input text"
                    )

        return BrandExtractionManager._format_result(
            success=False,
            message="No direct brand match found",
            extracted_brand=None,
            confidence=0,
            method="direct_check"
        )

    @staticmethod
    async def _search_open_food_facts(product_name: str) -> Dict[str, Any]:
        """Search Open Food Facts for brand information"""
        search_result = await OpenFoodFactsClient.search_by_name(product_name, max_results=20)

        if not search_result["found"]:
            # Fallback to parent company mapping
            parent_company = BrandNormalizer.find_parent_company(product_name)
            if parent_company:
                logger.info(f"Fallback to parent company: '{parent_company}' for product '{product_name}'")
                return BrandExtractionManager._format_result(
                    success=True,
                    message=f"Using parent company '{parent_company}' for product",
                    extracted_brand=parent_company.title(),
                    confidence=75,
                    method="parent_company_mapping",
                    parent_company=parent_company,
                    warning="Using parent company mapping (not from Open Food Facts)",
                    reason=f"Product '{product_name}' belongs to parent company '{parent_company}'"
                )

            return BrandExtractionManager._format_result(
                success=False,
                message=search_result["message"],
                extracted_brand=None,
                confidence=0,
                method="search_failed",
                search_results=search_result
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
                    reason=f"Product '{product_name}' belongs to parent company '{parent_company}'"
                )

            return BrandExtractionManager._format_result(
                success=False,
                message="No brand could be determined from search results",
                extracted_brand=None,
                confidence=0,
                method="search_failed",
                parent_company=None,
                search_results=search_result
            )

        # Successfully extracted brand from search
        return BrandExtractionManager._process_search_result(product_name, top_brand, search_result)

    @staticmethod
    def _process_search_result(product_name: str, extracted_brand: str, search_result: Dict) -> Dict[str, Any]:
        """Process search result to determine final brand"""
        parent_company = BrandNormalizer.find_parent_company(product_name)

        if parent_company:
            normalized_extracted = BrandNormalizer.normalize(extracted_brand)
            normalized_parent = BrandNormalizer.normalize(parent_company)

            # If parent company is different, consider using it
            if normalized_extracted != normalized_parent:
                # Check if parent company is a known national brand
                if normalized_parent in BrandNormalizer.NATIONAL_BRANDS:
                    logger.info(f"Using parent company '{parent_company}' instead of extracted '{extracted_brand}'")
                    extracted_brand = parent_company.title()
                    confidence = 80
                    method = "parent_company_override"
                    reason = f"Parent company '{parent_company}' is a known national brand"
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
                "total_products": search_result["brand_analysis"].get("total_products", 0),
                "total_brand_candidates": search_result["brand_analysis"].get("total_brand_candidates", 0)
            }
        )

    @staticmethod
    async def _handle_single_word_input(product_name: str) -> Dict[str, Any]:
        """Handle single word input - likely a brand name"""
        brand_normalized = BrandNormalizer.normalize(product_name)

        # Check for fuzzy matches with known brands
        best_match = None
        best_score = 0.0

        for brand_key in BrandNormalizer.BRAND_IDENTIFICATION_DB.keys():
            brand_key_normalized = BrandNormalizer.normalize(brand_key)
            similarity = SequenceMatcher(None, brand_normalized, brand_key_normalized).ratio()

            if similarity > best_score and similarity >= 0.7:  # 70% similarity threshold
                best_score = similarity
                best_match = brand_key

        if best_match:
            logger.info(f"Fuzzy match found: '{brand_normalized}' → '{best_match}' ({best_score:.1%} similarity)")
            confidence = int(best_score * 100)
            return BrandExtractionManager._format_result(
                success=True,
                message=f"Fuzzy match found for '{product_name}'",
                extracted_brand=best_match.title(),
                confidence=confidence,
                method="fuzzy_match",
                warning=f"Using fuzzy match ({best_score:.1%} similarity)",
                reason=f"'{product_name}' closely matches known brand '{best_match}'"
            )

        # If we get here, we couldn't identify the brand
        return BrandExtractionManager._format_result(
            success=False,
            message=f"Could not identify brand from '{product_name}'",
            extracted_brand=None,
            confidence=0,
            method="failed",
            warning="Input could not be identified as a brand or product",
            reason="No matches found in brand database or product search"
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
        **kwargs
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
            "reason": reason
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

# Database
USERS_DB = {}
PURCHASE_HISTORY_DB = {}
PRODUCT_CACHE = {}

# Test user with secure password
USERS_DB["Test123"] = {
    "username": "Test123",
    "email": "test@example.com",
    "password_hash": hash_password("Oranges"),
    "created_at": datetime.utcnow()
}
PURCHASE_HISTORY_DB["Test123"] = []

# ==================== SCRIPT EXECUTION FUNCTIONS ====================

def run_create_excel_script() -> Dict[str, Any]:
    """Execute the create_excel.py script"""
    try:
        # Check if script exists
        if not os.path.exists(FileConfig.CREATE_EXCEL_SCRIPT):
            return {
                "success": False,
                "message": f"Script file not found: {FileConfig.CREATE_EXCEL_SCRIPT}",
                "output": ""
            }

        # Import and run the script directly
        spec = importlib.util.spec_from_file_location("create_excel", FileConfig.CREATE_EXCEL_SCRIPT)
        create_excel_module = importlib.util.module_from_spec(spec)

        output_capture = io.StringIO()

        with redirect_stdout(output_capture), redirect_stderr(output_capture):
            spec.loader.exec_module(create_excel_module)
            # Run the main function if it exists
            if hasattr(create_excel_module, 'create_sample_excel_file'):
                create_excel_module.create_sample_excel_file()

        output = output_capture.getvalue()

        # Reload certification data
        certification_manager.load_certification_data()

        return {
            "success": True,
            "message": f"Successfully executed {FileConfig.CREATE_EXCEL_SCRIPT}",
            "output": output,
            "excel_file_created": os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE),
            "excel_file_size": os.path.getsize(FileConfig.CERTIFICATION_EXCEL_FILE)
                            if os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE) else 0
        }
    except Exception as e:
        logger.error(f"Error executing {FileConfig.CREATE_EXCEL_SCRIPT}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Error executing script: {str(e)}",
            "output": str(e)
        }

def verify_excel_script() -> Dict[str, Any]:
    """Verify the create_excel.py script and Excel file"""
    try:
        # Check if script exists
        script_exists = os.path.exists(FileConfig.CREATE_EXCEL_SCRIPT)
        script_size = os.path.getsize(FileConfig.CREATE_EXCEL_SCRIPT) if script_exists else 0

        # Check if Excel file exists
        excel_exists = os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE)
        excel_size = os.path.getsize(FileConfig.CERTIFICATION_EXCEL_FILE) if excel_exists else 0

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
                    "first_few_rows": df.head(5).to_dict('records')
                }
            except Exception as e:
                excel_data = {"error": str(e)}
        return {
            "script": {
                "exists": script_exists,
                "size_bytes": script_size,
                "path": os.path.abspath(FileConfig.CREATE_EXCEL_SCRIPT) if script_exists else None
            },
            "excel_file": {
                "exists": excel_exists,
                "size_bytes": excel_size,
                "path": os.path.abspath(FileConfig.CERTIFICATION_EXCEL_FILE) if excel_exists else None,
                "data": excel_data
            },
            "certification_data_loaded": certification_manager.data is not None,
            "certification_records": len(certification_manager.data) if certification_manager.data else 0
        }
    except Exception as e:
        logger.error(f"Error verifying script: {e}")
        return {
            "error": str(e),
            "script": {"exists": False},
            "excel_file": {"exists": False}
        }

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
            <div class="subtitle">Version 2.2 • Consistent, Transparent Certification-Based Scoring</div>

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
                        <td><strong>B Corp</strong></td>
                        <td>+1.0</td>
                        <td>+1.0</td>
                        <td>+1.0</td>
                        <td>Holistic corporate responsibility</td>
                    </tr>
                    <tr>
                        <td><strong>Fair Trade</strong></td>
                        <td>+1.0</td>
                        <td>+0.5</td>
                        <td>+0.5</td>
                        <td>Social justice & fair compensation</td>
                    </tr>
                    <tr>
                        <td><strong>Rainforest Alliance</strong></td>
                        <td>+0.5</td>
                        <td>+1.0</td>
                        <td>+0.5</td>
                        <td>Environmental sustainability</td>
                    </tr>
                    <tr>
                        <td><strong>Leaping Bunny</strong></td>
                        <td>+1.0</td>
                        <td>+0.5</td>
                        <td>+0.0</td>
                        <td>Animal welfare</td>
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
                <p><strong>Certifications:</strong> B Corp, Fair Trade, Rainforest Alliance</p>

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
                        <td>+1.0</td>
                        <td>+1.0</td>
                        <td>+1.0</td>
                    </tr>
                    <tr>
                        <td>+ Fair Trade Certification</td>
                        <td>+1.0</td>
                        <td>+0.5</td>
                        <td>+0.5</td>
                    </tr>
                    <tr>
                        <td>+ Rainforest Alliance Certification</td>
                        <td>+0.5</td>
                        <td>+1.0</td>
                        <td>+0.5</td>
                    </tr>
                    <tr>
                        <td>+ Multi-Cert Bonus (2 additional certs × {ScoringConfig.MULTI_CERT_BONUS})</td>
                        <td>+1.0</td>
                        <td>+1.0</td>
                        <td>+1.0</td>
                    </tr>
                    <tr style="font-weight: bold; background: #e8f5e9;">
                        <td>Final Scores (capped at 10.0)</td>
                        <td>8.5</td>
                        <td>8.5</td>
                        <td>8.0</td>
                    </tr>
                </table>

                <p><strong>Overall TBL Score:</strong> (8.5 + 8.5 + 8.0) ÷ 3 = <strong>8.3</strong></p>
                <p><strong>Grade:</strong> <span class="grade-box great">GREAT</span></p>
            </div>

            <div class="section">
                <h2>🔄 Consistent Scoring Across All Search Methods</h2>
                <div class="principle-box">
                    <h3>Single Source of Truth</h3>
                    <p>One function (<code>calculate_brand_scores()</code>) handles all scoring</p>
                    <p>Combines certifications from Excel database AND hardcoded database</p>
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

def render_score_breakdown(brand: str, scores: BrandData, tbl: Dict[str, Any],
                          excel_result: Dict[str, Any]) -> str:
    """Render score breakdown HTML"""
    brand_normalized = BrandNormalizer.normalize(brand)

    # Calculate how the score was derived
    base_score = ScoringConfig.BASE_SCORE
    total_social_bonus = scores.social - base_score
    total_env_bonus = scores.environmental - base_score
    total_econ_bonus = scores.economic - base_score

    # Get certifications from both sources
    hardcoded_certs = []
    if brand_normalized in BrandNormalizer.BRAND_IDENTIFICATION_DB:
        hardcoded_certs = BrandNormalizer.BRAND_IDENTIFICATION_DB[brand_normalized].get("certifications", [])

    excel_cert_list = []
    if excel_result["certifications"]["b_corp"]:
        excel_cert_list.append("B Corp")
    if excel_result["certifications"]["fair_trade"]:
        excel_cert_list.append("Fair Trade")
    if excel_result["certifications"]["rainforest_alliance"]:
        excel_cert_list.append("Rainforest Alliance")
    if excel_result["certifications"]["leaping_bunny"]:
        excel_cert_list.append("Leaping Bunny")

    # Combine both sources
    all_certs = list(set(hardcoded_certs + excel_cert_list))

    # Generate certification badges HTML
    cert_badges = ''.join([f'<span class="cert-badge">{cert}</span>' for cert in all_certs]) \
                  if all_certs else '<p style="color: #666; font-style: italic;">No verified certifications found</p>'

    # Generate certification bonus rows HTML
    cert_rows = ''.join([f'''
    <div class="bonus-row">
        <span>+ {cert} Certification</span>
        <span>+{ScoringConfig.CERTIFICATION_BONUSES[cert]['social']:.1f} social,
              +{ScoringConfig.CERTIFICATION_BONUSES[cert]['environmental']:.1f} environmental,
              +{ScoringConfig.CERTIFICATION_BONUSES[cert]['economic']:.1f} economic</span>
    </div>
    ''' for cert in all_certs if cert in ScoringConfig.CERTIFICATION_BONUSES])

    # Generate multi-cert bonus row HTML
    multi_cert_row = f'''
    <div class="bonus-row">
        <span>+ Multi-Certification Bonus ({len(all_certs)-1} additional cert{'s' if len(all_certs)-1 != 1 else ''} × {ScoringConfig.MULTI_CERT_BONUS})</span>
        <span>+{(len(all_certs)-1) * ScoringConfig.MULTI_CERT_BONUS:.1f} to each pillar</span>
    </div>
    ''' if len(all_certs) > 1 else '<p style="color: #666; font-style: italic;">No multi-certification bonus (only one or no certifications)</p>'

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
            </div>

            <div class="excel-status {'excel-found' if excel_result['found'] else 'excel-notfound'}">
                {'✓ Found in Excel Database' if excel_result['found'] else '✗ Not in Excel Database'}
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
                    Combined from Excel database and hardcoded database
                </p>
            </div>

            <div style="text-align: center; margin-top: 40px;">
                <a href="/" class="back-button">🏠 Back to Scanner</a>
                <a href="/scoring-methodology" class="back-button" style="background: linear-gradient(135deg, #ff9800 0%, #e65100 100%);">📖 Full Methodology</a>
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

@app.get("/test/scoring/{brand}")
async def test_scoring_methodology(brand: str):
    """Test the scoring methodology for a specific brand - returns HTML"""
    scores = scoring_manager.calculate_brand_scores(brand)
    tbl = calculate_overall_score(scores.social, scores.environmental, scores.economic)
    excel_result = certification_manager.get_certifications(brand)

    return HTMLResponse(content=render_score_breakdown(brand, scores, tbl, excel_result))

@app.post("/auth/register")
async def register_user(user: UserRegistration) -> Dict[str, Any]:
    """Register new user"""
    if user.username in USERS_DB:
        raise HTTPException(status_code=400, detail="Username already exists")

    USERS_DB[user.username] = {
        "username": user.username,
        "email": user.email,
        "password_hash": hash_password(user.password),
        "created_at": datetime.utcnow()
    }
    PURCHASE_HISTORY_DB[user.username] = []

    logger.info(f"New user registered: {user.username}")
    return {"message": "User registered successfully", "username": user.username}

@app.post("/auth/login")
async def login_user(login_data: LoginRequest) -> Dict[str, Any]:
    """Login user"""
    user = USERS_DB.get(login_data.username)
    if not user or not verify_password(login_data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    logger.info(f"User logged in: {login_data.username}")
    return {
        "message": "Login successful",
        "username": login_data.username,
        "token": "token_" + login_data.username
    }

@app.post("/scan")
async def scan_product(product: Product) -> Dict[str, Any]:
    """Scan product and return TBL scores with verified certifications"""
    logger.info(f"Scan request: barcode={product.barcode}, brand={product.brand}, name={product.product_name}")

    brand_extraction_info = {
        "extracted_from_name": False,
        "reason": "Brand provided or insufficient product name"
    }

    # If barcode provided, try to get product info from Open Food Facts
    if product.barcode and product.barcode.strip() != "":
        product_info = await food_facts_client.lookup_barcode(product.barcode)
        if product_info.get("found"):
            # Use data from Open Food Facts
            product.brand = product_info.get("brand", product.brand)
            product.product_name = product_info.get("name", product.product_name)
            product.category = product_info.get("category", product.category)

    # If brand is empty/Unknown but product_name is provided, try to extract brand
    if (not product.brand or product.brand == "Unknown") and product.product_name and product.product_name != "Generic Product":
        logger.info(f"Attempting to extract brand from product name: {product.product_name}")
        brand_extraction = await brand_extraction_manager.extract_brand_from_product_name(product.product_name)

        if brand_extraction["success"]:
            extracted_brand = brand_extraction["extracted_brand"]
            logger.info(f"Successfully extracted brand '{extracted_brand}' from product name '{product.product_name}'")

            # Update the product with extracted brand
            product.brand = extracted_brand

            # Also update product name if it was just a brand name
            if brand_extraction["method"] in ["direct_brand_recognition", "brand_synonym_match",
                                             "brand_alias_match", "brand_in_input", "fuzzy_match"]:
                product.product_name = f"{extracted_brand} Product"
                logger.info(f"Updated product name to '{product.product_name}' since input was a brand name")

            # Add extraction info to response
            brand_extraction_info = {
                "extracted_from_name": True,
                "confidence": brand_extraction["confidence"],
                "method": brand_extraction.get("method", "unknown"),
                "parent_company": brand_extraction.get("parent_company"),
                "warning": brand_extraction.get("warning"),
                "alternative_brands": brand_extraction.get("alternative_brands", []),
                "search_results": brand_extraction.get("search_results", {})
            }
        else:
            logger.warning(f"Failed to extract brand from product name: {brand_extraction['message']}")
            brand_extraction_info = {
                "extracted_from_name": False,
                "error": brand_extraction["message"],
                "method": brand_extraction.get("method", "none")
            }
            # If we couldn't extract a brand, use the input as the brand name (fallback)
            product.brand = product.product_name

    # Get scores using the scoring manager
    scores = scoring_manager.calculate_brand_scores(product.brand)
    tbl = calculate_overall_score(scores.social, scores.environmental, scores.economic)

    # Get certifications from Excel for display purposes
    cert_result = certification_manager.get_certifications(product.brand)

    logger.info(f"Scan result for {product.brand}: score={tbl['overall_score']}, certs={scores.certifications}")

    response_data = {
        "barcode": product.barcode,
        "brand": product.brand,
        "product_name": product.product_name,
        "category": product.category,
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
            "leaping_bunny": "Leaping Bunny" in scores.certifications
        },
        "certification_source": "Hardcoded Database (pre-calculated) + Excel Database (combined)",
        "scoring_method": scores.scoring_method,
        "notes": scores.notes,
        "found_in_excel": cert_result["found"],
        "excel_details": cert_result["details"],
        "certification_verified_date": datetime.utcnow().isoformat(),
        "certification_sources": FileConfig.CERT_SOURCES,
        "scoring_methodology": f"Base {ScoringConfig.BASE_SCORE} + Objective Certification Bonuses Only + Multi-Cert Bonus",
        "methodology_explanation": "See /scoring-methodology for detailed breakdown",
        "brand_extraction_info": brand_extraction_info
    }

    return response_data

@app.post("/extract-brand")
async def extract_brand_endpoint(search: ProductSearch) -> Dict[str, Any]:
    """Extract brand name from product name using enhanced methods"""
    logger.info(f"Extract brand request for product: {search.product_name}")

    result = await brand_extraction_manager.extract_brand_from_product_name(search.product_name)

    return {
        "product_name": search.product_name,
        "result": result
    }

@app.get("/test/brand-extraction/{product_name}")
async def test_brand_extraction_endpoint(product_name: str):
    """Test endpoint for brand extraction"""
    result = await brand_extraction_manager.extract_brand_from_product_name(product_name)
    parent_company = BrandNormalizer.find_parent_company(product_name)

    return {
        "product_name": product_name,
        "extraction_result": result,
        "parent_company": parent_company,
        "normalized_product_name": BrandNormalizer.normalize(product_name)
    }

# ==================== EXCEL MANAGEMENT ENDPOINTS ====================

@app.get("/certifications/status")
async def get_certification_status():
    """Get status of certification data"""
    certification_manager.load_certification_data()

    if certification_manager.data is None:
        return {
            "status": "error",
            "message": "Certification data not loaded",
            "excel_file": FileConfig.CERTIFICATION_EXCEL_FILE,
            "exists": os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE)
        }

    # Get sample brands with their certifications
    sample_brands = []
    for i, (brand_key, data) in enumerate(certification_manager.data.items()):
        if i >= 5:
            break
        sample_brands.append({
            "original_brand": data['original_brand'],
            "normalized": brand_key,
            "certifications": data['certifications']
        })

    return {
        "status": "success",
        "excel_file": FileConfig.CERTIFICATION_EXCEL_FILE,
        "total_records": len(certification_manager.data),
        "last_loaded": certification_manager.last_loaded.isoformat() if certification_manager.last_loaded else None,
        "sample_brands": sample_brands
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
            "total_records": len(certification_manager.data) if certification_manager.data else 0
        }

    except Exception as e:
        logger.error(f"Error uploading certification file: {e}")
        raise HTTPException(status_code=500, detail=f"Error uploading file: {str(e)}")

@app.get("/certifications/search/{brand}")
async def search_certifications(brand: str):
    """Search for a brand in the certification database"""
    result = certification_manager.get_certifications(brand)

    return {
        "brand": brand,
        "found": result["found"],
        "certifications": result["certifications"],
        "details": result["details"]
    }

@app.get("/certifications/export")
async def export_certifications():
    """Export certification data as JSON"""
    if certification_manager.data is None:
        certification_manager.load_certification_data()

    if certification_manager.data is None:
        raise HTTPException(status_code=404, detail="No certification data available")

    return JSONResponse(content=certification_manager.data)

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
            "excel_file_path": os.path.abspath(FileConfig.CERTIFICATION_EXCEL_FILE)
                              if os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE) else None
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
        **result
    }

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
            "backup_created": backup_file is not None and os.path.exists(backup_file)
        }

        if backup_file and os.path.exists(backup_file):
            response["backup_file"] = backup_file

        return response
    else:
        raise HTTPException(status_code=500, detail=result["message"])

# ==================== BARCODE VALIDATION ENDPOINT ====================

@app.get("/validate/barcode/{barcode}")
async def validate_barcode_format(barcode: str):
    """Validate barcode format and provide debugging info for ZXing-web"""
    # Common barcode patterns
    patterns = {
        "UPC-A": r'^\d{12}$',
        "UPC-E": r'^\d{6,8}$',
        "EAN-13": r'^\d{13}$',
        "EAN-8": r'^\d{8}$',
        "Code 39": r'^[A-Z0-9\-\.\ \$\/\+\%]+$',
        "Code 128": r'^[\x00-\x7F]+$',
        "QR Code": r'^.+$',  # QR codes can contain any data
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
        "is_valid_upc": len(barcode) in [12, 13, 8] and barcode.isdigit(),
        "zxing_support": "Yes" if detected_formats else "No - may need manual entry",
        "suggested_action": "Scan with /scan endpoint" if detected_formats else "Try manual lookup with /product/{barcode}"
    }

# ==================== TEST ENDPOINTS ====================

@app.get("/test/excel/{brand}")
async def test_excel_lookup(brand: str):
    """Test endpoint to check Excel lookup for a specific brand"""
    result = certification_manager.get_certifications(brand)

    # Also check all brands in Excel for debugging
    all_brands = []
    if certification_manager.data:
        for brand_key, data in certification_manager.data.items():
            all_brands.append({
                "normalized": brand_key,
                "original": data['original_brand'],
                "certifications": data['certifications']
            })

    return {
        "test_brand": brand,
        "normalized_brand": BrandNormalizer.normalize(brand),
        "result": result,
        "all_brands_in_excel": all_brands[:10],
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
        tbl = calculate_overall_score(scores.social, scores.environmental, scores.economic)
        cert_result = certification_manager.get_certifications(brand)

        comparison.append({
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
            "multi_cert_bonus": scores.multi_cert_bonus
        })

    if not comparison:
        raise HTTPException(status_code=400, detail="No valid brands provided")

    comparison.sort(key=lambda x: x["overall_score"], reverse=True)

    logger.info(f"Compared {len(brands)} brands")
    return {"comparison": comparison}

@app.post("/purchase")
async def record_purchase(username: str = Query(...), product: Optional[Product] = None) -> Dict[str, Any]:
    """Record user purchase"""
    if username not in USERS_DB:
        raise HTTPException(status_code=404, detail="User not found")

    if not product:
        raise HTTPException(status_code=400, detail="Product data required")

    scores = scoring_manager.calculate_brand_scores(product.brand)
    tbl = calculate_overall_score(scores.social, scores.environmental, scores.economic)

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
        "scoring_methodology": f"Base {ScoringConfig.BASE_SCORE} + Certification Bonuses + Multi-Cert Bonus"
    }

    if username not in PURCHASE_HISTORY_DB:
        PURCHASE_HISTORY_DB[username] = []
    PURCHASE_HISTORY_DB[username].append(purchase)

    logger.info(f"Purchase recorded for {username}: {product.product_name}")
    return {"message": "Purchase recorded", "purchase": purchase}

@app.get("/history/{username}")
async def get_purchase_history(username: str, limit: int = 50) -> Dict[str, Any]:
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

@app.get("/product/{barcode}")
async def get_product_info(barcode: str) -> Dict[str, Any]:
    """Get comprehensive product info by barcode with verified certifications"""
    # Add ZXing-web specific validation
    if not barcode or barcode.strip() == "":
        raise HTTPException(
            status_code=400,
            detail="Empty barcode. ZXing-web may not have captured properly. Try manual entry or rescan."
        )

    # Check if barcode looks like a common format
    if len(barcode) < 6:
        logger.warning(f"Short barcode from ZXing-web: {barcode}. May be misread.")

    product = await food_facts_client.lookup_barcode(barcode)

    # Enhanced logging for debugging scanner issues
    logger.info(f"ZXing-web scan -> Barcode: {barcode}, Length: {len(barcode)}, Found in OFF: {product.get('found', False)}")

    brand_name = product.get("brand", "Unknown")
    if brand_name != "Unknown":
        brand_name = brand_name.replace("The ", "").strip()

    # Use the scoring manager
    scores = scoring_manager.calculate_brand_scores(brand_name)
    tbl = calculate_overall_score(scores.social, scores.environmental, scores.economic)
    cert_result = certification_manager.get_certifications(brand_name)

    result = {
        "barcode": barcode,
        "found": product.get("found", False),
        "name": product.get("name"),
        "brand": brand_name,
        "category": product.get("category"),
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
            "leaping_bunny": "Leaping Bunny" in scores.certifications
        },
        "scoring_method": scores.scoring_method,
        "notes": scores.notes,
        "multi_cert_applied": scores.multi_cert_applied,
        "multi_cert_bonus": scores.multi_cert_bonus,
        "certification_source": "Hardcoded Database (pre-calculated) + Excel Database (combined)",
        "found_in_excel": cert_result["found"],
        "excel_details": cert_result["details"],
        "certification_verified_date": datetime.utcnow().isoformat(),
        "certification_sources": FileConfig.CERT_SOURCES,
        "scoring_methodology": f"Base {ScoringConfig.BASE_SCORE} + Objective Certification Bonuses Only + Multi-Cert Bonus",
        "methodology_explanation": "See /scoring-methodology for detailed breakdown",
        "scanner_notes": "Scanned with ZXing-web. If barcode looks incorrect, try adjusting lighting or camera distance."
    }

    # Include Open Food Facts data if product was found
    if product.get("found"):
        result["open_food_facts"] = {
            "eco_score_grade": product.get("eco_score"),
            "eco_score_value": product.get("eco_score_value"),
            "nutriscore_grade": product.get("nutriscore"),
            "nutriscore_value": product.get("nutriscore_value"),
            "nutrition": {
                "energy_kcal": product.get("energy_kcal"),
                "fat_g": product.get("fat"),
                "carbohydrates_g": product.get("carbohydrates"),
                "proteins_g": product.get("proteins"),
                "salt_g": product.get("salt")
            },
            "ingredients": product.get("ingredients"),
            "allergens": product.get("allergens"),
            "countries": product.get("countries"),
            "image_url": product.get("image_url"),
            "last_updated": product.get("last_updated")
        }
    else:
        # Provide helpful guidance for failed scans
        result["scanner_tips"] = {
            "suggestion": "Try scanning again with better lighting",
            "alternative": "Use manual entry with brand name instead",
            "validate_format": f"Visit /validate/barcode/{barcode} to check barcode format"
        }

    logger.info(f"ZXing-web product lookup for barcode: {barcode} - Found: {product.get('found', False)}")
    return result

# ==================== SCANNER HEALTH ENDPOINT ====================

@app.get("/scanner/health")
async def scanner_health():
    """Check scanner system health and compatibility"""
    return {
        "scanner_system": "ZXing-web (Browser Multi-Format Reader)",
        "backend_integration": "✅ Ready",
        "api_endpoints": {
            "scan": "/scan (POST) - Main scanning endpoint",
            "product_lookup": "/product/{barcode} (GET)",
            "barcode_validation": "/validate/barcode/{barcode} (GET)",
            "health": "/scanner/health (GET)"
        },
        "supported_formats": [
            "UPC-A (12-digit)",
            "UPC-E (6-8 digit)",
            "EAN-13 (13-digit)",
            "EAN-8 (8-digit)",
            "Code 39",
            "Code 128",
            "QR Code"
        ],
        "camera_requirements": "User media permission required",
        "mobile_compatible": "Yes (iOS Safari 11+, Android Chrome 53+)",
        "https_required": "Recommended for camera access",
        "fallback_methods": [
            "Manual barcode entry",
            "Brand name search via /extract-brand",
            "Product name search"
        ],
        "troubleshooting": {
            "no_camera": "Check browser permissions and ensure HTTPS",
            "poor_scanning": "Improve lighting and hold steady",
            "wrong_barcode": "Validate format at /validate/barcode/{code}"
        }
    }

# ==================== OTHER ENDPOINTS ====================

@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint"""
    excel_exists = os.path.exists(FileConfig.CERTIFICATION_EXCEL_FILE)
    excel_status = "found" if excel_exists else "not found"

    script_exists = os.path.exists(FileConfig.CREATE_EXCEL_SCRIPT)
    script_status = "found" if script_exists else "not found"

    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "total_brands": len(BrandNormalizer.BRAND_IDENTIFICATION_DB),
        "hardcoded_scores": len(BrandNormalizer.HARDCODED_SCORES_DB),
        "total_users": len(USERS_DB),
        "cache_size": len(PRODUCT_CACHE),
        "certification_system": "Hardcoded Scores + Excel-based + Hardcoded Identification Database",
        "scoring_methodology": f"Base {ScoringConfig.BASE_SCORE} + Objective Certification Bonuses + Multi-Cert Bonus",
        "scoring_priority": "Hardcoded DB → Brand Synonyms → Parent Company → Dynamic Calculation",
        "scoring_consistency": "Single scoring function ensures identical results across all search methods",
        "certification_bonuses": ScoringConfig.CERTIFICATION_BONUSES,
        "multi_cert_bonus": ScoringConfig.MULTI_CERT_BONUS,
        "excel_file": FileConfig.CERTIFICATION_EXCEL_FILE,
        "excel_file_status": excel_status,
        "excel_file_size": os.path.getsize(FileConfig.CERTIFICATION_EXCEL_FILE) if excel_exists else 0,
        "excel_data_loaded": certification_manager.data is not None,
        "excel_records": len(certification_manager.data) if certification_manager.data else 0,
        "create_excel_script": FileConfig.CREATE_EXCEL_SCRIPT,
        "create_excel_script_status": script_status,
        "create_excel_script_size": os.path.getsize(FileConfig.CREATE_EXCEL_SCRIPT) if script_exists else 0,
        "brand_extraction_enhanced": True,
        "parent_company_mappings": len(BrandNormalizer.PARENT_COMPANY_MAPPING),
        "brand_aliases": len(BrandNormalizer.BRAND_ALIASES),
        "brand_synonyms": len(BrandNormalizer.BRAND_SYNONYMS),
        "scoring_methodology_endpoint": "/scoring-methodology (HTML)",
        "version": "2.3.0",
        "message": "TBL Grocery Scanner API with Consistent Scoring Across All Search Methods (Hardcoded + Dynamic)"
    }

# ==================== ADD THIS: Root endpoint to serve frontend ====================

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the frontend HTML file"""
    try:
        # Read the index.html file
        with open("index.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content, status_code=200)
    except FileNotFoundError:
        # If index.html doesn't exist, serve a basic page with instructions
        basic_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>TBL Grocery Scanner</title>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body { font-family: Arial, sans-serif; padding: 40px; text-align: center; }
                h1 { color: #2e7d32; }
                .container { max-width: 600px; margin: 0 auto; }
                .card { background: #f8f9fa; padding: 30px; border-radius: 10px; border: 1px solid #dee2e6; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🌿 TBL Grocery Scanner Backend</h1>
                <div class="card">
                    <p>✅ Backend is running!</p>
                    <p>To use the scanner, place <code>index.html</code> in the same directory as this Python file.</p>
                    <p>📊 <a href="/health">Health Check</a></p>
                    <p>📖 <a href="/scoring-methodology">Scoring Methodology</a></p>
                    <p>🛠️ <a href="/scanner/health">Scanner Health</a></p>
                </div>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=basic_html, status_code=200)

# ==================== ALSO ADD THIS: Favicon endpoint ====================

@app.get("/favicon.ico")
async def favicon():
    """Serve favicon (return empty to avoid 404 errors)"""
    # Return a minimal transparent PNG to avoid 404 errors
    from fastapi.responses import Response
    import base64

    # A 1x1 transparent PNG
    transparent_png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

    return Response(content=transparent_png, media_type="image/png")

if __name__ == "__main__":
    # Load certification data on startup
    certification_manager.load_certification_data()

    if certification_manager.data:
        logger.info(f"Successfully loaded {len(certification_manager.data)} certification records from Excel")
    else:
        logger.warning("No Excel certification data loaded")

    logger.info(f"Brand identification database has {len(BrandNormalizer.BRAND_IDENTIFICATION_DB)} brands")
    logger.info(f"Hardcoded scores database has {len(BrandNormalizer.HARDCODED_SCORES_DB)} pre-calculated scores")
    logger.info("Scoring Consistency: Single scoring function with hardcoded priority ensures identical results")
    logger.info("Multi-certification bonus always applied correctly")
    logger.info(f"Certification Bonuses: {ScoringConfig.CERTIFICATION_BONUSES}")
    logger.info(f"Multi-certification bonus: {ScoringConfig.MULTI_CERT_BONUS} per additional cert")

    logger.info(f"Parent company mappings: {len(BrandNormalizer.PARENT_COMPANY_MAPPING)}")
    logger.info(f"Brand aliases: {len(BrandNormalizer.BRAND_ALIASES)}")
    logger.info(f"Brand synonyms: {len(BrandNormalizer.BRAND_SYNONYMS)}")

    # Test some product mappings
    test_products = ["Cheerios", "Oreo Cookies", "Pringles Chips", "Dove Chocolate", "Tide Detergent"]
    for product in test_products:
        parent = BrandNormalizer.find_parent_company(product)
        if parent:
            logger.info(f"Test mapping: '{product}' → '{parent}'")

    logger.info("🎯 Scanner System: ZXing-web integrated")
    logger.info("🌐 Open http://localhost:8000 in your browser")
    logger.info("📱 For mobile: Use your computer's IP address with port 8000")
    logger.info("🔧 Key endpoint: GET /scoring-methodology for complete transparency")
    logger.info("📊 Scanner health: GET /scanner/health")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
