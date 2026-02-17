"""
Product schemas — staging and published product models.
Version: 1.0.0
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class LocationAvailability(BaseModel):
    location: Optional[str] = None
    avail_quantity: Optional[int] = None


class LocationQuantity(BaseModel):
    location: Optional[str] = None
    quantity: Optional[int] = None


class ShopifyProductModel(BaseModel):
    title: Optional[str] = None
    sku: Optional[str] = None
    description: Optional[str] = None
    body_html: Optional[str] = None
    vendor: Optional[str] = None
    manufacturer: Optional[str] = None
    price: Optional[float] = None
    cost_per_item: Optional[float] = None
    currency: Optional[str] = None
    unit_of_measure: Optional[str] = None
    country_of_origin: Optional[str] = None
    length: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    dim_uom: Optional[str] = None
    weight: Optional[float] = None
    weight_uom: Optional[str] = None
    inventory_quantity: Optional[int] = None
    locations: Optional[List[str]] = None
    location_quantities: Optional[List[LocationQuantity]] = None
    location_summary: Optional[str] = None
    product_image: Optional[str] = None
    thumbnail_image: Optional[str] = None
    cert: Optional[str] = None
    image_url: Optional[str] = None
    image_path: Optional[str] = None
    condition: Optional[str] = None
    pma: Optional[bool] = None
    estimated_lead_time_days: Optional[int] = None
    trace: Optional[str] = None
    expiration_date: Optional[str] = None
    notes: Optional[str] = None


class NormalizedProduct(BaseModel):
    # Boeing source fields
    aviall_part_number: Optional[str] = None
    base_uom: Optional[str] = None
    country_of_origin: Optional[str] = None
    description: Optional[str] = None
    dim: Optional[str] = None
    dim_uom: Optional[str] = None
    eccn: Optional[str] = None
    faa_approval_code: Optional[str] = None
    hazmat_code: Optional[str] = None
    in_stock: Optional[bool] = None
    list_price: Optional[float] = None
    location_availabilities: Optional[List[LocationAvailability]] = None
    name: Optional[str] = None
    net_price: Optional[float] = None
    price: Optional[float] = None
    product_image: Optional[str] = None
    quantity: Optional[int] = None
    schedule_b_code: Optional[str] = None
    supplier_name: Optional[str] = None
    thumbnail_image: Optional[str] = None
    weight: Optional[float] = None
    weight_uom: Optional[str] = None
    currency: Optional[str] = None
    base_uom: Optional[str] = None
    boeing_image_url: Optional[str] = None
    boeing_thumbnail_url: Optional[str] = None
    image_url: Optional[str] = None
    image_path: Optional[str] = None

    # Normalized/shopify-friendly fields
    title: Optional[str] = None
    sku: Optional[str] = None
    vendor: Optional[str] = None
    manufacturer: Optional[str] = None
    cost_per_item: Optional[float] = None
    inventory_quantity: Optional[int] = None
    inventory_status: Optional[str] = None
    location_summary: Optional[str] = None
    dim_length: Optional[float] = None
    dim_width: Optional[float] = None
    dim_height: Optional[float] = None
    cert: Optional[str] = None
    condition: Optional[str] = None
    pma: Optional[bool] = None
    estimated_lead_time_days: Optional[int] = None
    trace: Optional[str] = None
    expiration_date: Optional[str] = None
    notes: Optional[str] = None

    shopify: Optional[ShopifyProductModel] = None
    user_id: Optional[str] = None
