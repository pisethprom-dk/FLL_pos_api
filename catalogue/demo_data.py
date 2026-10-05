# v1.0.0 — demo catalogue for a tool shop, loaded by `manage.py seed_demo`.
#
# Products the mockup shows (TL-0101, TL-0118, FX-0302, SV-0001 and the rest)
# keep its codes, prices and shelves, so the API and the design agree.
#
# Code numbers are unique across prefixes — TL-01xx power tools, TL-02xx hand
# tools, FX-03xx fixings, SF-04xx safety, AC-05xx accessories, GD-06xx garden,
# EL-07xx electrical, SV-00xx services — and the demo barcode is built from
# that number. Model numbers are given only where they are the real
# manufacturer's code.
from typing import NamedTuple

# (code, name, name_kh). The first seven are the ones `seed` makes.
UNITS = [
    ("PCS", "Piece", "ដុំ"),
    ("SET", "Set", "ឈុត"),
    ("BOX", "Box", "ប្រអប់"),
    ("PAIR", "Pair", "គូ"),
    ("M", "Metre", "ម៉ែត្រ"),
    ("ROLL", "Roll", "រមូរ"),
    ("CTN", "Carton", "កេស"),
    ("BX100", "Box of 100", "ប្រអប់ ១០០"),
    ("KG", "Kilogram", "គីឡូក្រាម"),
    ("PACK", "Pack", "កញ្ចប់"),
    ("HR", "Hour", "ម៉ោង"),
]

# (name, country, display_order)
BRANDS = [
    ("Makita", "Japan", 1),
    ("Bosch", "Germany", 2),
    ("Total", "China", 3),
    ("Stanley", "United States", 4),
    ("Ingco", "China", 5),
    ("DeWalt", "United States", 6),
    ("3M", "United States", 7),
    ("Unbranded", "", 9),
]

# (code, name, name_kh, parent code, display_order). Parents come first.
CATEGORIES = [
    ("CAT-01", "Power tools", "ឧបករណ៍អគ្គិសនី", None, 1),
    ("CAT-02", "Hand tools", "ឧបករណ៍ដៃ", None, 2),
    ("CAT-03", "Fixings", "ដែកគោល និងខ្ចៅ", None, 3),
    ("CAT-04", "Safety wear", "សម្ភារៈសុវត្ថិភាព", None, 4),
    ("CAT-05", "Services", "សេវាកម្ម", None, 5),
    ("CAT-06", "Power tool accessories", "គ្រឿងបន្ថែមម៉ាស៊ីន", None, 6),
    ("CAT-07", "Garden tools", "ឧបករណ៍សួនច្បារ", None, 7),
    ("CAT-08", "Electrical", "គ្រឿងអគ្គិសនី", None, 8),

    ("CAT-0101", "Drills", "ម៉ាស៊ីនស្វាន", "CAT-01", 1),
    ("CAT-0102", "Grinders", "ម៉ាស៊ីនកិន", "CAT-01", 2),
    ("CAT-0103", "Saws", "ម៉ាស៊ីនអារ", "CAT-01", 3),
    ("CAT-0104", "Rotary hammers", "ម៉ាស៊ីនស្វានបេតុង", "CAT-01", 4),
    ("CAT-0105", "Sanders & polishers", "ម៉ាស៊ីនខាត់", "CAT-01", 5),
    ("CAT-0106", "Welding machines", "ម៉ាស៊ីនផ្សារ", "CAT-01", 6),

    ("CAT-0201", "Wrenches", "ក្លេ", "CAT-02", 1),
    ("CAT-0202", "Hammers", "ញញួរ", "CAT-02", 2),
    ("CAT-0203", "Screwdrivers", "ទួណឺវីស", "CAT-02", 3),
    ("CAT-0204", "Pliers & cutters", "ដង្កាប់ និងកន្ត្រៃ", "CAT-02", 4),
    ("CAT-0205", "Measuring & marking", "ឧបករណ៍វាស់", "CAT-02", 5),
    ("CAT-0206", "Tool sets & storage", "ឈុតឧបករណ៍ និងប្រអប់", "CAT-02", 6),

    ("CAT-0601", "Drill bits", "មុខស្វាន", "CAT-06", 1),
    ("CAT-0602", "Discs & wheels", "ថាសកាត់ និងថាសកិន", "CAT-06", 2),
    ("CAT-0603", "Saw blades", "ផ្លែរណារ", "CAT-06", 3),
]


