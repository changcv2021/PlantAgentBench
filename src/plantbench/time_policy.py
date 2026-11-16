"""Conservative source-time comparison; unknown precision never invents order."""
import datetime as dt


def available_by(available, target):
    if available is None or target is None or available.kind != target.kind:
        return False
    try:
        if available.kind in {"experiment_day","dialog_turn"}:
            return type(available.value) in {int,float} and type(target.value) in {int,float} and available.value <= target.value
        if available.kind == "date":
            return dt.date.fromisoformat(available.value) <= dt.date.fromisoformat(target.value)
        if available.kind == "datetime":
            if available.timezone_status != "source_declared" or target.timezone_status != "source_declared":
                return False
            left,right = dt.datetime.fromisoformat(available.value),dt.datetime.fromisoformat(target.value)
            return left.tzinfo is not None and right.tzinfo is not None and left <= right
    except (ValueError,TypeError):
        return False
    return False
