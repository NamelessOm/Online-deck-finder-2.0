import re
import time
import pandas as pd
import requests
import streamlit as st
from streamlit_searchbox import st_searchbox

st.set_page_config(page_title="Limitless Card Tournament Finder", layout="wide")

LIMITLESS_API = "https://play.limitlesstcg.com/api"
TCGDEX_API = "https://api.tcgdex.net/v2/en"

# Official English / Limitless / PTCGL set codes mapped from TCGdex set IDs
TCGDEX_TO_ENGLISH_SET_CODE = {
    # Mega Evolution / 2025-2026 Era
    "me01": "MEG",
    "me02": "PFL",
    "me02.5": "ASC",
    "me03": "POR",
    "me04": "DLR",
    "mep": "MEP",
    # Scarlet & Violet Era
    "sv01": "SVI",
    "sv02": "PAL",
    "sv03": "OBF",
    "sv03.5": "MEW",
    "sv04": "PAR",
    "sv04.5": "PAF",
    "sv05": "TEF",
    "sv06": "TWM",
    "sv06.5": "SFA",
    "sv07": "SCR",
    "sv08": "SSP",
    "sv08.5": "PRE",
    "sv09": "JTG",
    "sv10": "DRI",
    "sv10.5b": "BLK",
    "sv10.5w": "WHT",
    "svp": "SVP",
    # Sword & Shield Era
    "swsh1": "SSH",
    "swsh2": "RCL",
    "swsh3": "DAA",
    "swsh3.5": "CPA",
    "swsh4": "VIV",
    "swsh4.5": "SHF",
    "swsh5": "BST",
    "swsh6": "CRE",
    "swsh7": "EVS",
    "swsh8": "FST",
    "swsh9": "BRS",
    "swsh10": "ASR",
    "swsh10.5": "PGO",
    "swsh11": "LOR",
    "swsh12": "SIT",
    "swsh12.5": "CRZ",
    "swshp": "SWSHP",
    "cel25": "CEL",
    # Sun & Moon Era
    "sm1": "SUM",
    "sm2": "GRI",
    "sm3": "BUS",
    "sm3.5": "SLG",
    "sm4": "CIN",
    "sm5": "UPR",
    "sm6": "FLI",
    "sm7": "CES",
    "sm7.5": "DRM",
    "sm8": "LOT",
    "sm9": "TEU",
    "sm10": "UNB",
    "sm11": "UNM",
    "sm115": "HIF",
    "sm12": "CEC",
    "smp": "SMP",
}

ENGLISH_PROMO_SET_CODES = {"MEP", "SVP", "SWSHP", "SMP", "XYSP", "BWP", "DPP", "HGP", "WSP"}


def _is_non_english_set(set_id: str, set_name: str = "") -> bool:
    """Rejects Japanese/Pocket/non-English sets that occasionally appear in TCGdex."""
    sid = (set_id or "").strip().lower()
    sname = (set_name or "").strip().lower()

    # Reject TCG Pocket sets (A1, A1a, A2, P-A, etc.)
    if re.fullmatch(r"(a\d+[a-z]*|p-a|b\d+[a-z]*)", sid):
        return True
    # Reject Japanese subset IDs like sv2a, sm12a, s8b, m1l, m1s
    if re.fullmatch(r"(sv|sm|xy|bw|s|m)\d+[a-z]+", sid) and sid not in TCGDEX_TO_ENGLISH_SET_CODE:
        return True
    if "(jp)" in sname or "japanese" in sname or "pocket" in sname:
        return True
    return False


def _card_number_sort_key(number_str: str) -> tuple[int, str]:
    cleaned = str(number_str).strip()
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    num_val = int(digits) if digits else 0
    return (num_val, cleaned.upper())


@st.cache_data(ttl=86400)
def load_set_metadata() -> dict[str, dict[str, str]]:
    """
    Maps TCGdex English set IDs to their official Limitless/English 3-letter set code
    and releaseDate (YYYY-MM-DD).
    """
    meta: dict[str, dict[str, str]] = {}
    try:
        resp = requests.get(f"{TCGDEX_API}/sets", timeout=15)
        if resp.status_code == 200:
            for s in resp.json():
                set_id = str(s.get("id", "")).lower().strip()
                set_name = str(s.get("name", ""))
                if not set_id or _is_non_english_set(set_id, set_name):
                    continue

                raw_tcg_code = str(s.get("tcgOnline") or "").strip().upper()
                if set_id in TCGDEX_TO_ENGLISH_SET_CODE:
                    eng_code = TCGDEX_TO_ENGLISH_SET_CODE[set_id]
                elif raw_tcg_code and not _is_non_english_set(raw_tcg_code):
                    eng_code = raw_tcg_code
                else:
                    eng_code = set_id.upper()

                release_date = str(s.get("releaseDate") or "0000-00-00")
                meta[set_id] = {
                    "code": eng_code,
                    "name": set_name,
                    "release_date": release_date,
                }
    except requests.RequestException:
        pass
    return meta


