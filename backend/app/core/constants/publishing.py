"""
Publishing constants — metafield definitions, product tags, UOM mapping.

Publishing pipeline constants (Shopify mappings and metafield definitions).
Version: 1.0.0
"""

# Product category GID for "Aircraft Parts & Accessories"
# Full path: Vehicles & Parts > Vehicle Parts & Accessories > Aircraft Parts & Accessories
PRODUCT_CATEGORY_GID: str = "gid://shopify/TaxonomyCategory/vp-1-1"

# Default product tags applied to all published products
PRODUCT_TAGS: list[str] = ["boeing", "aerospace", "Aircraft Parts & Accessories"]

# Metafield namespaces
METAFIELD_NAMESPACE: str = "custom"
METAFIELD_NAMESPACE_BOEING: str = "boeing"

# Allowed domains for trace document URLs
TRACE_ALLOWED_DOMAINS: list[str] = [
    "https://cdn.shopify.com/",
    "https://www.getsmartcert.com/",
]

# UOM mapping: Boeing UOM values -> Shopify choice list values
UOM_MAPPING: dict[str, str] = {
    "EA": "EA",
    "EACH": "EA",
    "IN": "Inches",
    "INCH": "Inches",
    "INCHES": "Inches",
    "LB": "Pound",
    "LBS": "Pound",
    "POUND": "Pound",
    "POUNDS": "Pound",
    "PK": "Pack (1PK = 25EA)",
    "PACK": "Pack (1PK = 25EA)",
    "PC": "EA",
    "PCS": "EA",
    "PIECE": "EA",
    "PIECES": "EA",
    "UNIT": "EA",
    "UNITS": "EA",
}

# Cert mapping: keywords -> Shopify choice list values
CERT_MAPPING: list[tuple[list[str], str]] = [
    (["8130", "FAA"], "FAA 8130-3"),
    (["EASA"], "EASA Form 1"),
    (["BRAZIL", "SEGV"], "Brazil Form SEGV00 003"),
    (["OEM"], "OEM Cert"),
    (["121"], "121 Trace"),
    (["129"], "129 Trace"),
    (["145"], "145 Trace"),
    (["CANADA", "TRANSPORT"], "Transport Canada Form 1"),
    (["C OF C", "COC", "CERTIFICATE OF CONFORMANCE"], "C of C"),
    (["CAA"], "CAA UK"),
]

# Metafield definitions for Shopify product setup
METAFIELD_DEFINITIONS: list[dict[str, str]] = [
    {"namespace": "custom", "key": "part_number", "name": "Part Number", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "alternate_part_number", "name": "Alternate part number", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "dimensions", "name": "Dimensions", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "distribution_source", "name": "Distribution Source", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "unit_of_measure", "name": "Unit of Measure", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "certificate", "name": "Certificate", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "condition", "name": "Condition", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "pma", "name": "PMA", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "country_of_origin", "name": "Country of Origin", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "hazmat_code", "name": "Hazmat Code", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "faa_approval_code", "name": "FAA Approval Code", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "eccn", "name": "ECCN", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "schedule_b_code", "name": "Schedule B Code", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "estimated_lead_time_days", "name": "Estimated Lead Time (Days)", "type": "number_integer"},
    {"namespace": "custom", "key": "trace", "name": "Trace", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "expiration_date", "name": "Expiration Date", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "notes", "name": "Notes", "type": "multi_line_text_field"},
    {"namespace": "custom", "key": "inventory_location_code", "name": "Inventory Location Code", "type": "single_line_text_field"},
    {"namespace": "boeing", "key": "location_summary", "name": "Location Summary", "type": "single_line_text_field"},
    {"namespace": "custom", "key": "manufacturer", "name": "Manufacturer", "type": "single_line_text_field"},
]
