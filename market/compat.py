"""Deterministic fit checks. Unknown specs stay unverified, never fits."""

def check(category, offer_specs, owned_parts):
    """Return a compat object for one offer against the owned parts."""
    specs = offer_specs or {}
    owned = owned_parts or []
    if category == "ram":
        return _check_ram(specs, owned)
    if category == "ssd":
        return _check_ssd(specs, owned)
    return {
        "status": "unverified",
        "reasons": [],
        "unknown": ["category rules not written yet"],
    }


def _board_specs(owned):
    for part in owned:
        if part.get("category") == "board":
            return part.get("specs") or {}
    return None


def _finish(reasons, unknown, failed):
    if failed:
        status = "no"
    elif unknown:
        status = "unverified"
    else:
        status = "fits"
    return {"status": status, "reasons": reasons, "unknown": unknown}


def _text(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _both(offer, board, offer_field, board_field, unknown):
    """Return (offer_value, board_value). Record each missing name once."""
    offer_ok = offer_field in offer and offer.get(offer_field) is not None
    board_ok = board_field in board and board.get(board_field) is not None
    if not offer_ok and offer_field not in unknown:
        unknown.append(offer_field)
    if not board_ok and board_field not in unknown:
        unknown.append(board_field)
    if not offer_ok or not board_ok:
        return None
    return offer[offer_field], board[board_field]


def _check_ram(offer, owned):
    board = _board_specs(owned)
    if board is None:
        return {"status": "unverified", "reasons": [], "unknown": ["board"]}

    reasons = []
    unknown = []
    failed = False

    pair = _both(offer, board, "ddr_gen", "ddr_gen", unknown)
    if pair is not None:
        got, need = pair
        if got == need:
            reasons.append(f"{got} matches your board")
        else:
            failed = True
            reasons.append(f"{got} does not match your board's {need}")

    pair = _both(offer, board, "dimm_format", "dimm_format", unknown)
    if pair is not None:
        got, need = pair
        if got == need:
            reasons.append(f"{got} matches your board")
        else:
            failed = True
            reasons.append(f"{got} does not match your board's {need}")

    ecc = offer.get("ecc") if "ecc" in offer else None
    buffered = offer.get("buffered") if "buffered" in offer else None
    board_ecc = board.get("ecc") is True
    if ecc is True or buffered is True:
        if board_ecc:
            if ecc is True:
                reasons.append("ECC matches your board")
            if buffered is True:
                reasons.append("Buffered memory matches your board's ECC support")
        else:
            failed = True
            if ecc is True:
                reasons.append("ECC memory does not fit this consumer board")
            if buffered is True:
                reasons.append("Buffered memory does not fit this consumer board")
    elif ecc is False:
        reasons.append("Not ECC")
    else:
        unknown.append("ecc")

    pair = _both(offer, board, "speed_mts", "max_speed_mts", unknown)
    if pair is not None:
        speed, limit = pair
        if speed <= limit:
            reasons.append(f"{_text(speed)} MT/s is within your board's {_text(limit)} MT/s")
        else:
            reasons.append(f"will run at {_text(limit)} MT/s")

    pair = _both(offer, board, "capacity_gb", "max_capacity_gb", unknown)
    if pair is not None:
        cap, limit = pair
        if cap <= limit:
            reasons.append(f"{_text(cap)} GB is within your board's {_text(limit)} GB")
        else:
            failed = True
            reasons.append(f"{_text(cap)} GB is over your board's {_text(limit)} GB")

    pair = _both(offer, board, "modules", "dimm_slots", unknown)
    if pair is not None:
        modules, slots = pair
        if modules <= slots:
            reasons.append(f"{_text(modules)} modules fit your board's {_text(slots)} slots")
        else:
            failed = True
            reasons.append(f"{_text(modules)} modules is more than your board's {_text(slots)} slots")

    return _finish(reasons, unknown, failed)


def _check_ssd(offer, owned):
    board = _board_specs(owned)
    if board is None:
        return {"status": "unverified", "reasons": [], "unknown": ["board"]}

    reasons = []
    unknown = []
    failed = False

    pair = _both(offer, board, "m2_key", "m2_key", unknown)
    if pair is not None:
        got, need = pair
        if got == need:
            reasons.append(f"{got} key matches your board")
        else:
            failed = True
            reasons.append(f"{got} key does not match your board's {need} key")

    pair = _both(offer, board, "pcie_gen", "pcie_gen", unknown)
    if pair is not None:
        got, need = pair
        if got <= need:
            reasons.append(f"PCIe {got} is within your board's PCIe {need}")
        else:
            reasons.append(f"runs at PCIe {need} speed")

    return _finish(reasons, unknown, failed)