def get_gameplay_fingerprint(card: dict) -> tuple:
    name = str(card.get("name", "")).strip().lower()
    category = str(card.get("category", "")).strip().lower()
    hp = str(card.get("hp", ""))

    abilities = tuple(
        (str(a.get("name", "")).strip().lower(), str(a.get("effect", "")).strip().lower())
        for a in card.get("abilities", [])
    )
    attacks = tuple(
        (
            str(a.get("name", "")).strip().lower(),
            tuple(a.get("cost", [])),
            str(a.get("damage", "")).strip().lower(),
            str(a.get("effect", "")).strip().lower(),
        )
        for a in card.get("attacks", [])
    )
    effect = str(card.get("effect", "")).strip().lower()

    return (name, category, hp, abilities, attacks, effect)


@st.cache_data(ttl=3600)
def search_card_suggestions(searchterm: str) -> list[tuple[str, dict]]:
    """
    Queries TCGdex English cards as the user types, maps to official English set codes,
    and returns (label, card_dict) tuples sorted newest-first.
    """
    query = (searchterm or "").strip()
    if len(query) < 2:
        return []

    set_meta = load_set_metadata()
    candidates = []

    try:
        resp = requests.get(f"{TCGDEX_API}/cards", params={"name": query}, timeout=10)
        if resp.status_code != 200:
            return []

        items = resp.json()
        if not isinstance(items, list):
            return []

        for item in items:
            card_id = str(item.get("id", ""))
            name = str(item.get("name", "Unknown"))
            local_id = str(item.get("localId", "")).strip()
            raw_set_id = card_id.split("-")[0].lower() if "-" in card_id else ""

            if _is_non_english_set(raw_set_id):
                continue

            info = set_meta.get(raw_set_id)
            if not info:
                continue

            set_code = info["code"]
            release_date = info["release_date"]

            label = f"{name} ({set_code} #{local_id})"
            payload = {
                "id": card_id,
                "name": name,
                "set_code": set_code,
                "raw_set_id": raw_set_id,
                "number": local_id,
                "image": item.get("image", ""),
                "release_date": release_date,
            }
            candidates.append((release_date, _card_number_sort_key(local_id), label, payload))

        candidates.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        return [(entry[2], entry[3]) for entry in candidates[:30]]
    except requests.RequestException:
        return []


@st.cache_data(ttl=3600)
def fetch_full_card_details(selected_card: dict) -> dict:
    card_id = selected_card.get("id", "")
    raw_set_id = selected_card.get("raw_set_id", "").lower()
    set_code = TCGDEX_TO_ENGLISH_SET_CODE.get(raw_set_id, selected_card.get("set_code", "")).upper()
    number = str(selected_card.get("number", "")).lstrip("0")
    padded_num = number.zfill(3)
    fallback_img = f"https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/tpci/{set_code}/{set_code}_{padded_num}_R_EN_LG.png"

    try:
        if card_id:
            resp = requests.get(f"{TCGDEX_API}/cards/{card_id}", timeout=10)
            if resp.status_code == 200:
                card = resp.json()
                raw_tcg_online = str(card.get("set", {}).get("tcgOnline") or "").strip().upper()
                resolved_set = TCGDEX_TO_ENGLISH_SET_CODE.get(raw_set_id) or raw_tcg_online or set_code
                card["resolved_set_code"] = resolved_set
                card["resolved_number"] = str(card.get("localId", number)).strip()
                card["resolved_image"] = f"{card['image']}/high.webp" if card.get("image") else fallback_img
                return card
    except requests.RequestException:
        pass

    return {
        "name": selected_card.get("name", "Unknown"),
        "resolved_set_code": set_code,
        "resolved_number": number,
        "resolved_image": fallback_img,
    }


