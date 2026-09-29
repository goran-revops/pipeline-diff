"""Invented companies, products, and reps for demo and seed data. Every domain is under example.com, .org, or .net."""

import random

COMPANIES = [
    "Brightpath Logistics", "Cobalt Ridge Health", "Juniper Freight", "Ironvale Energy", "Lumen Harbor", "Quarry Lane Foods",
    "Silverline Dental", "Tidewater Labs", "Oakmont Retail", "Pinecrest Insurance", "Redstone Media", "Bluefin Systems",
    "Summit Arc Software", "Harborview Clinics", "Maplewood Schools", "Crescent Parcel", "Evergreen Grid", "Falcon Peak Security",
    "Granite Bay Credit", "Hollow Creek Farms", "Kestrel Robotics", "Larkspur Travel", "Meridian Tile", "Nimbus Payroll",
    "Orchard Street Legal", "Prairie Wind Power", "Quillstone Publishing", "Riverbend Hospitality", "Stonebridge Capital",
    "Tallgrass Telecom", "Upland Outdoor", "Vantage Point Realty", "Westfield Mills", "Yellowpine Studios", "Zephyr Air Cargo",
    "Amberline Pharmacy", "Birchwood Analytics", "Canyon Forge", "Driftwood Marine", "Elmstead Clinics", "Foxglove Cosmetics",
    "Glacier Point Water", "Hearthstone Homes", "Indigo Loom", "Jetstream Couriers", "Keystone Fabrication", "Lighthouse Learning",
    "Mosaic Health Partners", "Northgate Storage", "Obsidian Networks",
]
FIRST = ["Alder", "Ashford", "Beacon", "Blue Ridge", "Brookline", "Cedar", "Clearwater", "Copper", "Crestview", "Delta",
         "Eastgate", "Elm", "Fairview", "Fernwood", "Golden", "Greenfield", "Highland", "Horizon", "Iron", "Juniper", "Lakeside",
         "Linden", "Madison", "Maple", "Northfield", "Oak", "Pacific", "Pine", "Quarry", "Redwood", "Ridgeway", "Riverside",
         "Sandstone", "Silver", "Southport", "Sterling", "Stonewall", "Summit", "Westbrook", "Willow"]
SECOND = ["Analytics", "Bank", "Biotech", "Builders", "Capital", "Clinics", "Consulting", "Dental", "Distribution", "Energy",
          "Engineering", "Foods", "Freight", "Group", "Health", "Hotels", "Insurance", "Labs", "Legal", "Logistics", "Manufacturing",
          "Media", "Motors", "Partners", "Pharma", "Realty", "Retail", "Robotics", "Software", "Systems", "Telecom", "Travel"]
PRODUCTS = ["Platform", "Analytics add-on", "Enterprise plan", "Support plan", "Data sync", "Pilot", "Expansion", "Onboarding"]
REPS = [
    ("Ava Reyes", "ava.reyes"), ("Marcus Chen", "marcus.chen"), ("Priya Nair", "priya.nair"), ("Diego Alvarez", "diego.alvarez"),
    ("Hannah Brooks", "hannah.brooks"), ("Tom Okafor", "tom.okafor"), ("Lena Fischer", "lena.fischer"), ("Sam Whitaker", "sam.whitaker"),
    ("Grace Liu", "grace.liu"), ("Omar Haddad", "omar.haddad"), ("Nora Lindqvist", "nora.lindqvist"), ("Ben Castillo", "ben.castillo"),
    ("Aisha Mensah", "aisha.mensah"), ("Jonah Weiss", "jonah.weiss"), ("Carmen Ruiz", "carmen.ruiz"), ("Felix Moreau", "felix.moreau"),
    ("Keiko Tanaka", "keiko.tanaka"), ("Ryan Doyle", "ryan.doyle"), ("Zara Patel", "zara.patel"), ("Owen Price", "owen.price"),
    ("Mei Wong", "mei.wong"), ("Luca Bianchi", "luca.bianchi"), ("Ines Duarte", "ines.duarte"), ("Kofi Asante", "kofi.asante"),
]
TLDS = ("example.com", "example.org", "example.net")


def domain(company):
    slug = "".join(ch for ch in company.lower().replace(" ", "-") if ch.isalnum() or ch == "-")
    return f"{slug}.{TLDS[sum(map(ord, company)) % len(TLDS)]}"


def reps(count=8):
    if not 1 <= count <= len(REPS):
        raise SystemExit(f"Pick between 1 and {len(REPS)} reps.")
    return [{"id": f"rep{i + 1}", "name": name, "email": f"{handle}@example.com"} for i, (name, handle) in enumerate(REPS[:count])]


def company(rng: random.Random):
    """One of about 1,300 invented companies."""
    return rng.choice(COMPANIES) if rng.random() < 0.3 else f"{rng.choice(FIRST)} {rng.choice(SECOND)}"


def deal_name(rng: random.Random):
    return f"{company(rng)} - {rng.choice(PRODUCTS)}"


def amount(rng: random.Random):
    """Mostly mid-market sizes with a few large deals, rounded to 500."""
    base = rng.choice([5_000, 8_000, 12_000, 18_000, 25_000, 35_000, 50_000, 75_000, 120_000])
    return float(round(base * rng.uniform(0.7, 1.4) / 500) * 500)
