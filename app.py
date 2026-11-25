import re
import time
import pandas as pd
import requests
import streamlit as st
from streamlit_searchbox import st_searchbox

st.set_page_config(page_title="Limitless Card Tournament Finder", layout="wide")

LIMITLESS_API = "https://play.limitlesstcg.com/api"
TCGDEX_API = "https://api.tcgdex.net/v2/en"

# Official English / Limitless set codes mapped from TCGdex set IDs (for image fallback & display)
TCGDEX_TO_ENGLISH_SET_CODE = {
    "me01": "MEG",
    "me02": "PFL",
    "me02.5": "ASC",
    "me03": "POR",
    "me04": "CRI",
    "me05": "PBL",
    "me06": "30C",
    "me07": "DLR",
    "mep": "MEP",
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
}


def _is_non_english_set(set_id: str) -> bool:
    sid = (set_id or "").strip().lower()
    if re.fullmatch(r"(a\d+[a-z]*|p-a|b\d+[a-z]*)", sid):
        return True
    if re.fullmatch(r"(sv|sm|xy|bw|s|m)\d+[a-z]+", sid) and sid not in TCGDEX_TO_ENGLISH_SET_CODE:
        return True
    return False


@st.cache_data(ttl=86400)
def load_set_metadata() -> dict[str, dict[str, str]]:
    meta: dict[str, dict[str, str]] = {}
    try:
        resp = requests.get(f"{TCGDEX_API}/sets", timeout=15)
        if resp.status_code == 200:
            for s in resp.json():
                set_id = str(s.get("id", "")).lower().strip()
                if not set_id or _is_non_english_set(set_id):
                    continue
                raw_tcg = str(s.get("tcgOnline") or "").strip().upper()
                eng_code = TCGDEX_TO_ENGLISH_SET_CODE.get(set_id) or raw_tcg or set_id.upper()
                meta[set_id] = {
                    "code": eng_code,
                    "release_date": str(s.get("releaseDate") or "0000-00-00"),
                }
    except requests.RequestException:
        pass
    return meta


@st.cache_data(ttl=3600)
def search_card_name_suggestions(searchterm: str) -> list[tuple[str, str]]:
    """
    Queries TCGdex as the user types and returns deduplicated (Card Name, Card Name) tuples,
    ordered so names with the most recent printings appear first.
    """
    query = (searchterm or "").strip()
    if len(query) < 2:
        return []

    set_meta = load_set_metadata()
    latest_date_by_name: dict[str, str] = {}
    display_name_by_lower: dict[str, str] = {}

    try:
        resp = requests.get(f"{TCGDEX_API}/cards", params={"name": query}, timeout=10)
        if resp.status_code != 200:
            return []

        items = resp.json()
        if not isinstance(items, list):
            return []

        for item in items:
            name = str(item.get("name", "")).strip()
            if not name:
                continue

            card_id = str(item.get("id", ""))
            raw_set_id = card_id.split("-")[0].lower() if "-" in card_id else ""
            if _is_non_english_set(raw_set_id):
                continue

            release_date = (set_meta.get(raw_set_id) or {}).get("release_date", "0000-00-00")
            key = name.lower()

            if key not in display_name_by_lower:
                display_name_by_lower[key] = name
                latest_date_by_name[key] = release_date
            elif release_date > latest_date_by_name[key]:
                latest_date_by_name[key] = release_date

        # Sort by newest release date first, then shorter/exact-prefix name matches
        q_lower = query.lower()
        sorted_keys = sorted(
            display_name_by_lower.keys(),
            key=lambda k: (
                latest_date_by_name[k],
                1 if k == q_lower else 0,
                1 if k.startswith(q_lower) else 0,
            ),
            reverse=True,
        )

        return [(display_name_by_lower[k], display_name_by_lower[k]) for k in sorted_keys[:30]]
    except requests.RequestException:
        return []