@st.cache_data(ttl=86400)
def resolve_deck_card_image(name: str, set_code: str, number: str) -> str:
    clean_set = set_code.strip().upper()
    clean_num = str(number).strip().lstrip("0")
    padded_num = clean_num.zfill(3)
    fallback_img = f"https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/tpci/{clean_set}/{clean_set}_{padded_num}_R_EN_LG.png"

    try:
        resp = requests.get(f"{TCGDEX_API}/cards", params={"name": name.strip()}, timeout=8)
        if resp.status_code == 200:
            set_meta = load_set_metadata()
            for item in resp.json():
                if str(item.get("localId", "")).lstrip("0") != clean_num:
                    continue
                cid = item.get("id", "")
                raw_set_id = cid.split("-")[0].lower() if "-" in cid else ""
                mapped_set = set_meta.get(raw_set_id, {}).get("code")
                if mapped_set == clean_set and item.get("image"):
                    return f"{item['image']}/high.webp"
    except requests.RequestException:
        pass

    return fallback_img


@st.cache_data(ttl=3600)
def find_equivalent_printings(base_card: dict, include_alt_arts: bool) -> list[dict]:
    base_name = base_card.get("name", "").strip()
    base_set = base_card.get("resolved_set_code", "").upper()
    base_num = str(base_card.get("resolved_number", "")).strip()

    primary_printing = {
        "name": base_name,
        "set_code": base_set,
        "number": base_num,
        "rarity": base_card.get("rarity", ""),
    }

    if not include_alt_arts:
        return [primary_printing]

    target_fp = get_gameplay_fingerprint(base_card)
    set_meta = load_set_metadata()
    equivalents = []
    seen = set()

    try:
        resp = requests.get(f"{TCGDEX_API}/cards", params={"name": base_name}, timeout=10)
        if resp.status_code == 200:
            for item in resp.json():
                if str(item.get("name", "")).strip().lower() != base_name.lower():
                    continue

                cid = item.get("id", "")
                raw_set_id = cid.split("-")[0].lower() if "-" in cid else ""
                if _is_non_english_set(raw_set_id):
                    continue

                set_info = set_meta.get(raw_set_id)
                if not set_info:
                    continue

                detail_resp = requests.get(f"{TCGDEX_API}/cards/{cid}", timeout=10)
                if detail_resp.status_code != 200:
                    continue

                cand = detail_resp.json()
                if get_gameplay_fingerprint(cand) != target_fp:
                    continue

                raw_tcg_online = str(cand.get("set", {}).get("tcgOnline") or "").strip().upper()
                cand_set = TCGDEX_TO_ENGLISH_SET_CODE.get(raw_set_id) or raw_tcg_online or set_info["code"]
                cand_num = str(cand.get("localId", "")).strip()

                key = (cand_set, cand_num.lstrip("0"))
                if key not in seen:
                    seen.add(key)
                    equivalents.append({
                        "name": base_name,
                        "set_code": cand_set,
                        "number": cand_num,
                        "rarity": cand.get("rarity", ""),
                    })
    except requests.RequestException:
        pass

    base_key = (base_set, base_num.lstrip("0"))
    if base_key not in seen:
        equivalents.insert(0, primary_printing)

    return equivalents


