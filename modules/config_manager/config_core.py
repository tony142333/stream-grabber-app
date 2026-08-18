import os
import json
from urllib.parse import urlparse
from typing import Dict, Any, Union
from fastapi import APIRouter, HTTPException

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(CONFIG_DIR, "sites_config.json")

class ConfigEngine:
    def __init__(self, config_path: str = CONFIG_FILE):
        self.config_path = config_path
        self.ensure_config_exists()

    def ensure_config_exists(self):
        """Creates default sites_config.json if missing."""
        if not os.path.exists(self.config_path):
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            default_data = {
                "sites": [
                    {
                        "id": "bigcdn",
                        "name": "BigCDN Network",
                        "match_domain": "bigcdn.cc",
                        "engine_mode": "bigcdn"
                    },
                    {
                        "id": "fullhdporn",
                        "name": "FullHDPorn",
                        "match_domain": "fullhdporn.net",
                        "engine_mode": "tamperdev"
                    }
                ]
            }
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(default_data, f, indent=2)

    def load_configs(self) -> dict:
        """Loads site configurations from disk."""
        try:
            if not os.path.exists(self.config_path):
                self.ensure_config_exists()
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return {"sites": data}
                return data
        except Exception as e:
            print(f"[-] Config load error: {e}", flush=True)
            return {"sites": []}

    def save_configs(self, data: Union[dict, list]) -> bool:
        """Persists site configurations to disk."""
        try:
            payload = data if isinstance(data, dict) else {"sites": data}
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            return True
        except Exception as e:
            print(f"[-] Config save error: {e}", flush=True)
            return False

    def get_all_sites(self) -> list:
        return self.load_configs().get("sites", [])

    def save_site(self, site_data: dict) -> bool:
        data = self.load_configs()
        sites = data.get("sites", [])

        site_id = site_data.get("id") or site_data.get("match_domain")
        site_data["id"] = site_id

        updated = False
        for idx, s in enumerate(sites):
            if s.get("id") == site_id or s.get("match_domain") == site_data.get("match_domain"):
                sites[idx] = site_data
                updated = True
                break

        if not updated:
            sites.append(site_data)

        data["sites"] = sites
        return self.save_configs(data)

    def delete_site(self, site_id: str) -> bool:
        data = self.load_configs()
        sites = data.get("sites", [])
        data["sites"] = [s for s in sites if s.get("id") != site_id]
        return self.save_configs(data)

    def match_url(self, target_url: str) -> dict:
        domain = urlparse(target_url).netloc.lower()
        sites = self.get_all_sites()
        for s in sites:
            match_d = s.get("match_domain", "").lower()
            if match_d and (match_d in domain or domain.endswith(match_d)):
                return s

        return {
            "id": "default",
            "name": "Default / Direct Fallback",
            "match_domain": domain,
            "engine_mode": "bigcdn"
        }

# ---------------------------------------------------------------------------
# FASTAPI ROUTER (Mounted under /api in server.py)
# ---------------------------------------------------------------------------
config_router = APIRouter(tags=["configs"])
engine = ConfigEngine()

# Resolves to /api/configs, /api/configs/, /api/config, /api/config/
@config_router.get("/configs")
@config_router.get("/configs/")
@config_router.get("/config")
@config_router.get("/config/")
def get_configs():
    return engine.load_configs()

@config_router.post("/configs")
@config_router.post("/configs/")
@config_router.post("/config")
@config_router.post("/config/")
def add_or_update_config(site: Dict[str, Any]):
    if "sites" in site and isinstance(site["sites"], list):
        success = engine.save_configs(site)
    else:
        success = engine.save_site(site)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to save configuration")
    return engine.load_configs()

@config_router.delete("/configs/{site_id}")
@config_router.delete("/configs/{site_id}/")
@config_router.delete("/config/{site_id}")
@config_router.delete("/config/{site_id}/")
def delete_config(site_id: str):
    success = engine.delete_site(site_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete configuration")
    return engine.load_configs()