@st.cache_data(ttl=3600)
def fetch_representative_card(card_name: str) -> dict | None:
    """Fetches the most recent English printing of a card name for artwork and attack text."""
    set_meta = load_set_metadata()
    best_item = None
    best_date = ""

    try:
        resp = requests.get(f"{TCGDEX_API}/cards", params={"name": card_name}, timeout=10)
        if resp.status_code == 200:
            for item in resp.json():
                if str(item.get("name", "")).strip().lower() != card_name.strip().lower():
                    continue
                cid = str(item.get("id", ""))
                raw_set_id = cid.split("-")[0].lower() if "-" in cid else ""
                if _is_non_english_set(raw_set_id):
                    continue
                r_date = (set_meta.get(raw_set_id) or {}).get("release_date", "0000-00-00")
                if best_item is None or r_date >= best_date:
                    best_item = item
                    best_date = r_date

        if best_item:
            cid = str(best_item.get("id", ""))
            raw_set_id = cid.split("-")[0].lower() if "-" in cid else ""
            local_num = str(best_item.get("localId", "")).strip()
            eng_set = (set_meta.get(raw_set_id) or {}).get("code") or raw_set_id.upper()
            padded_num = local_num.lstrip("0").zfill(3)
            fallback_img = f"https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/tpci/{eng_set}/{eng_set}_{padded_num}_R_EN_LG.png"

            detail_resp = requests.get(f"{TCGDEX_API}/cards/{cid}", timeout=10)
            if detail_resp.status_code == 200:
                card = detail_resp.json()
                card["resolved_set_code"] = eng_set
                card["resolved_number"] = local_num
                card["resolved_image"] = f"{card['image']}/high.webp" if card.get("image") else fallback_img
                return card
    except requests.RequestException:
        pass

    return None


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


def count_card_by_name(decklist: dict | list, target_name: str) -> tuple[int, str]:
    """
    Matches any printing (regular, Full Art, SIR/IR, reprint, promo) with the exact card name
    inside a Limitless decklist. Returns (total_copies, breakdown_string).
    """
    target_lower = target_name.strip().lower()
    grouped = normalize_decklist_groups(decklist)
    cards = grouped["Pokémon"] + grouped["Trainer"] + grouped["Energy"]

    total_count = 0
    breakdown_parts = []

    for card in cards:
        c_name = str(card.get("name", "")).strip().lower()
        if c_name == target_lower:
            qty = int(card.get("count", 1))
            c_set = str(card.get("set", "")).strip().upper()
            c_num = str(card.get("number", "")).strip()
            total_count += qty
            breakdown_parts.append(f"{qty}x {c_set} #{c_num}")

    return total_count, ", ".join(breakdown_parts)


def render_card_panel(card_data: dict | None, fallback_name: str):
    if not card_data:
        st.subheader(fallback_name)
        return

    col_img, col_info = st.columns([1, 2], gap="large")
    set_code = card_data.get("resolved_set_code", "")
    number = card_data.get("resolved_number", "")

    with col_img:
        if card_data.get("resolved_image"):
            st.image(card_data["resolved_image"], use_container_width=True)

    with col_info:
        hp_text = f" - {card_data['hp']} HP" if card_data.get("hp") else ""
        types = ", ".join(card_data.get("types", []))
        type_text = f" - {types}" if types else ""
        st.subheader(f"{card_data.get('name', fallback_name)}{type_text}{hp_text}")

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
        if set_name or set_code:
            st.info(f"Showing preview from **{set_name} ({set_code})** #{number} • Tournament search includes all printings & alt-arts of **{fallback_name}**")


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
    selected_name_1 = st_searchbox(
        search_card_name_suggestions,
        placeholder="Type card name (e.g. Mega Golisopod ex, Talonflame ex)...",
        key=f"card1_searchbox_{epoch}",
        clear_on_submit=False,
    )

