"""Deterministic reference (dimension) data shared by every stream.

Everything here is generated from the seed, so ids such as ``C000123`` or ``P00042`` mean the
same thing across restarts and the dim_* tables always join cleanly to the live fact streams.
Each table is built from its own RNG so adding a table never reshuffles the others.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .schema import F, TableDef
from .util import Weighted, clamp, slug

REF_DATE = date(2026, 1, 1)


@dataclass(frozen=True)
class City:
    name: str
    country: str
    region: str
    lat: float
    lon: float
    weight: float
    utc_offset: float
    temp_mean: float
    temp_seasonal: float
    temp_daily: float

    def local_time(self, ts: datetime) -> datetime:
        return ts + timedelta(hours=self.utc_offset)

    def local_hour(self, ts: datetime) -> float:
        return (ts.hour + ts.minute / 60 + ts.second / 3600 + self.utc_offset) % 24

    def outdoor_temp(self, ts: datetime) -> float:
        """Climatological temperature for this city at a UTC instant (season + daily cycle)."""
        season = math.cos(2 * math.pi * (ts.timetuple().tm_yday - 200) / 365.25)
        if self.lat < 0:
            season = -season
        daily = math.cos(2 * math.pi * (self.local_hour(ts) - 15) / 24)
        return self.temp_mean + self.temp_seasonal * season + self.temp_daily * daily


# name, country, region, lat, lon, population weight, UTC offset, mean temp, seasonal amp, daily amp
_CITY_ROWS = [
    ("New York", "United States", "North America", 40.7128, -74.0060, 10, -5, 13, 11, 4),
    ("Los Angeles", "United States", "North America", 34.0522, -118.2437, 7, -8, 18, 4, 6),
    ("Chicago", "United States", "North America", 41.8781, -87.6298, 5, -6, 10, 13, 5),
    ("Houston", "United States", "North America", 29.7604, -95.3698, 4, -6, 21, 8, 5),
    ("Toronto", "Canada", "North America", 43.6532, -79.3832, 4, -5, 9, 13, 5),
    ("Mexico City", "Mexico", "Latin America", 19.4326, -99.1332, 4, -6, 17, 3, 7),
    ("São Paulo", "Brazil", "Latin America", -23.5505, -46.6333, 4, -3, 20, 3, 5),
    ("Buenos Aires", "Argentina", "Latin America", -34.6037, -58.3816, 2, -3, 18, 6, 5),
    ("London", "United Kingdom", "Europe", 51.5074, -0.1278, 8, 0, 11, 7, 4),
    ("Manchester", "United Kingdom", "Europe", 53.4808, -2.2426, 3, 0, 10, 6, 4),
    ("Paris", "France", "Europe", 48.8566, 2.3522, 5, 1, 12, 8, 5),
    ("Berlin", "Germany", "Europe", 52.5200, 13.4050, 4, 1, 10, 10, 5),
    ("Madrid", "Spain", "Europe", 40.4168, -3.7038, 3, 1, 15, 10, 7),
    ("Amsterdam", "Netherlands", "Europe", 52.3676, 4.9041, 3, 1, 10, 7, 4),
    ("Lagos", "Nigeria", "Africa", 6.5244, 3.3792, 4, 1, 27, 1.5, 4),
    ("Accra", "Ghana", "Africa", 5.6037, -0.1870, 3, 0, 27, 1.5, 4),
    ("Nairobi", "Kenya", "Africa", -1.2921, 36.8219, 3, 3, 19, 1.5, 6),
    ("Johannesburg", "South Africa", "Africa", -26.2041, 28.0473, 3, 2, 16, 5, 7),
    ("Cairo", "Egypt", "Africa", 30.0444, 31.2357, 3, 2, 22, 7, 7),
    ("Dubai", "United Arab Emirates", "Middle East", 25.2048, 55.2708, 3, 4, 28, 8, 5),
    ("Mumbai", "India", "Asia Pacific", 19.0760, 72.8777, 6, 5.5, 27, 2, 4),
    ("Bengaluru", "India", "Asia Pacific", 12.9716, 77.5946, 4, 5.5, 24, 2, 6),
    ("Singapore", "Singapore", "Asia Pacific", 1.3521, 103.8198, 4, 8, 28, 0.8, 3),
    ("Tokyo", "Japan", "Asia Pacific", 35.6762, 139.6503, 6, 9, 16, 10, 4),
    ("Seoul", "South Korea", "Asia Pacific", 37.5665, 126.9780, 4, 9, 12, 14, 5),
    ("Sydney", "Australia", "Asia Pacific", -33.8688, 151.2093, 4, 10, 18, 5, 4),
]
CITIES = [City(*row) for row in _CITY_ROWS]
CITY_BY_NAME = {c.name: c for c in CITIES}

FIRST_NAMES = [
    "James",
    "Mary",
    "Kwame",
    "Ama",
    "Wei",
    "Mei",
    "Aarav",
    "Priya",
    "Mohammed",
    "Fatima",
    "Carlos",
    "Sofia",
    "Lucas",
    "Emma",
    "Noah",
    "Olivia",
    "Liam",
    "Ava",
    "Yuki",
    "Haruto",
    "Ji-woo",
    "Amara",
    "Chidi",
    "Zanele",
    "Thabo",
    "Omar",
    "Layla",
    "Mateo",
    "Valentina",
    "Hugo",
    "Chloé",
    "Lars",
    "Ingrid",
    "Ivan",
    "Anya",
    "Diego",
    "Lucía",
    "Ethan",
    "Isabella",
    "Kofi",
    "Abena",
    "Raj",
    "Ananya",
    "Tariq",
    "Noor",
    "Daniel",
    "Grace",
    "Samuel",
    "Esther",
    "Leon",
    "Mia",
    "Felix",
    "Hannah",
    "Arjun",
    "Kavya",
    "Hiro",
    "Sakura",
    "Min-jun",
    "Seo-yeon",
    "Wanjiru",
    "Tunde",
    "Nia",
    "Elijah",
    "Zara",
    "Ahmed",
]
LAST_NAMES = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Garcia",
    "Martinez",
    "Mensah",
    "Owusu",
    "Okafor",
    "Adeyemi",
    "Kariuki",
    "Naidoo",
    "Dlamini",
    "Wang",
    "Li",
    "Zhang",
    "Chen",
    "Patel",
    "Sharma",
    "Singh",
    "Kumar",
    "Khan",
    "Ahmed",
    "Hassan",
    "Ali",
    "Silva",
    "Santos",
    "Oliveira",
    "Rodriguez",
    "Lopez",
    "Hernandez",
    "Müller",
    "Schmidt",
    "Fischer",
    "Dubois",
    "Martin",
    "Bernard",
    "Rossi",
    "Russo",
    "Kowalski",
    "Nowak",
    "Jansen",
    "de Vries",
    "Tanaka",
    "Suzuki",
    "Sato",
    "Kim",
    "Lee",
    "Park",
    "Nguyen",
    "Tran",
    "Taylor",
    "Wilson",
    "Anderson",
    "Thomas",
    "Moore",
    "Jackson",
    "White",
    "Harris",
    "Clark",
    "Boateng",
    "Odhiambo",
]

# category, subcategory, min price, max price, product nouns
CATALOG = [
    ("Electronics", "Smartphones", 180, 1300, ["Smartphone"]),
    ("Electronics", "Laptops", 400, 2600, ["Laptop", "Notebook", "Ultrabook"]),
    ("Electronics", "Headphones", 20, 450, ["Wireless Earbuds", "Over-Ear Headphones", "Noise-Cancelling Headphones"]),
    ("Electronics", "Smart Home", 20, 300, ["Smart Speaker", "Smart Plug", "Video Doorbell", "Smart Thermostat"]),
    ("Electronics", "Wearables", 40, 600, ["Smartwatch", "Fitness Tracker"]),
    ("Electronics", "Accessories", 8, 90, ["USB-C Charger", "Power Bank", "Phone Case", "HDMI Cable", "Wireless Mouse"]),
    ("Home & Kitchen", "Cookware", 20, 300, ["Frying Pan", "Cookware Set", "Dutch Oven", "Knife Set"]),
    ("Home & Kitchen", "Small Appliances", 30, 500, ["Air Fryer", "Coffee Maker", "Blender", "Stand Mixer", "Electric Kettle"]),
    ("Home & Kitchen", "Furniture", 60, 1500, ["Office Chair", "Bookshelf", "Coffee Table", "Standing Desk"]),
    ("Home & Kitchen", "Bedding", 20, 250, ["Duvet Set", "Pillow Pair", "Weighted Blanket"]),
    ("Home & Kitchen", "Decor", 10, 150, ["Table Lamp", "Wall Art", "Scented Candle", "Plant Pot"]),
    ("Fashion", "Men's Apparel", 15, 200, ["T-Shirt", "Hoodie", "Chinos", "Rain Jacket", "Polo Shirt"]),
    ("Fashion", "Women's Apparel", 15, 250, ["Dress", "Blouse", "Jeans", "Cardigan", "Trench Coat"]),
    ("Fashion", "Footwear", 30, 250, ["Running Shoes", "Sneakers", "Leather Boots", "Sandals"]),
    ("Fashion", "Bags & Accessories", 10, 300, ["Backpack", "Tote Bag", "Leather Wallet", "Sunglasses"]),
    ("Sports & Outdoors", "Fitness", 15, 900, ["Yoga Mat", "Dumbbell Set", "Resistance Bands", "Exercise Bike"]),
    ("Sports & Outdoors", "Camping", 20, 500, ["Tent", "Sleeping Bag", "Camping Stove", "Headlamp"]),
    ("Sports & Outdoors", "Cycling", 25, 1800, ["Road Bike", "Bike Helmet", "Bike Lights", "Cycling Gloves"]),
    ("Beauty & Personal Care", "Skincare", 8, 90, ["Moisturizer", "Vitamin C Serum", "Sunscreen SPF 50", "Cleanser"]),
    ("Beauty & Personal Care", "Makeup", 6, 60, ["Lipstick", "Mascara", "Foundation", "Eyeshadow Palette"]),
    ("Beauty & Personal Care", "Haircare", 6, 250, ["Shampoo", "Conditioner", "Hair Dryer", "Hair Oil"]),
    ("Grocery", "Snacks", 2, 12, ["Granola Bars", "Kettle Chips", "Dark Chocolate", "Trail Mix"]),
    ("Grocery", "Beverages", 2, 30, ["Cold Brew Coffee", "Green Tea", "Sparkling Water 12-Pack", "Orange Juice"]),
    ("Grocery", "Pantry", 2, 25, ["Olive Oil", "Basmati Rice", "Pasta", "Honey", "Peanut Butter"]),
    ("Grocery", "Fresh", 2, 20, ["Avocados", "Free-Range Eggs", "Sourdough Bread", "Greek Yogurt"]),
    ("Toys & Games", "Toys", 8, 150, ["Building Blocks Set", "Remote Control Car", "Plush Bear", "1000-Piece Puzzle"]),
    ("Toys & Games", "Video Games", 20, 70, ["Adventure Game", "Racing Game", "Puzzle Game", "Sports Game"]),
    ("Books & Media", "Books", 8, 45, ["Mystery Novel", "Cookbook", "Data Science Handbook", "Biography", "Travel Guide"]),
]
BRANDS = {
    "Electronics": ["Voltix", "Nexora", "Lumina", "Quantra", "Aurex", "Zentro"],
    "Home & Kitchen": ["Hearthly", "Casaro", "Kitchora", "NestWell", "Oakhaven"],
    "Fashion": ["Urbane", "Threadline", "Velora", "Marlowe & Co"],
    "Sports & Outdoors": ["Peakline", "Stridex", "Trailcraft", "Summitry"],
    "Beauty & Personal Care": ["Glowra", "PureLeaf", "Velvetine", "Bloomly"],
    "Grocery": ["FarmFresh", "GoldenGrain", "Oceanic", "Harvest Lane"],
    "Toys & Games": ["Playnest", "Brickwise", "Pixel Forge"],
    "Books & Media": ["Inkwell Press", "Lanternhouse"],
}
ADJECTIVES = ["Pro", "Ultra", "Lite", "Max", "Classic", "Eco", "Smart", "Mini", "Plus", "Air", "Prime", "Essential", "Flex", "Go"]
SIMPLE_ADJECTIVES = ["Organic", "Classic", "Premium", "Original", "Family Size", "Everyday", "Signature", "Deluxe"]
CATEGORY_DEMAND = {
    "Electronics": 1.0,
    "Home & Kitchen": 0.9,
    "Fashion": 1.3,
    "Sports & Outdoors": 0.6,
    "Beauty & Personal Care": 1.0,
    "Grocery": 1.8,
    "Toys & Games": 0.5,
    "Books & Media": 0.6,
}

SITES = [
    ("SITE-01", "Riverside Plant", "Houston"),
    ("SITE-02", "Northgate Factory", "Manchester"),
    ("SITE-03", "Harbourfront Logistics Hub", "Singapore"),
    ("SITE-04", "Highveld Data Center", "Johannesburg"),
    ("SITE-05", "Lakeside Assembly", "Chicago"),
    ("SITE-06", "Coastal Processing Plant", "Accra"),
]
# type: models, temperature C, vibration mm/s, pressure bar (None = not instrumented), rpm, power kW
DEVICE_TYPES = {
    "pump": (("PX-200", "PX-350"), 55.0, 2.4, 6.0, 1750, 15.0),
    "compressor": (("CM-75", "CM-110"), 74.0, 3.2, 8.5, 2950, 45.0),
    "motor": (("MT-30", "MT-55"), 63.0, 1.9, None, 1460, 30.0),
    "chiller": (("CH-400",), 7.5, 1.4, 4.6, 880, 60.0),
    "conveyor": (("CV-12", "CV-20"), 39.0, 3.8, None, 310, 7.5),
    "hvac_unit": (("HV-9", "HV-14"), 21.5, 1.1, 2.1, 1100, 12.0),
}

# name, MCC, median amount USD, share paid online, frequency weight, targeted by fraudsters
MERCHANT_CATEGORIES = [
    ("Grocery", "5411", 42.0, 0.12, 20, False),
    ("Restaurants", "5812", 31.0, 0.08, 15, False),
    ("Fast Food", "5814", 12.0, 0.25, 14, False),
    ("Fuel", "5541", 44.0, 0.0, 9, False),
    ("Pharmacy", "5912", 21.0, 0.15, 6, False),
    ("Clothing", "5651", 68.0, 0.45, 7, False),
    ("Department Stores", "5311", 82.0, 0.35, 5, False),
    ("Electronics", "5732", 190.0, 0.55, 4, True),
    ("Jewelry", "5944", 340.0, 0.25, 1, True),
    ("Digital Goods", "5815", 11.0, 1.0, 7, True),
    ("Gift Cards", "5947", 75.0, 0.6, 2, True),
    ("Airlines", "4511", 410.0, 1.0, 2, False),
    ("Hotels", "7011", 230.0, 0.7, 2, False),
    ("Rideshare & Taxi", "4121", 17.0, 1.0, 6, False),
    ("Utilities", "4900", 115.0, 1.0, 3, False),
    ("Entertainment", "7832", 28.0, 0.5, 3, False),
    ("Crypto Exchange", "6051", 480.0, 1.0, 0.6, True),
]
MERCHANT_CATEGORY = {c[0]: c for c in MERCHANT_CATEGORIES}
MERCHANT_NAMES = {
    "Grocery": ["FreshMart", "GreenBasket", "DailyHarvest", "CornerGrocer", "MarketHall"],
    "Restaurants": ["Bistro Luna", "The Copper Pot", "Saffron House", "Olive & Thyme", "Blue Door Kitchen"],
    "Fast Food": ["QuickBite", "Burger Barn", "Taco Loco", "WrapStar", "Noodle Box"],
    "Fuel": ["PetroPoint", "FuelStop", "GreenGas", "Highway Fuels"],
    "Pharmacy": ["CarePlus Pharmacy", "MediQuick", "WellRx"],
    "Clothing": ["Urban Closet", "Style Avenue", "Denim Depot", "Threadline Store"],
    "Department Stores": ["Grand Emporium", "MegaMart", "Harbor Department Store"],
    "Electronics": ["CircuitHub", "GadgetZone", "TechTown", "Voltix Store"],
    "Jewelry": ["Golden Facet", "Lustre & Co", "Diamond Lane"],
    "Digital Goods": ["AppVault", "StreamPlus", "GameKeys Online", "CloudTunes"],
    "Gift Cards": ["GiftHub", "CardCorner", "eGift World"],
    "Airlines": ["SkyBridge Airways", "BlueJet", "Meridian Air"],
    "Hotels": ["Harbor Suites", "Grand Plaza Hotel", "CityStay Inn"],
    "Rideshare & Taxi": ["RideNow", "CityCab", "ZipRide"],
    "Utilities": ["PowerGrid Energy", "AquaWorks Water", "FiberLink Internet"],
    "Entertainment": ["CinemaMax", "LiveArena Tickets", "PlayZone"],
    "Crypto Exchange": ["CoinVault", "BitHarbor", "TokenSwap"],
}

# symbol, company, sector, exchange, reference price, volatility factor, market beta, liquidity weight
SYMBOLS = [
    ("NVTK", "Novatek Systems", "Technology", "NEXA", 182.40, 1.2, 1.25, 9),
    ("QBTX", "Quantabit Labs", "Technology", "NEXA", 64.10, 1.8, 1.5, 6),
    ("CLDN", "Cloudnine Software", "Technology", "NEXA", 241.75, 1.3, 1.3, 7),
    ("SMCD", "Semicore Dynamics", "Technology", "NEXA", 118.20, 1.6, 1.45, 8),
    ("FNXB", "Finexa Bank", "Financials", "SIMX", 48.35, 0.9, 1.1, 7),
    ("ASRG", "Assurage Insurance", "Financials", "SIMX", 92.60, 0.7, 0.9, 4),
    ("MDCR", "MediCore Health", "Healthcare", "SIMX", 133.90, 0.8, 0.8, 5),
    ("BIOV", "BioVantage Therapeutics", "Healthcare", "NEXA", 27.45, 2.2, 1.2, 5),
    ("GRLF", "Greenleaf Energy", "Energy", "SIMX", 71.80, 1.1, 0.9, 6),
    ("SOLP", "SolarisPeak Renewables", "Energy", "NEXA", 39.15, 1.7, 1.3, 5),
    ("RTLO", "Retailio Group", "Consumer Discretionary", "SIMX", 56.20, 1.0, 1.05, 6),
    ("AUTM", "Autonomy Motors", "Consumer Discretionary", "NEXA", 211.30, 1.9, 1.6, 9),
    ("AGNV", "AgroNova Foods", "Consumer Staples", "SIMX", 44.75, 0.6, 0.6, 4),
    ("BRWH", "Brewhaven Beverages", "Consumer Staples", "SIMX", 61.40, 0.6, 0.55, 3),
    ("STWK", "SteelWorks Industries", "Materials", "SIMX", 33.60, 1.2, 1.15, 4),
    ("SKWD", "Skyward Airlines", "Industrials", "SIMX", 22.85, 1.5, 1.35, 5),
    ("LOGX", "Logistix Freight", "Industrials", "SIMX", 87.10, 0.9, 1.0, 4),
    ("TLCN", "TeleConnect Communications", "Communication Services", "SIMX", 18.95, 0.7, 0.7, 5),
    ("STRM", "Streamly Media", "Communication Services", "NEXA", 149.50, 1.4, 1.2, 7),
    ("URSP", "UrbanSpace REIT", "Real Estate", "SIMX", 26.30, 0.7, 0.75, 3),
]

METER_CITIES = ["Houston", "Toronto", "London", "Berlin", "Accra", "Dubai", "Mumbai", "Sydney"]

WAREHOUSES = [
    ("WH-01", "New York Fulfilment Center", "New York", 120_000),
    ("WH-02", "Los Angeles Fulfilment Center", "Los Angeles", 150_000),
    ("WH-03", "London Distribution Center", "London", 90_000),
    ("WH-04", "Berlin Distribution Center", "Berlin", 70_000),
    ("WH-05", "Lagos Regional Hub", "Lagos", 45_000),
    ("WH-06", "Mumbai Regional Hub", "Mumbai", 80_000),
    ("WH-07", "Singapore Gateway Hub", "Singapore", 60_000),
    ("WH-08", "São Paulo Distribution Center", "São Paulo", 55_000),
]

# zone id, zone name, city, zone type, lat, lon, demand weight
ZONES = [
    ("NYC-MID", "Midtown Manhattan", "New York", "business", 40.7549, -73.9840, 10),
    ("NYC-FID", "Financial District", "New York", "business", 40.7075, -74.0113, 6),
    ("NYC-UES", "Upper East Side", "New York", "residential", 40.7736, -73.9566, 5),
    ("NYC-HAR", "Harlem", "New York", "residential", 40.8116, -73.9465, 3),
    ("NYC-WBG", "Williamsburg", "New York", "nightlife", 40.7081, -73.9571, 5),
    ("NYC-AST", "Astoria", "New York", "residential", 40.7644, -73.9235, 3),
    ("NYC-JFK", "JFK Airport", "New York", "airport", 40.6413, -73.7781, 5),
    ("NYC-LGA", "LaGuardia Airport", "New York", "airport", 40.7769, -73.8740, 4),
    ("LON-SOH", "Soho", "London", "nightlife", 51.5136, -0.1365, 7),
    ("LON-CTY", "City of London", "London", "business", 51.5155, -0.0922, 7),
    ("LON-CWF", "Canary Wharf", "London", "business", 51.5054, -0.0235, 5),
    ("LON-WST", "Westminster", "London", "business", 51.4975, -0.1357, 5),
    ("LON-KGX", "King's Cross", "London", "residential", 51.5308, -0.1238, 5),
    ("LON-CAM", "Camden", "London", "nightlife", 51.5390, -0.1426, 4),
    ("LON-SHD", "Shoreditch", "London", "nightlife", 51.5265, -0.0798, 5),
    ("LON-LHR", "Heathrow Airport", "London", "airport", 51.4700, -0.4543, 4),
    ("SIN-MRB", "Marina Bay", "Singapore", "business", 1.2834, 103.8607, 7),
    ("SIN-ORC", "Orchard Road", "Singapore", "business", 1.3048, 103.8318, 6),
    ("SIN-BUG", "Bugis", "Singapore", "nightlife", 1.3008, 103.8559, 5),
    ("SIN-SEN", "Sentosa", "Singapore", "nightlife", 1.2494, 103.8303, 3),
    ("SIN-JUR", "Jurong East", "Singapore", "residential", 1.3329, 103.7436, 4),
    ("SIN-TAM", "Tampines", "Singapore", "residential", 1.3496, 103.9568, 4),
    ("SIN-WDL", "Woodlands", "Singapore", "residential", 1.4382, 103.7890, 3),
    ("SIN-CHG", "Changi Airport", "Singapore", "airport", 1.3644, 103.9915, 4),
]

MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _rand_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def _jitter(rng: random.Random, value: float, sd: float) -> float:
    return round(value + rng.gauss(0, sd), 5)


class ReferenceData:
    """All dimension tables plus hidden 'ground truth' parameters the generators draw on."""

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.cities = CITIES
        self.customers, self.customer_weights = self._customers(random.Random(seed * 101 + 1))
        self.products, self.product_weights, self.product_quality = self._products(random.Random(seed * 101 + 2))
        self.stores = self._stores(random.Random(seed * 101 + 3))
        self.devices, self.device_params = self._devices(random.Random(seed * 101 + 4))
        self.accounts, self.account_profiles = self._accounts(random.Random(seed * 101 + 5))
        self.merchants = self._merchants(random.Random(seed * 101 + 6))
        self.symbols, self.symbol_params = self._symbols(random.Random(seed * 101 + 7))
        self.meters, self.meter_params = self._meters(random.Random(seed * 101 + 8))
        self.warehouses = self._warehouses()
        self.zones = self._zones()
        self.calendar = self._calendar(date(2024, 1, 1), date(2027, 12, 31))

        self.customer_by_id = {c["customer_id"]: c for c in self.customers}
        self.product_by_id = {p["product_id"]: p for p in self.products}
        self.tables: dict[str, TableDef] = {t.name: t for t in self._table_defs()}

    # -- builders --------------------------------------------------------------------------

    def _customers(self, rng: random.Random):
        pick_city = Weighted(CITIES, [c.weight for c in CITIES])
        tier_factor = {"Bronze": 1.0, "Silver": 1.6, "Gold": 2.5, "Platinum": 4.0}
        rows, weights = [], []
        for i in range(1, 5001):
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            city = pick_city.pick(rng)
            age = int(clamp(rng.gauss(39, 13), 18, 88))
            tier = rng.choices(("Bronze", "Silver", "Gold", "Platinum"), (50, 30, 15, 5))[0]
            rows.append(
                {
                    "customer_id": f"C{i:06d}",
                    "first_name": first,
                    "last_name": last,
                    "email": f"{slug(first)}.{slug(last)}{rng.randint(1, 999)}@example.com",
                    "gender": rng.choices(("Female", "Male", "Non-binary", "Undisclosed"), (48, 48, 2, 2))[0],
                    "birth_date": date(REF_DATE.year - age, rng.randint(1, 12), rng.randint(1, 28)).isoformat(),
                    "signup_date": _rand_date(rng, date(2019, 1, 1), date(2026, 9, 30)).isoformat(),
                    "segment": rng.choices(("Consumer", "Small Business", "Corporate"), (72, 19, 9))[0],
                    "loyalty_tier": tier,
                    "city": city.name,
                    "country": city.country,
                    "region": city.region,
                    "latitude": _jitter(rng, city.lat, 0.06),
                    "longitude": _jitter(rng, city.lon, 0.06),
                }
            )
            weights.append(rng.lognormvariate(0, 0.8) * tier_factor[tier])
        return rows, weights

    def _products(self, rng: random.Random):
        rows, weights, quality, names = [], [], {}, set()
        self.product_noun: dict[str, str] = {}
        pid = 0
        for category, subcategory, lo, hi, nouns in CATALOG:
            simple = category in ("Grocery", "Books & Media")
            for _ in range(rng.randint(11, 17)):
                pid += 1
                brand = rng.choice(BRANDS[category])
                adjective = rng.choice(SIMPLE_ADJECTIVES if simple else ADJECTIVES)
                noun = rng.choice(nouns)
                self.product_noun[f"P{pid:05d}"] = noun
                base_name = f"{brand} {adjective} {noun}"
                name, k = base_name, 2
                while name in names:
                    name, k = f"{base_name} {k}", k + 1
                names.add(name)
                price = max(0.99, round(math.exp(rng.uniform(math.log(lo), math.log(hi)))) - 0.01)
                product_id = f"P{pid:05d}"
                rows.append(
                    {
                        "product_id": product_id,
                        "product_name": name,
                        "category": category,
                        "subcategory": subcategory,
                        "brand": brand,
                        "list_price": price,
                        "unit_cost": round(price * rng.uniform(0.38, 0.72), 2),
                        "launch_date": _rand_date(rng, date(2018, 1, 1), date(2026, 8, 31)).isoformat(),
                    }
                )
                weights.append(rng.lognormvariate(0, 0.9) * CATEGORY_DEMAND[category] / price**0.25)
                quality[product_id] = rng.betavariate(5, 2)
        return rows, weights, quality

    def _stores(self, rng: random.Random):
        rows = [
            {
                "store_id": "ONLINE",
                "store_name": "Online Store (web & app)",
                "store_type": "Online",
                "city": None,
                "country": None,
                "region": "Global",
                "latitude": None,
                "longitude": None,
                "opened_date": "2018-03-01",
                "floor_area_sqm": None,
            }
        ]
        sid = 0
        for city in CITIES:
            types = (
                ["Flagship", rng.choice(["Mall", "High Street", "Outlet"])]
                if city.weight >= 6
                else [rng.choice(["Mall", "High Street", "Outlet", "Express"])]
            )
            for store_type in types:
                sid += 1
                area = {
                    "Flagship": (2500, 6000),
                    "Mall": (900, 2500),
                    "High Street": (500, 1500),
                    "Outlet": (1200, 3000),
                    "Express": (150, 450),
                }[store_type]
                rows.append(
                    {
                        "store_id": f"S{sid:03d}",
                        "store_name": f"{city.name} {store_type}",
                        "store_type": store_type,
                        "city": city.name,
                        "country": city.country,
                        "region": city.region,
                        "latitude": _jitter(rng, city.lat, 0.03),
                        "longitude": _jitter(rng, city.lon, 0.03),
                        "opened_date": _rand_date(rng, date(2012, 1, 1), date(2025, 12, 31)).isoformat(),
                        "floor_area_sqm": rng.randint(*area),
                    }
                )
        return rows

    def _devices(self, rng: random.Random):
        rows, params = [], {}
        types = list(DEVICE_TYPES)
        n = 0
        for site_id, site_name, city_name in SITES:
            city = CITY_BY_NAME[city_name]
            for k in range(10):
                n += 1
                dtype = types[(n + k) % len(types)] if k < 6 else rng.choice(types)
                models, temp, vib, pressure, rpm, power = DEVICE_TYPES[dtype]
                device_id = f"DEV-{n:04d}"
                scale = rng.uniform(0.9, 1.1)
                rows.append(
                    {
                        "device_id": device_id,
                        "device_type": dtype,
                        "model": rng.choice(models),
                        "site_id": site_id,
                        "site_name": site_name,
                        "city": city.name,
                        "country": city.country,
                        "latitude": _jitter(rng, city.lat, 0.01),
                        "longitude": _jitter(rng, city.lon, 0.01),
                        "install_date": _rand_date(rng, date(2016, 1, 1), date(2025, 12, 31)).isoformat(),
                        "firmware_version": rng.choice(["3.1.4", "3.2.0", "3.2.1", "4.0.2"]),
                        "rated_power_kw": round(power * scale * 1.25, 1),
                    }
                )
                params[device_id] = {
                    "type": dtype,
                    "site_id": site_id,
                    "city": city,
                    "temp": temp * rng.uniform(0.95, 1.05),
                    "vib": vib * rng.uniform(0.85, 1.15),
                    "pressure": None if pressure is None else pressure * rng.uniform(0.92, 1.08),
                    "rpm": rpm * rng.uniform(0.97, 1.03),
                    "power": power * scale,
                    "rssi": rng.uniform(-82, -58),
                }
        return rows, params

    def _accounts(self, rng: random.Random):
        owners = rng.sample(self.customers, 1700) + rng.sample(self.customers, 300)
        cat_names = [c[0] for c in MERCHANT_CATEGORIES]
        rows, profiles = [], {}
        for i, cust in enumerate(owners, 1):
            card_type = "credit" if rng.random() < 0.6 else "debit"
            account_id = f"A{i:06d}"
            rows.append(
                {
                    "account_id": account_id,
                    "customer_id": cust["customer_id"],
                    "card_type": card_type,
                    "card_network": rng.choices(("Visa", "Mastercard", "Amex", "Discover"), (50, 35, 10, 5))[0],
                    "credit_limit": float(rng.choice([1000, 2500, 5000, 7500, 10000, 15000, 25000]))
                    if card_type == "credit"
                    else None,
                    "open_date": _rand_date(rng, date(2010, 1, 1), date(2026, 6, 30)).isoformat(),
                    "home_city": cust["city"],
                    "home_country": cust["country"],
                }
            )
            profiles[account_id] = {
                "customer_id": cust["customer_id"],
                "city": CITY_BY_NAME[cust["city"]],
                "spend_factor": math.exp(rng.gauss(0, 0.5)),  # multiplies category median amounts
                "categories": Weighted(cat_names, [c[4] * rng.lognormvariate(0, 0.9) for c in MERCHANT_CATEGORIES]),
                "activity": rng.lognormvariate(0, 0.7),
            }
        return rows, profiles

    def _merchants(self, rng: random.Random):
        rows = []
        n = 0
        for name, mcc, _median, online_share, _weight, _risky in MERCHANT_CATEGORIES:
            bases = MERCHANT_NAMES[name]
            if online_share < 1.0:  # physical presence in every city so local spend stays local
                for city in CITIES:
                    n += 1
                    rows.append(
                        {
                            "merchant_id": f"M{n:05d}",
                            "merchant_name": f"{rng.choice(bases)} {city.name}",
                            "category": name,
                            "mcc": mcc,
                            "city": city.name,
                            "country": city.country,
                            "latitude": _jitter(rng, city.lat, 0.04),
                            "longitude": _jitter(rng, city.lon, 0.04),
                            "is_online": False,
                        }
                    )
            online_count = 1 + round(4 * online_share)
            for k in range(online_count):
                n += 1
                hq = rng.choice(CITIES)
                rows.append(
                    {
                        "merchant_id": f"M{n:05d}",
                        "merchant_name": bases[k % len(bases)] + ("" if online_share == 1.0 else " Online"),
                        "category": name,
                        "mcc": mcc,
                        "city": hq.name,
                        "country": hq.country,
                        "latitude": None,
                        "longitude": None,
                        "is_online": True,
                    }
                )
        return rows

    def _symbols(self, rng: random.Random):
        rows, params = [], {}
        for symbol, company, sector, exchange, price, vol, beta, liquidity in SYMBOLS:
            rows.append(
                {
                    "symbol": symbol,
                    "company_name": company,
                    "sector": sector,
                    "exchange": exchange,
                    "ipo_year": rng.randint(1985, 2022),
                    "shares_outstanding_m": round(rng.uniform(80, 4200), 1),
                    "reference_price": price,
                }
            )
            params[symbol] = {
                "price": price,
                "vol": vol,
                "beta": beta,
                "liquidity": liquidity,
                "spread_bps": rng.uniform(2, 9) / (liquidity**0.5),
            }
        return rows, params

    def _meters(self, rng: random.Random):
        rows, params = [], {}
        n = 0
        for city_name in METER_CITIES:
            city = CITY_BY_NAME[city_name]
            north_america = city.country in ("United States", "Canada")
            for _ in range(6):
                n += 1
                mtype = rng.choices(("residential", "commercial", "industrial"), (60, 30, 10))[0]
                solar_p = {"residential": 0.35, "commercial": 0.4, "industrial": 0.2}[mtype]
                has_solar = rng.random() < solar_p
                capacity = {"residential": (3, 8), "commercial": (20, 100), "industrial": (100, 500)}[mtype]
                voltage = (120 if north_america else 230) if mtype == "residential" else (480 if north_america else 400)
                meter_id = f"MTR-{n:04d}"
                rows.append(
                    {
                        "meter_id": meter_id,
                        "meter_type": mtype,
                        "city": city.name,
                        "country": city.country,
                        "region": city.region,
                        "has_solar": has_solar,
                        "solar_capacity_kw": round(rng.uniform(*capacity), 1) if has_solar else None,
                        "tariff_plan": {
                            "residential": "Time-of-Use",
                            "commercial": "Business TOU",
                            "industrial": "Industrial Demand",
                        }[mtype],
                        "install_date": _rand_date(rng, date(2015, 1, 1), date(2025, 12, 31)).isoformat(),
                        "nominal_voltage_v": voltage,
                    }
                )
                scale = {
                    "residential": rng.uniform(0.7, 1.4),
                    "commercial": rng.uniform(15, 45),
                    "industrial": rng.uniform(120, 320),
                }[mtype]
                params[meter_id] = {
                    "type": mtype,
                    "city": city,
                    "scale": scale,
                    "voltage": voltage,
                    "solar_kw": rows[-1]["solar_capacity_kw"] or 0.0,
                    "hvac": rng.uniform(0.6, 1.4),
                    "register_start": rng.uniform(2_000, 60_000) * (1 if mtype == "residential" else scale),
                }
        return rows, params

    def _warehouses(self):
        rows = []
        for wid, name, city_name, capacity in WAREHOUSES:
            city = CITY_BY_NAME[city_name]
            rows.append(
                {
                    "warehouse_id": wid,
                    "warehouse_name": name,
                    "city": city.name,
                    "country": city.country,
                    "region": city.region,
                    "capacity_units": capacity,
                }
            )
        return rows

    def _zones(self):
        return [
            {
                "zone_id": zid,
                "zone_name": name,
                "city": city,
                "country": CITY_BY_NAME[city].country,
                "zone_type": ztype,
                "latitude": lat,
                "longitude": lon,
            }
            for zid, name, city, ztype, lat, lon, _w in ZONES
        ]

    def _calendar(self, start: date, end: date):
        rows = []
        day = start
        while day <= end:
            rows.append(
                {
                    "date_key": int(day.strftime("%Y%m%d")),
                    "date": day.isoformat(),
                    "year": day.year,
                    "quarter": (day.month - 1) // 3 + 1,
                    "month": day.month,
                    "month_name": MONTH_NAMES[day.month - 1],
                    "day": day.day,
                    "day_of_week": day.isoweekday(),
                    "day_name": DAY_NAMES[day.weekday()],
                    "week_of_year": day.isocalendar()[1],
                    "is_weekend": day.weekday() >= 5,
                }
            )
            day += timedelta(days=1)
        return rows

    # -- table definitions -----------------------------------------------------------------

    def _table_defs(self) -> list[TableDef]:
        return [
            TableDef(
                "dim_customer",
                "Customers",
                "5,000 customers with demographics, segment, loyalty tier and home city. Joins to orders, clickstream, "
                "reviews and dim_account.",
                [
                    F("customer_id", "string", "id", "Customer key"),
                    F("first_name", "string"),
                    F("last_name", "string"),
                    F("email", "string", "attribute", "Synthetic address on the reserved example.com domain"),
                    F("gender", "string", "dimension"),
                    F("birth_date", "date"),
                    F("signup_date", "date"),
                    F("segment", "string", "dimension", "Consumer | Small Business | Corporate"),
                    F("loyalty_tier", "string", "dimension", "Bronze | Silver | Gold | Platinum (higher tiers order more often)"),
                    F("city", "string", "dimension"),
                    F("country", "string", "dimension"),
                    F("region", "string", "dimension"),
                    F("latitude", "float"),
                    F("longitude", "float"),
                ],
                "customer_id",
                self.customers,
            ),
            TableDef(
                "dim_product",
                "Products",
                "Product catalogue with category hierarchy, brand, list price and unit cost (for margin analysis).",
                [
                    F("product_id", "string", "id", "Product key"),
                    F("product_name", "string"),
                    F("category", "string", "dimension"),
                    F("subcategory", "string", "dimension"),
                    F("brand", "string", "dimension"),
                    F("list_price", "float", "measure", "USD"),
                    F("unit_cost", "float", "measure", "USD"),
                    F("launch_date", "date"),
                ],
                "product_id",
                self.products,
            ),
            TableDef(
                "dim_store",
                "Stores",
                "Physical stores worldwide plus the ONLINE store used for web and app orders.",
                [
                    F("store_id", "string", "id", "Store key (ONLINE for web/app orders)"),
                    F("store_name", "string"),
                    F("store_type", "string", "dimension", "Online | Flagship | Mall | High Street | Outlet | Express"),
                    F("city", "string", "dimension"),
                    F("country", "string", "dimension"),
                    F("region", "string", "dimension"),
                    F("latitude", "float"),
                    F("longitude", "float"),
                    F("opened_date", "date"),
                    F("floor_area_sqm", "int", "measure"),
                ],
                "store_id",
                self.stores,
            ),
            TableDef(
                "dim_device",
                "IoT devices",
                "Industrial equipment fitted with sensors, across six sites.",
                [
                    F("device_id", "string", "id", "Device key"),
                    F("device_type", "string", "dimension"),
                    F("model", "string", "dimension"),
                    F("site_id", "string", "dimension"),
                    F("site_name", "string", "dimension"),
                    F("city", "string", "dimension"),
                    F("country", "string", "dimension"),
                    F("latitude", "float"),
                    F("longitude", "float"),
                    F("install_date", "date"),
                    F("firmware_version", "string", "dimension"),
                    F("rated_power_kw", "float", "measure"),
                ],
                "device_id",
                self.devices,
            ),
            TableDef(
                "dim_account",
                "Card accounts",
                "Payment card accounts owned by customers (some customers hold two).",
                [
                    F("account_id", "string", "id", "Account key"),
                    F("customer_id", "string", "fk", "-> dim_customer"),
                    F("card_type", "string", "dimension", "credit | debit"),
                    F("card_network", "string", "dimension"),
                    F("credit_limit", "float", "measure", "USD, null for debit cards"),
                    F("open_date", "date"),
                    F("home_city", "string", "dimension"),
                    F("home_country", "string", "dimension"),
                ],
                "account_id",
                self.accounts,
            ),
            TableDef(
                "dim_merchant",
                "Merchants",
                "Merchants with category and ISO 18245 merchant category code (MCC). Online merchants have no coordinates.",
                [
                    F("merchant_id", "string", "id", "Merchant key"),
                    F("merchant_name", "string"),
                    F("category", "string", "dimension"),
                    F("mcc", "string", "dimension", "Merchant category code"),
                    F("city", "string", "dimension", "Location, or headquarters for online merchants"),
                    F("country", "string", "dimension"),
                    F("latitude", "float"),
                    F("longitude", "float"),
                    F("is_online", "bool", "dimension"),
                ],
                "merchant_id",
                self.merchants,
            ),
            TableDef(
                "dim_symbol",
                "Listed companies",
                "Fictional listed companies traded on two fictional exchanges (NEXA, SIMX).",
                [
                    F("symbol", "string", "id", "Ticker"),
                    F("company_name", "string"),
                    F("sector", "string", "dimension"),
                    F("exchange", "string", "dimension"),
                    F("ipo_year", "int"),
                    F("shares_outstanding_m", "float", "measure", "Millions of shares (for market-cap calculations)"),
                    F("reference_price", "float", "measure", "Long-run anchor price the simulation mean-reverts towards"),
                ],
                "symbol",
                self.symbols,
            ),
            TableDef(
                "dim_meter",
                "Smart meters",
                "Electricity smart meters (residential, commercial, industrial) in eight cities, some with rooftop solar.",
                [
                    F("meter_id", "string", "id", "Meter key"),
                    F("meter_type", "string", "dimension"),
                    F("city", "string", "dimension"),
                    F("country", "string", "dimension"),
                    F("region", "string", "dimension"),
                    F("has_solar", "bool", "dimension"),
                    F("solar_capacity_kw", "float", "measure"),
                    F("tariff_plan", "string", "dimension"),
                    F("install_date", "date"),
                    F("nominal_voltage_v", "int"),
                ],
                "meter_id",
                self.meters,
            ),
            TableDef(
                "dim_warehouse",
                "Warehouses",
                "Fulfilment centres and regional hubs referenced by the inventory CDC stream.",
                [
                    F("warehouse_id", "string", "id", "Warehouse key"),
                    F("warehouse_name", "string"),
                    F("city", "string", "dimension"),
                    F("country", "string", "dimension"),
                    F("region", "string", "dimension"),
                    F("capacity_units", "int", "measure"),
                ],
                "warehouse_id",
                self.warehouses,
            ),
            TableDef(
                "dim_zone",
                "Ride zones",
                "Pickup and drop-off zones for the rides stream (New York, London, Singapore).",
                [
                    F("zone_id", "string", "id", "Zone key"),
                    F("zone_name", "string"),
                    F("city", "string", "dimension"),
                    F("country", "string", "dimension"),
                    F("zone_type", "string", "dimension", "business | residential | nightlife | airport"),
                    F("latitude", "float"),
                    F("longitude", "float"),
                ],
                "zone_id",
                self.zones,
            ),
            TableDef(
                "dim_date",
                "Calendar",
                "Date dimension 2024-2027. Join on date (or date_key = yyyymmdd) to any event_time.",
                [
                    F("date_key", "int", "id", "yyyymmdd"),
                    F("date", "date"),
                    F("year", "int", "dimension"),
                    F("quarter", "int", "dimension"),
                    F("month", "int", "dimension"),
                    F("month_name", "string", "dimension"),
                    F("day", "int"),
                    F("day_of_week", "int", "dimension", "ISO weekday, 1 = Monday"),
                    F("day_name", "string", "dimension"),
                    F("week_of_year", "int", "dimension", "ISO week number"),
                    F("is_weekend", "bool", "dimension"),
                ],
                "date_key",
                self.calendar,
            ),
        ]