class P(NamedTuple):
    code: str
    name: str
    brand: str  # brand name, or "" for none
    unit: str  # unit code
    retail: str
    wholesale: str
    model_no: str = ""
    warranty: int = 0  # months
    shelf: str = ""
    reorder: int = 0
    reorder_qty: int = 0
    fixed: bool = False  # is_price_fixed — no discounting
    track: bool = True  # track_stock — off for services


# Keyed by category code.
PRODUCTS = {
    "CAT-0101": [  # Power tools → Drills
        P("TL-0101", "Impact drill 13mm 710W", "Bosch", "PCS", "78.00", "69.00",
          model_no="GSB 550", warranty=12, shelf="A1-1", reorder=6, reorder_qty=6),
        P("TL-0102", "Impact drill 13mm 680W", "Total", "PCS", "29.00", "25.50",
          warranty=6, shelf="A1-1", reorder=6, reorder_qty=10),
        P("TL-0104", "Electric drill 10mm 450W", "Total", "PCS", "18.50", "16.00",
          warranty=6, shelf="A1-1", reorder=6, reorder_qty=10),
        P("TL-0106", "Impact drill 13mm 710W", "Makita", "PCS", "72.00", "64.00",
          model_no="HP1630", warranty=12, shelf="A1-1", reorder=3, reorder_qty=4),
        P("TL-0150", "Cordless driver 12V", "Makita", "PCS", "92.00", "84.00",
          model_no="DF333D", warranty=12, shelf="A1-3", reorder=4, reorder_qty=4,
          fixed=True),
        P("TL-0151", "Cordless drill 20V, 2 batteries", "Total", "SET", "69.00", "61.00",
          warranty=6, shelf="A1-3", reorder=4, reorder_qty=6),
        P("TL-0152", "Cordless impact driver 20V", "Total", "PCS", "75.00", "67.00",
          warranty=6, shelf="A1-3", reorder=2, reorder_qty=4),
        P("TL-0153", "Cordless drill 18V", "DeWalt", "PCS", "145.00", "132.00",
          model_no="DCD771", warranty=12, shelf="A1-3", reorder=2, reorder_qty=2,
          fixed=True),
    ],
    "CAT-0104": [  # Power tools → Rotary hammers
        P("TL-0103", "Rotary hammer 24mm", "Bosch", "PCS", "165.00", "148.00",
          model_no="GBH 2-24 DRE", warranty=12, shelf="A1-2", reorder=6, reorder_qty=4),
        P("TL-0107", "Rotary hammer 26mm 800W", "Total", "PCS", "62.00", "55.00",
          warranty=6, shelf="A1-2", reorder=3, reorder_qty=4),
        P("TL-0108", "Rotary hammer 24mm 780W", "Makita", "PCS", "145.00", "130.00",
          model_no="HR2470", warranty=12, shelf="A1-2", reorder=2, reorder_qty=2),
    ],
    "CAT-0102": [  # Power tools → Grinders
        P("TL-0118", "Angle grinder 100mm 570W", "Makita", "PCS", "59.00", "52.00",
          model_no="M0910B", warranty=6, shelf="A2-1", reorder=8, reorder_qty=6),
        P("TL-0115", "Angle grinder 100mm 750W", "Total", "PCS", "22.00", "19.50",
          warranty=6, shelf="A2-1", reorder=10, reorder_qty=12),
        P("TL-0116", "Angle grinder 100mm 670W", "Bosch", "PCS", "52.00", "46.00",
          model_no="GWS 060", warranty=12, shelf="A2-1", reorder=6, reorder_qty=6),
        P("TL-0117", "Angle grinder 125mm 950W", "Ingco", "PCS", "34.00", "30.00",
          warranty=6, shelf="A2-2", reorder=4, reorder_qty=6),
        P("TL-0119", "Cordless angle grinder 20V", "Total", "PCS", "65.00", "58.00",
          warranty=6, shelf="A2-2", reorder=2, reorder_qty=4),
        P("TL-0120", "Bench grinder 150mm 150W", "Total", "PCS", "39.00", "34.00",
          warranty=6, shelf="A2-3", reorder=2, reorder_qty=2),
    ],
    "CAT-0103": [  # Power tools → Saws
        P("TL-0130", "Circular saw 185mm 1400W", "Total", "PCS", "55.00", "49.00",
          warranty=6, shelf="A3-1", reorder=2, reorder_qty=4),
        P("TL-0131", "Jigsaw 570W", "Total", "PCS", "32.00", "28.00",
          warranty=6, shelf="A3-1", reorder=2, reorder_qty=4),
        P("TL-0132", "Cut-off saw 355mm 2000W", "Total", "PCS", "115.00", "102.00",
          warranty=6, shelf="A3-2", reorder=1, reorder_qty=2),
        P("TL-0133", "Circular saw 185mm 1050W", "Makita", "PCS", "98.00", "88.00",
          model_no="5806B", warranty=12, shelf="A3-1", reorder=1, reorder_qty=2),
        P("TL-0134", "Marble cutter 110mm", "Makita", "PCS", "75.00", "67.00",
          model_no="4100NH", warranty=12, shelf="A3-2", reorder=2, reorder_qty=2),
    ],
    "CAT-0105": [  # Power tools → Sanders & polishers
        P("TL-0140", "Orbital sander 240W", "Total", "PCS", "28.00", "24.50",
          warranty=6, shelf="A4-1", reorder=2, reorder_qty=3),
        P("TL-0141", "Polisher 180mm 1400W", "Total", "PCS", "58.00", "51.00",
          warranty=6, shelf="A4-1", reorder=1, reorder_qty=2),
        P("TL-0142", "Heat gun 2000W", "Total", "PCS", "19.00", "16.50",
          warranty=6, shelf="A4-2", reorder=3, reorder_qty=5),
    ],
    "CAT-0106": [  # Power tools → Welding machines
        P("TL-0160", "Inverter welder 160A", "Total", "PCS", "89.00", "79.00",
          warranty=12, shelf="A5-1", reorder=2, reorder_qty=2),
    ],
    "CAT-0201": [  # Hand tools → Wrenches
        P("TL-0236", "Combination spanner 8mm", "Total", "PCS", "1.60", "1.35",
          shelf="B1-1", reorder=24, reorder_qty=48),
        P("TL-0238", "Combination spanner 10mm", "Total", "PCS", "1.90", "1.60",
          shelf="B1-1", reorder=24, reorder_qty=48),
        P("TL-0240", "Combination spanner 12mm", "Total", "PCS", "3.20", "2.70",
          shelf="B1-2", reorder=24, reorder_qty=48),
        P("TL-0241", "Combination spanner set, 14 piece", "Total", "SET", "29.00", "25.50",
          shelf="B1-3", reorder=4, reorder_qty=6),
        P("TL-0244", "Adjustable wrench 250mm", "Total", "PCS", "6.50", "5.60",
          shelf="B2-1", reorder=6, reorder_qty=12),
        P("TL-0245", "Adjustable wrench 300mm", "Stanley", "PCS", "12.50", "11.00",
          shelf="B2-1", reorder=4, reorder_qty=6),
        P("TL-0246", 'Pipe wrench 18"', "Total", "PCS", "11.00", "9.50",
          shelf="B2-2", reorder=3, reorder_qty=6),
        P("TL-0247", 'Socket set 1/2", 24 piece', "Total", "SET", "32.00", "28.00",
          shelf="B2-3", reorder=2, reorder_qty=4),
        P("TL-0248", "Hex key set, 9 piece", "Ingco", "SET", "4.50", "3.80",
          shelf="B2-3", reorder=6, reorder_qty=12),
    ],
    "CAT-0202": [  # Hand tools → Hammers
        P("TL-0250", "Claw hammer 450g", "Total", "PCS", "5.50", "4.70",
          shelf="B3-1", reorder=6, reorder_qty=12),
        P("TL-0251", "Claw hammer 570g", "Stanley", "PCS", "14.00", "12.20",
          shelf="B3-1", reorder=3, reorder_qty=6),
        P("TL-0252", "Rubber mallet 450g", "Total", "PCS", "4.80", "4.10",
          shelf="B3-1", reorder=4, reorder_qty=8),
        P("TL-0253", "Sledge hammer 2kg", "Total", "PCS", "11.50", "10.00",
          shelf="B3-2", reorder=2, reorder_qty=4),
    ],
    "CAT-0203": [  # Hand tools → Screwdrivers
        P("TL-0260", "Screwdriver set, 6 piece", "Total", "SET", "6.50", "5.50",
          shelf="B4-1", reorder=6, reorder_qty=12),
        P("TL-0261", "Phillips screwdriver PH2 × 150mm", "Stanley", "PCS", "3.60", "3.10",
          shelf="B4-1", reorder=6, reorder_qty=12),
        P("TL-0262", "Flat screwdriver 6 × 150mm", "Total", "PCS", "1.80", "1.50",
          shelf="B4-1", reorder=10, reorder_qty=20),
        P("TL-0263", "Voltage tester screwdriver", "Total", "PCS", "1.20", "1.00",
          shelf="B4-2", reorder=12, reorder_qty=24),
    ],
    "CAT-0204": [  # Hand tools → Pliers & cutters
        P("TL-0270", "Combination pliers 200mm", "Total", "PCS", "4.20", "3.60",
          shelf="B5-1", reorder=8, reorder_qty=12),
        P("TL-0271", "Long nose pliers 160mm", "Total", "PCS", "3.80", "3.20",
          shelf="B5-1", reorder=6, reorder_qty=12),
        P("TL-0272", "Diagonal cutting pliers 160mm", "Ingco", "PCS", "3.90", "3.30",
          shelf="B5-1", reorder=6, reorder_qty=12),
        P("TL-0273", "Bolt cutter 600mm", "Total", "PCS", "16.00", "14.00",
          shelf="B5-2", reorder=2, reorder_qty=4),
        P("TL-0274", "Locking pliers 250mm", "Stanley", "PCS", "9.50", "8.30",
          shelf="B5-2", reorder=3, reorder_qty=6),
    ],
    "CAT-0205": [  # Hand tools → Measuring & marking
        P("TL-0280", "Tape measure 5m", "Stanley", "PCS", "6.50", "5.60",
          shelf="B6-1", reorder=12, reorder_qty=24),
        P("TL-0281", "Tape measure 7.5m", "Total", "PCS", "4.80", "4.10",
          shelf="B6-1", reorder=10, reorder_qty=20),
        P("TL-0282", "Spirit level 600mm", "Total", "PCS", "5.50", "4.70",
          shelf="B6-2", reorder=4, reorder_qty=8),
        P("TL-0283", "Laser distance meter 40m", "Total", "PCS", "35.00", "31.00",
          warranty=6, shelf="B6-2", reorder=2, reorder_qty=2),
    ],
    "CAT-0206": [  # Hand tools → Tool sets & storage
        P("TL-0290", 'Tool box 19"', "Stanley", "PCS", "16.00", "14.00",
          shelf="B7-1", reorder=2, reorder_qty=4),
        P("TL-0291", "Household tool set, 25 piece", "Total", "SET", "24.00", "21.00",
          shelf="B7-1", reorder=2, reorder_qty=4),
    ],
    "CAT-03": [  # Fixings
        P("FX-0301", "Wood screw 4×25mm", "Unbranded", "BX100", "1.20", "1.00",
          shelf="C1-3", reorder=20, reorder_qty=40),
        P("FX-0302", "Wood screw 4×40mm", "Unbranded", "BX100", "2.00", "1.70",
          shelf="C1-4", reorder=20, reorder_qty=40),
        P("FX-0303", "Roofing screw 12×50mm", "Unbranded", "BX100", "4.50", "3.90",
          shelf="C1-5", reorder=10, reorder_qty=20),
        P("FX-0310", "Common nails 50mm", "Unbranded", "KG", "1.40", "1.20",
          shelf="C2-1", reorder=20, reorder_qty=50),
        P("FX-0311", "Concrete nails 75mm", "Unbranded", "KG", "2.20", "1.90",
          shelf="C2-1", reorder=10, reorder_qty=25),
        P("FX-0320", "Wall plugs 8mm, pack of 100", "Unbranded", "PACK", "1.50", "1.25",
          shelf="C3-1", reorder=10, reorder_qty=20),
        P("FX-0321", "Anchor bolt M10 × 100mm", "Unbranded", "PCS", "0.35", "0.28",
          shelf="C3-2", reorder=50, reorder_qty=200),
        P("FX-0330", "Hex bolt and nut M12 × 50mm", "Unbranded", "PCS", "0.30", "0.24",
          shelf="C3-3", reorder=50, reorder_qty=200),
    ],
    "CAT-04": [  # Safety wear
        P("SF-0401", "Safety goggles, clear", "Unbranded", "PCS", "1.80", "1.50",
          shelf="D2-1", reorder=12, reorder_qty=24),
        P("SF-0402", "Work gloves, cotton", "Unbranded", "PAIR", "0.60", "0.48",
          shelf="D1-1", reorder=24, reorder_qty=60),
        P("SF-0403", "Leather welding gloves", "Total", "PAIR", "4.20", "3.60",
          shelf="D1-1", reorder=6, reorder_qty=12),
        P("SF-0404", "Safety helmet, white", "Total", "PCS", "3.50", "3.00",
          shelf="D1-2", reorder=6, reorder_qty=12),
        P("SF-0405", "Dust mask N95, box of 20", "3M", "BOX", "22.00", "19.50",
          model_no="8210", shelf="D2-2", reorder=2, reorder_qty=4),
        P("SF-0406", "Auto-darkening welding helmet", "Total", "PCS", "18.00", "15.50",
          warranty=6, shelf="D1-3", reorder=2, reorder_qty=4),
    ],
    "CAT-0601": [  # Power tool accessories → Drill bits
        P("AC-0501", "HSS drill bit set, 13 piece", "Total", "SET", "7.50", "6.40",
          shelf="E1-1", reorder=4, reorder_qty=8),
        P("AC-0502", "Masonry drill bit 8mm", "Bosch", "PCS", "1.60", "1.35",
          shelf="E1-2", reorder=10, reorder_qty=20),
        P("AC-0503", "SDS-plus drill bit 10 × 160mm", "Bosch", "PCS", "2.80", "2.40",
          shelf="E1-2", reorder=10, reorder_qty=20),
    ],
    "CAT-0602": [  # Power tool accessories → Discs & wheels
        P("AC-0510", "Cutting disc 105mm, metal", "Total", "PCS", "0.45", "0.36",
          shelf="E2-1", reorder=50, reorder_qty=200),
        P("AC-0511", "Grinding disc 100mm", "Total", "PCS", "0.70", "0.58",
          shelf="E2-1", reorder=25, reorder_qty=100),
        P("AC-0512", "Diamond blade 105mm", "Total", "PCS", "4.50", "3.80",
          shelf="E2-2", reorder=6, reorder_qty=12),
    ],
    "CAT-0603": [  # Power tool accessories → Saw blades
        P("AC-0520", "TCT saw blade 185mm, 40 teeth", "Total", "PCS", "6.80", "5.80",
          shelf="E3-1", reorder=4, reorder_qty=8),
        P("AC-0521", "Jigsaw blades, pack of 5", "Bosch", "PACK", "4.20", "3.60",
          shelf="E3-1", reorder=4, reorder_qty=8),
    ],
    "CAT-07": [  # Garden tools
        P("GD-0601", "Garden hoe", "Unbranded", "PCS", "4.50", "3.80",
          shelf="F1-1", reorder=4, reorder_qty=8),
        P("GD-0602", "Pruning shears 200mm", "Total", "PCS", "4.20", "3.60",
          shelf="F1-1", reorder=4, reorder_qty=8),
        P("GD-0603", "Garden hose 15m", "Total", "ROLL", "9.50", "8.20",
          shelf="F1-2", reorder=2, reorder_qty=4),
        P("GD-0604", "High-pressure washer 1400W", "Total", "PCS", "85.00", "76.00",
          warranty=6, shelf="F2-1", reorder=1, reorder_qty=2),
    ],
    "CAT-08": [  # Electrical
        P("EL-0701", "Extension reel, 4 sockets, 10m", "Total", "PCS", "9.80", "8.50",
          shelf="G1-1", reorder=4, reorder_qty=8),
        P("EL-0702", "PVC insulating tape, black", "Unbranded", "ROLL", "0.40", "0.30",
          shelf="G1-2", reorder=24, reorder_qty=100),
        P("EL-0703", "Electric cable 2 × 1.5mm²", "Unbranded", "M", "0.55", "0.45",
          shelf="G1-3", reorder=50, reorder_qty=100),
        P("EL-0704", "Digital multimeter", "Total", "PCS", "12.50", "10.80",
          warranty=6, shelf="G1-4", reorder=2, reorder_qty=4),
    ],
    "CAT-05": [  # Services — no quantity, so no stock and no reorder level
        P("SV-0001", "Key cutting", "", "PCS", "1.50", "1.50", track=False),
        P("SV-0002", "Drill bit sharpening", "", "PCS", "1.00", "1.00", track=False),
        P("SV-0003", "Power tool repair, labour", "", "HR", "5.00", "5.00", track=False),
    ],
}
