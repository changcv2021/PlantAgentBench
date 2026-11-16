"""Observed Lettuce_growth.zip layout, preserving its incompatible source years."""
import datetime
import math
import re
from pathlib import Path

import openpyxl
from openpyxl.utils.cell import get_column_letter


def date_time(value, date_only=False):
    if not isinstance(value, (datetime.datetime, datetime.date)):
        raise ValueError("Source date is not an Excel date cell")
    if date_only:
        value = value.date() if isinstance(value, datetime.datetime) else value
        return {"kind": "date", "value": value.isoformat(), "precision": "date", "timezone_status": "not_applicable"}
    return {"kind": "datetime", "value": value.isoformat(), "precision": "second", "timezone_status": "unknown"}


class LettuceAdapter:
    def convert(self, ctx):
        figures = {Path(p).name: p for p in ctx.files if Path(p).name in {"2_Figure_1.png", "3_Figure_2.png"}}
        if set(figures) != {"2_Figure_1.png", "3_Figure_2.png"}:
            raise ValueError("Verified source layout figures are required for camera-to-plant linkage")
        figure_provenance = {}
        for name, relative in figures.items():
            prov = ctx.provenance(relative, "image")
            figure_provenance[name] = prov
            ctx.asset(relative, prov, scope="knowledge_entry", modality="reference_figure", access_class="reference_annotation")
        layout = figure_provenance["3_Figure_2.png"]
        camera = ctx.subject("canopy-series-2022/reference-heads-view", "camera_view", layout, cycle="biomass-workbook-2022", resolution="source_group",
                             source_fields={"identity_basis": "Source Figure 2 labels B22-B47 in this shared canopy view; no individual crop boxes generated"})
        image_count = 0
        for relative in sorted(ctx.files):
            path = Path(relative)
            if path.suffix.lower() != ".png" or path.name in figures:
                continue
            prov = ctx.provenance(relative, "image")
            # Numeric image prefixes are acquisition sequence labels, not plant IDs.
            match = re.fullmatch(r"\d+_(\d{4}-\d{2}-\d{2})(?:_biomass_measured)?\.png", path.name)
            if not match:
                raise ValueError("Unreviewed canopy filename; do not assign a guessed date")
            times = {"collection_time": {"kind": "date", "value": match[1], "precision": "date", "timezone_status": "not_applicable", "source_value": path.name}}
            asset = ctx.asset(relative, prov, times, scope="camera_view")
            ctx.observation(relative, camera, "visual.rgb", {"kind": "asset_ref", "value": asset}, relative, None, prov,
                            "camera_view", "visual_asset", times)
            image_count += 1
        books = {Path(p).name: p for p in ctx.files if p.lower().endswith(".xlsx")}
        if set(books) != {"4_Biomass.xlsx", "5_Environment_Solution.xlsx"}:
            raise ValueError("Unexpected workbook inventory; mapping needs review")
        relative = books["4_Biomass.xlsx"]
        workbook = openpyxl.load_workbook(ctx.source_root / relative, read_only=True, data_only=True)
        biomass_cells = 0
        for sheet in workbook:
            rows = sheet.iter_rows()
            header = next(rows)
            plants = {}
            for cell in header:
                match = re.fullmatch(r"(B\d+)\s*\(g\)", str(cell.value or ""))
                if match:
                    prov = ctx.provenance(relative, "xlsx", sheet=sheet.title, cell=cell.coordinate)
                    plants[cell.column] = (match[1], ctx.subject(f"biomass-2022/{match[1]}", "individual_plant", prov,
                                                               cycle="biomass-workbook-2022", source_fields={"release_year_conflict": True}))
                    ctx.link(f"layout/reference-heads/{match[1]}", camera, plants[cell.column][1], "source_camera_view_contains_plant_position",
                             [layout, prov], "camera_view")
            if not plants:
                raise ValueError("Biomass sheet has no evidenced gram columns")
            for row_index, row in enumerate(rows, 2):
                if row[0].value is None:
                    continue
                times = {"event_time": date_time(row[0].value, True)}
                for column, (source_id, subject) in plants.items():
                    cell = row[column - 1]
                    coordinate = f"{get_column_letter(column)}{row_index}"
                    value = cell.value
                    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                        raise ValueError("Non-numeric source biomass requires explicit handling")
                    prov = ctx.provenance(relative, "xlsx", sheet=sheet.title, cell=f"A{row_index}:{coordinate}")
                    ctx.observation(f"{relative}/{sheet.title}/{coordinate}", subject, "growth.fresh_mass", {"kind": "scalar", "value": value},
                                    value, "g", prov, "individual_plant", "manual_measurement", times,
                                    source_fields={"source_days_after_transplanting": row[1].value,
                                                   "calendar_dat_mapping": "source co-recorded values only; no inferred transplant-date correction"})
                    biomass_cells += 1
        workbook.close()
        relative = books["5_Environment_Solution.xlsx"]
        workbook = openpyxl.load_workbook(ctx.source_root / relative, read_only=True, data_only=True)
        mapping = {
            "EC (dS/m)": ("environment.solution_ec", "dS/m", "reservoir", "solution"),
            "WaterTemp (°C)": ("environment.solution_temperature", "degC", "reservoir", "solution"),
            "AirTemp_Lower (°C)": ("environment.air_temperature", "degC", "tier", "lower"),
            "AirHum_Lower (%)": ("environment.relative_humidity", "%", "tier", "lower"),
            "AirTemp_Upper (°C)": ("environment.air_temperature", "degC", "tier", "upper"),
            "AirHum_Upper (%)": ("environment.relative_humidity", "%", "tier", "upper"),
            "Volume (L)": ("environment.solution_volume", "L", "reservoir", "solution"),
        }
        environment_cells = 0
        for sheet in workbook:
            rows = sheet.iter_rows()
            header = next(rows)
            if header[0].value != "Timestamp" or {c.value for c in header[1:] if c.value} != set(mapping):
                raise ValueError("Environment workbook headers changed")
            subjects = {}
            for index, cell in enumerate(header[1:], 1):
                if cell.value not in mapping:
                    continue
                variable, unit, scope, name = mapping[cell.value]
                if name not in subjects:
                    prov = ctx.provenance(relative, "xlsx", sheet=sheet.title, cell=cell.coordinate)
                    subjects[name] = ctx.subject(f"environment-2024/{name}", scope, prov, cycle="environment-workbook-2024", resolution="source_group")
            for row_index, row in enumerate(rows, 2):
                if row[0].value is None:
                    continue
                times = {"event_time": date_time(row[0].value)}
                for index, cell in enumerate(row[1:], 1):
                    coordinate = f"{get_column_letter(index + 1)}{row_index}"
                    label = header[index].value
                    if label not in mapping:
                        continue
                    variable, unit, scope, name = mapping[label]
                    value = cell.value
                    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                        raise ValueError("Unexpected environment value")
                    prov = ctx.provenance(relative, "xlsx", sheet=sheet.title, cell=f"A{row_index}:{coordinate}")
                    ctx.observation(f"{relative}/{sheet.title}/{coordinate}", subjects[name], variable, {"kind": "scalar", "value": value}, value, unit,
                                    prov, scope, "source_processed_unknown", times, ["RELEASE_YEAR_CONFLICT", "SOURCE_HALF_HOUR_AGGREGATE"], source_unit=label.split("(")[1].rstrip(")"))
                    environment_cells += 1
        workbook.close()
        ctx.issues.extend(["Images and biomass use 2022 while README and environment use 2024; no year correction or cross-year joins performed.",
                           "Figure 2 supports camera-view membership of 18 reference heads; individual crop localization and exact capture clock times remain unavailable.",
                           "Biomass measurement clock/AM-PM fields retained in raw workbook; only source date is canonical pending clock semantics review.",
                           "Sensor aggregation method is not fully specified; environment values are source_processed_unknown, not claimed instantaneous direct measurements."])
        total = image_count + biomass_cells + environment_cells + len(figures)
        return {"source_record_definition": "one released image/reference figure or one defined plant-biomass/environment value cell; DAT retained as source context, clock/aggregate columns remain locatable in the unchanged workbook",
                "source_records_seen": total, "converted": total, "preserved_unmapped": 0, "quarantined": 0, "rejected": 0,
                "image_records_seen": image_count, "biomass_cells_seen": biomass_cells, "environment_cells_seen": environment_cells,
                "documentation_image_assets": len(figures), "camera_plant_membership_links": sum(1 for name in ctx.subject_cache if name.startswith("biomass-2022/")) if hasattr(ctx, "subject_cache") else None,
                "cross_year_links": 0, "issues": ctx.issues}