@st.cache_data(ttl=600)
def get_recent_tournaments(limit: int, fmt: str | None, api_key: str) -> list[dict]:
    headers = {"X-Access-Key": api_key} if api_key else {}
    delay = 0.15 if api_key else 0.5
    tournaments = []
    page = 1

    while len(tournaments) < limit:
        params = {"game": "PTCG", "limit": 50, "page": page}
        if fmt and fmt != "ALL":
            params["format"] = fmt
        resp = requests.get(f"{LIMITLESS_API}/tournaments", params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        tournaments.extend(batch)
        page += 1
        time.sleep(delay)

    return tournaments[:limit]


def normalize_decklist_groups(decklist: dict | list) -> dict[str, list[dict]]:
    grouped = {"Pokémon": [], "Trainer": [], "Energy": []}

    if isinstance(decklist, dict):
        for section, items in decklist.items():
            if not isinstance(items, list):
                continue
            s_lower = str(section).lower()
            if "poke" in s_lower or "poké" in s_lower:
                grouped["Pokémon"].extend(items)
            elif "train" in s_lower:
                grouped["Trainer"].extend(items)
            elif "energ" in s_lower:
                grouped["Energy"].extend(items)
            else:
                grouped["Trainer"].extend(items)
    elif isinstance(decklist, list):
        for card in decklist:
            cat = str(card.get("category", "")).lower()
            if "poke" in cat or "poké" in cat:
                grouped["Pokémon"].append(card)
            elif "energ" in cat:
                grouped["Energy"].append(card)
            else:
                grouped["Trainer"].append(card)

    return grouped


def count_matching_printings(decklist: dict | list, printings: list[dict]) -> tuple[int, str]:
    target_lookup = {
        (p["name"].strip().lower(), p["set_code"].strip().upper(), str(p["number"]).strip().lstrip("0"))
        for p in printings
    }

    grouped = normalize_decklist_groups(decklist)
    cards = grouped["Pokémon"] + grouped["Trainer"] + grouped["Energy"]

    total_count = 0
    breakdown_parts = []

    for card in cards:
        c_name = str(card.get("name", "")).strip().lower()
        c_set = str(card.get("set", "")).strip().upper()
        c_num_raw = str(card.get("number", "")).strip()
        c_num_clean = c_num_raw.lstrip("0")

        if (c_name, c_set, c_num_clean) in target_lookup:
            qty = int(card.get("count", 1))
            total_count += qty
            breakdown_parts.append(f"{qty}x {c_set} #{c_num_raw}")

    return total_count, ", ".join(breakdown_parts)


def render_card_panel(card_data: dict, number: str, printings: list[dict]):
    col_img, col_info = st.columns([1, 2], gap="large")
    set_code = card_data.get("resolved_set_code", "")

    with col_img:
        if card_data.get("resolved_image"):
            st.image(card_data["resolved_image"], use_container_width=True)

    with col_info:
        hp_text = f" - {card_data['hp']} HP" if card_data.get("hp") else ""
        types = ", ".join(card_data.get("types", []))
        type_text = f" - {types}" if types else ""
        st.subheader(f"{card_data.get('name', 'Unknown')}{type_text}{hp_text}")

        category = card_data.get("category", "")
        stage = card_data.get("stage", "")
        evolves = f" - Evolves from {card_data['evolveFrom']}" if card_data.get("evolveFrom") else ""
        if category or stage:
            st.caption(f"{category} {('• ' + stage) if stage else ''}{evolves}")

        st.divider()

        for ability in card_data.get("abilities", []):
            st.markdown(f"**Ability — {ability.get('name')}**")
            st.write(ability.get("effect", ""))

        for atk in card_data.get("attacks", []):
            cost = "".join(f"[{c}]" for c in atk.get("cost", []))
            dmg = atk.get("damage", "")
            st.markdown(f"**{cost} {atk.get('name')}** `{dmg}`")
            if atk.get("effect"):
                st.write(atk["effect"])

        if card_data.get("weaknesses") or card_data.get("retreat") is not None:
            st.divider()
            weaknesses = ", ".join(f"{w['type']} {w['value']}" for w in card_data.get("weaknesses", [])) or "None"
            resistances = ", ".join(f"{r['type']} {r['value']}" for r in card_data.get("resistances", [])) or "None"
            retreat = card_data.get("retreat", "None")
            st.markdown(f"**Weakness:** {weaknesses} &nbsp;|&nbsp; **Resistance:** {resistances} &nbsp;|&nbsp; **Retreat:** {retreat}")

        set_info = card_data.get("set", {})
        set_name = set_info.get("name", set_code)
        rarity = card_data.get("rarity", "")
        st.info(f"**{set_name} ({set_code})** — #{number} {('• ' + rarity) if rarity else ''}")

        if len(printings) > 1:
            linked_labels = ", ".join(f"{p['set_code']} #{p['number']}" for p in printings)
            st.success(f"**Linked Alt-Arts / Reprints Included ({len(printings)}):** {linked_labels}")


def render_visual_decklist(deck_record: dict):
    st.markdown(f"### Visual Decklist — {deck_record['Player']} (#{deck_record['Place']} at {deck_record['Tournament']})")
    grouped = normalize_decklist_groups(deck_record["raw_decklist"])

    cols_per_row = 6
    for section_name, cards in grouped.items():
        total_in_section = sum(int(c.get("count", 1)) for c in cards)
        if not cards:
            continue
        st.markdown(f"#### {section_name} ({total_in_section})")

        for i in range(0, len(cards), cols_per_row):
            row_cards = cards[i : i + cols_per_row]
            cols = st.columns(cols_per_row)
            for col, card in zip(cols, row_cards):
                c_name = str(card.get("name", "Unknown"))
                c_set = str(card.get("set", "")).upper()
                c_num = str(card.get("number", ""))
                c_count = int(card.get("count", 1))
                img_url = resolve_deck_card_image(c_name, c_set, c_num)

                with col:
                    st.image(img_url, use_container_width=True)
                    st.caption(f"**{c_count}x** {c_name} ({c_set} #{c_num})")


if "search_results" not in st.session_state:
    st.session_state.search_results = []
if "active_visual_deck_idx" not in st.session_state:
    st.session_state.active_visual_deck_idx = None
if "search_epoch" not in st.session_state:
    st.session_state.search_epoch = 0

st.title("Limitless Online Tournament Card Finder")

top_r1, top_r2 = st.columns([6, 1.5])
with top_r2:
    if st.button("Reset Search Boxes", use_container_width=True):
        st.session_state.search_epoch += 1
        st.rerun()

epoch = st.session_state.search_epoch
col_search1, col_search2 = st.columns(2, gap="large")

with col_search1:
    st.markdown("#### 1. Primary Card (Required)")
    selected_card_1 = st_searchbox(
        search_card_suggestions,
        placeholder="Type at least 2 letters (e.g. Golisopod, Talonflame)...",
        key=f"card1_searchbox_{epoch}",
        clear_on_submit=False,
    )

with col_search2:
    st.markdown("#### 2. Second Card Combo Filter (Optional)")
    selected_card_2 = st_searchbox(
        search_card_suggestions,
        placeholder="Optional 2nd card...",
        key=f"card2_searchbox_{epoch}",
        clear_on_submit=False,
    )

st.markdown("#### Tournament Filters")
f1, f2, f3, f4 = st.columns([1, 1, 1, 1.5])
num_tournaments = f1.number_input("Last X Tournaments", min_value=1, max_value=200, value=20)
min_players = f2.number_input("Min Players", min_value=0, max_value=2000, value=32)
fmt_filter = f3.selectbox("Format", ["STANDARD", "EXPANDED", "ALL"])
api_key = f4.text_input("Limitless API Key (Optional)", type="password")

include_alt_arts = st.checkbox(
    "Include Alternative Arts, Full Arts, Illustration Rares, Reprints & Promos",
    value=True,
)

run_search = st.button("Search Tournaments", type="primary", use_container_width=True)

if run_search:
    if not selected_card_1:
        st.error("Please select a primary card from the dropdown menu first.")
        st.stop()

    st.session_state.active_visual_deck_idx = None
    card1_full = fetch_full_card_details(selected_card_1)
    c1_name = card1_full.get("name", selected_card_1["name"])
    c1_num = selected_card_1["number"]
    c1_printings = find_equivalent_printings(card1_full, include_alt_arts)

    card2_full = None
    c2_printings = []
    if selected_card_2:
        card2_full = fetch_full_card_details(selected_card_2)
        c2_name = card2_full.get("name", selected_card_2["name"])
        c2_num = selected_card_2["number"]
        c2_printings = find_equivalent_printings(card2_full, include_alt_arts)

    st.session_state.card1_full = card1_full
    st.session_state.c1_num = c1_num
    st.session_state.c1_printings = c1_printings
    st.session_state.card2_full = card2_full
    st.session_state.c2_num = selected_card_2["number"] if selected_card_2 else ""
    st.session_state.c2_printings = c2_printings

    headers = {"X-Access-Key": api_key} if api_key else {}
    delay = 0.15 if api_key else 0.5
    tournaments = get_recent_tournaments(int(num_tournaments), fmt_filter, api_key)

    progress = st.progress(0, text="Scanning tournaments...")
    matches = []

    for idx, t in enumerate(tournaments, start=1):
        t_id = t.get("id")
        t_name = t.get("name", "Unknown")
        t_date = t.get("date", "")[:10]
        t_players = int(t.get("players") or 0)

        progress.progress(idx / len(tournaments), text=f"Checking {idx}/{len(tournaments)}: {t_name}")

        if t_players < min_players:
            continue

        resp = requests.get(f"{LIMITLESS_API}/tournaments/{t_id}/standings", headers=headers, timeout=15)
        if resp.status_code != 200:
            continue

        for entry in resp.json():
            decklist = entry.get("decklist")
            if not decklist:
                continue

            c1_count, c1_breakdown = count_matching_printings(decklist, c1_printings)
            if c1_count == 0:
                continue

            c2_count = None
            c2_breakdown = ""
            if card2_full:
                c2_val, c2_bd = count_matching_printings(decklist, c2_printings)
                if c2_val == 0:
                    continue
                c2_count = c2_val
                c2_breakdown = c2_bd

            row = {
                "Date": t_date,
                "Place": entry.get("placing"),
                "Player": entry.get("name", "Unknown"),
                "Tournament": t_name,
                "Players": t_players,
                f"{c1_name} Total": c1_count,
                f"{c1_name} Printings": c1_breakdown,
            }
            if card2_full:
                c2_label = card2_full.get("name", selected_card_2["name"])
                row[f"{c2_label} Total"] = c2_count
                row[f"{c2_label} Printings"] = c2_breakdown

            row["Decklist"] = f"https://play.limitlesstcg.com/tournament/{t_id}/player/{entry.get('player')}/decklist"
            row["raw_decklist"] = decklist
            matches.append(row)
