import os
import json
from urllib.parse import urlparse

class ConfigEngine:
    def __init__(self, config_path: str = None):
        if config_path:
            self.config_path = config_path
        else:
            # Anchor path directly to this module's directory
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.config_path = os.path.join(base_dir, "sites_config.json")

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
        """Loads and returns all site configurations."""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[-] Config load error: {e}", flush=True)
            return {"sites": []}

    def save_configs(self, data: dict) -> bool:
        """Persists site configurations to disk."""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            print(f"[-] Config save error: {e}", flush=True)
            return False

    def get_all_sites(self) -> list:
        """Returns list of all configured site profiles."""
        return self.load_configs().get("sites", [])

    def save_site(self, site_data: dict) -> bool:
        """Adds or updates a site profile by ID or match_domain."""
        data = self.load_configs()
        sites = data.get("sites", [])

        site_id = site_data.get("id") or site_data.get("match_domain")
        site_data["id"] = site_id

        # Update existing or append new
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
        """Removes a site profile by ID."""
        data = self.load_configs()
        sites = data.get("sites", [])
        data["sites"] = [s for s in sites if s.get("id") != site_id]
        return self.save_configs(data)

    def match_url(self, target_url: str) -> dict:
        """Matches a target URL against configured site domains."""
        domain = urlparse(target_url).netloc.lower()
        # Strip subdomains (e.g., s6.bigcdn.cc -> bigcdn.cc, www.fullhdporn.net -> fullhdporn.net)
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