with col_search2:
    st.markdown("#### 2. Second Card Combo Filter (Optional)")
    selected_name_2 = st_searchbox(
        search_card_name_suggestions,
        placeholder="Optional 2nd card name...",
        key=f"card2_searchbox_{epoch}",
        clear_on_submit=False,
    )

st.markdown("#### Tournament Filters")
f1, f2, f3, f4 = st.columns([1, 1, 1, 1.5])
num_tournaments = f1.number_input("Last X Tournaments", min_value=1, max_value=200, value=20)
min_players = f2.number_input("Min Players", min_value=0, max_value=2000, value=32)
fmt_filter = f3.selectbox("Format", ["STANDARD", "EXPANDED", "ALL"])
api_key = f4.text_input("Limitless API Key (Optional)", type="password")

run_search = st.button("Search Tournaments", type="primary", use_container_width=True)

if run_search:
    if not selected_name_1:
        st.error("Please select a primary card name from the dropdown menu first.")
        st.stop()

    st.session_state.active_visual_deck_idx = None
    c1_name = selected_name_1.strip()
    card1_full = fetch_representative_card(c1_name)

    c2_name = selected_name_2.strip() if selected_name_2 else ""
    card2_full = fetch_representative_card(c2_name) if c2_name else None

    st.session_state.c1_name = c1_name
    st.session_state.card1_full = card1_full
    st.session_state.c2_name = c2_name
    st.session_state.card2_full = card2_full

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

            c1_count, c1_breakdown = count_card_by_name(decklist, c1_name)
            if c1_count == 0:
                continue

            c2_count = None
            c2_breakdown = ""
            if c2_name:
                c2_val, c2_bd = count_card_by_name(decklist, c2_name)
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
            if c2_name:
                row[f"{c2_name} Total"] = c2_count
                row[f"{c2_name} Printings"] = c2_breakdown

            row["Decklist"] = f"https://play.limitlesstcg.com/tournament/{t_id}/player/{entry.get('player')}/decklist"
            row["raw_decklist"] = decklist
            matches.append(row)

        time.sleep(delay)

    progress.empty()
    st.session_state.search_results = matches

if "c1_name" in st.session_state:
    st.markdown("---")
    if st.session_state.c2_name:
        left_card, right_card = st.columns(2, gap="large")
        with left_card:
            render_card_panel(st.session_state.card1_full, st.session_state.c1_name)
        with right_card:
            render_card_panel(st.session_state.card2_full, st.session_state.c2_name)
    else:
        render_card_panel(st.session_state.card1_full, st.session_state.c1_name)

if run_search or st.session_state.search_results:
    st.markdown("---")
    st.subheader("Decklists That Include This Card")

    matches = st.session_state.search_results
    if not matches:
        st.warning("No decklists found matching those filters.")
    else:
        public_rows = [{k: v for k, v in m.items() if k != "raw_decklist"} for m in matches]
        df = pd.DataFrame(public_rows)

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Decklist": st.column_config.LinkColumn("Decklist Link", display_text="View Decklist")
            },
        )

        st.download_button(
            "Download Results as CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="limitless_card_results.csv",
            mime="text/csv",
        )

        st.markdown("---")
        st.subheader("Visual Decklist")
        deck_options = [
            f"#{m['Place']} — {m['Player']} ({m['Tournament']} • {m['Date']})"
            for m in matches
        ]
        pick_col, btn_col = st.columns([3, 1], vertical_alignment="bottom")
        chosen_label = pick_col.selectbox("Select a matching decklist to preview", deck_options, key="visual_deck_select")
        if btn_col.button("Show Visual Decklist", type="secondary", use_container_width=True):
            st.session_state.active_visual_deck_idx = deck_options.index(chosen_label)

        if st.session_state.active_visual_deck_idx is not None:
            idx = st.session_state.active_visual_deck_idx
            if 0 <= idx < len(matches):
                render_visual_decklist(matches[idx])
