import os
import json
import re
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(CONFIG_DIR, "sites_config.json")

config_router = APIRouter()

class SiteConfigModel(BaseModel):
    id: str = Field(..., description="Unique alphanumeric URL slug ID")
    name: str
    match_domain: str
    example_url: str
    engine_mode: str = "standard"  # 'standard' or 'tamperdev'
    sniff_keywords: list[str] = []
    title_mode: str = "page_url"   # 'page_url' or 'html_tag'
    title_pattern: str = ""
    quality_preference: list[str] = ["1080", "720", "480"]
    click_selectors: list[str] = []

class ConfigListResponse(BaseModel):
    sites: list[SiteConfigModel]


class ConfigEngine:
    def __init__(self):
        self.config_path = CONFIG_FILE
        self.default_config = {
            "id": "default_fallback",
            "name": "Global Fallback Handler",
            "match_domain": "*",
            "example_url": "",
            "engine_mode": "standard",
            "sniff_keywords": [".mp4", ".m3u8", "video", "stream"],
            "title_mode": "page_url",
            "title_pattern": "",
            "quality_preference": ["2160", "4k", "1440", "1080", "720", "480", "360"],
            "click_selectors": ["video", "button"]
        }

    def _load_raw(self) -> dict:
        if not os.path.exists(self.config_path):
            return {"sites": []}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"sites": []}

    def match_url(self, url: str) -> dict:
        data = self._load_raw()
        for site in data.get("sites", []):
            domain = site.get("match_domain", "").strip().lower()
            if domain and domain in url.lower():
                return site
        return self.default_config


def _write_config_raw(data: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

@config_router.get("/configs", response_model=ConfigListResponse)
def get_configs():
    if not os.path.exists(CONFIG_FILE):
        return {"sites": []}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

@config_router.post("/configs")
def save_or_update_config(site: SiteConfigModel):
    data = {"sites": []}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass

    existing_index = next((i for i, item in enumerate(data["sites"]) if item["id"] == site.id), None)

    if existing_index is not None:
        data["sites"][existing_index] = site.model_dump()
    else:
        data["sites"].append(site.model_dump())

    _write_config_raw(data)
    return {"status": "success", "message": f"Saved configuration profile: {site.name}"}

@config_router.delete("/configs/{site_id}")
def delete_config(site_id: str):
    if not os.path.exists(CONFIG_FILE):
        raise HTTPException(status_code=404, detail="Configuration store not found")

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    initial_len = len(data["sites"])
    data["sites"] = [item for item in data["sites"] if item["id"] != site_id]

    if len(data["sites"]) == initial_len:
        raise HTTPException(status_code=404, detail="Configuration profile ID not found")

    _write_config_raw(data)
    return {"status": "success", "message": f"Deleted configuration profile: {site_id}"}