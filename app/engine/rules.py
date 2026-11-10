"""Official Alliance rules and dynamic active rules generator."""
from typing import Dict, Any
from app.i18n.translator import translate


def get_official_rulebook(lang: str = "en") -> Dict[str, Any]:
    """Return the authoritative standardized game rules in requested language."""
    return {
        "title": translate("rules_title", lang),
        "sections": [
            {
                "id": "objective",
                "title": translate("rules_objective_title", lang),
                "text": translate("rules_objective_text", lang)
            },
            {
                "id": "economy",
                "title": translate("rules_economy_title", lang),
                "text": translate("rules_economy_text", lang)
            },
            {
                "id": "actions",
                "title": translate("rules_actions_title", lang),
                "subsections": [
                    translate("rules_actions_move", lang),
                    translate("rules_actions_attack", lang),
                    translate("rules_actions_give", lang),
                    translate("rules_actions_upgrade", lang)
                ]
            },
            {
                "id": "burst",
                "title": translate("rules_burst_title", lang),
                "text": translate("rules_burst_text", lang)
            },
            {
                "id": "range",
                "title": translate("rules_range_title", lang),
                "text": translate("rules_range_text", lang)
            },
            {
                "id": "fow",
                "title": translate("rules_fow_title", lang),
                "text": translate("rules_fow_text", lang)
            }
        ]
    }


def generate_active_rules_summary(cfg: Dict[str, Any], lang: str = "en") -> Dict[str, Any]:
    """Generate dynamic summary of rules tailored to active lobby parameters."""
    return {
        "board_size": f"{cfg.get('board_width', 15)}x{cfg.get('board_height', 15)}",
        "starting_hp": cfg.get("starting_hp", 3),
        "starting_ap": cfg.get("starting_ap", 0),
        "burst_cap_ap": cfg.get("burst_cap_ap", 3),
        "ap_drop_schedule": f"{cfg.get('ap_drop_server_time', '22:00')} (Server Time)",
        "ap_drop_amount": cfg.get("ap_drop_amount", 1),
        "move_cost_ap": cfg.get("move_cost_ap", 1),
        "attack_cost_ap": cfg.get("attack_cost_ap", 1),
        "give_ap_cost_ap": cfg.get("give_ap_cost_ap", 0),
        "range_upgrade_base": cfg.get("range_upgrade_cost_base", 2),
        "active_win_conditions": {
            "last_man_standing": bool(cfg.get("win_cond_last_man", 1)),
            "armistice": bool(cfg.get("win_cond_armistice", 1)),
            "armistice_threshold": f"{int(float(cfg.get('armistice_threshold', 1.0)) * 100)}%",
            "rogue_traitor_rule": bool(cfg.get("win_cond_rogue", 1)),
            "rogue_mode": cfg.get("rogue_resolution_mode", "GRANT_AP"),
            "stagnation_timeout": bool(cfg.get("win_cond_stagnation", 0)),
            "stagnation_limit_days": cfg.get("stagnation_days_limit", 5),
            "stagnation_outcome": cfg.get("stagnation_mode", "DRAW")
        },
        "fog_of_war_stats": bool(cfg.get("fog_of_war_stats", 1)),
        "reveal_killer_on_death": bool(cfg.get("reveal_killer_on_death", 0)),
        "allow_dead_chat": bool(cfg.get("allow_dead_chat", 1))
    }
