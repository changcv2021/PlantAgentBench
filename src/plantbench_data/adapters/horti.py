"""Full release conversion with explicit source processing and unresolved joins."""
import collections
import csv
import datetime
import io
import math
import posixpath
import re
from pathlib import Path

from .mirage import annotation as reference_annotation


def annotation(ctx, key, subject, value, provenance, annotation_type):
    return reference_annotation(ctx, key, subject, value, provenance, annotation_type,
        assertion_scope="Original source documentation or tabular context; semantic interpretation unresolved, not agricultural gold")


GROWTH = {
    "Leaf Area": ("growth.leaf_area", "cm2"), "叶面积": ("growth.leaf_area", "cm2"),
    "Leaf Angle": ("growth.leaf_angle", None), "叶夹角": ("growth.leaf_angle", None),
    "Plant Height": ("growth.plant_height", "cm"), "高度": ("growth.plant_height", "cm"), "株高": ("growth.plant_height", "cm"),
    "Stem Diameter": ("growth.stem_diameter", "mm"), "茎粗": ("growth.stem_diameter", "mm"),
    "Leaf Width": ("growth.leaf_width", "cm"), "叶宽-1": ("growth.leaf_width", "cm"),
    "Leaf Length": ("growth.leaf_length", "cm"), "叶长-1": ("growth.leaf_length", "cm"),
    "NDVI": ("spectral.ndvi", "1"), "RVI": ("spectral.rvi", "1"), "LNC": ("spectral.leaf_nitrogen_content", "mg/g"),
    "LNA": ("spectral.leaf_nitrogen_accumulation", "mg"), "LAI": ("spectral.leaf_area_index", "1"),
    "LDW": ("spectral.leaf_dry_weight_estimate", "g")}
SENSOR = {
    "空气温度": ("environment.air_temperature", "degC"), "Air Temperature": ("environment.air_temperature", "degC"),
    "相对湿度": ("environment.relative_humidity", "%"), "Relative Humidity": ("environment.relative_humidity", "%"),
    "二氧化碳": ("environment.co2", "ppm"), "CO2": ("environment.co2", "ppm"),
    "土壤水分": ("substrate.water_content_volumetric", "%vol"), "Soil Moisture": ("substrate.water_content_volumetric", "%vol"),
    "土壤温度": ("substrate.temperature", "degC"), "Soil Temperature": ("substrate.temperature", "degC"),
    "光照": ("environment.illuminance", None), "Light Intensity": ("environment.illuminance", None),
    "电压": ("sensor.supply_voltage", "V"), "Voltage": ("sensor.supply_voltage", "V")}


def source_time(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        return {"kind": "datetime", "value": value.isoformat(), "precision": "second", "timezone_status": "unknown"}
    text = str(value).strip().replace("/", "-")
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?: (\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    if not match:
        return None
    year, month, day = map(int, match.groups()[:3])
    if match[4] is None:
        return {"kind": "date", "value": datetime.date(year, month, day).isoformat(), "precision": "date", "timezone_status": "not_applicable"}
    stamp = datetime.datetime(year, month, day, int(match[4]), int(match[5]), int(match[6] or 0))
    return {"kind": "datetime", "value": stamp.isoformat(), "precision": "second" if match[6] else "minute", "timezone_status": "unknown"}


def scalar(value, unit):
    if value in (None, ""):
        return None, []
    if unit is None:
        return None, ["UNRESOLVED_SOURCE_UNIT"]
    try:
        number = float(value)
        if isinstance(value, bool) or not math.isfinite(number):
            raise ValueError()
        return number, []
    except (ValueError, TypeError):
        return None, ["NONNUMERIC_SOURCE_VALUE"]


def sensor_mapping(label):
    for prefix, (variable, unit) in SENSOR.items():
        if str(label).startswith(prefix):
            # Source sheet headers explicitly specify klux in some releases.
            # Unit-less processed columns do not inherit either klux or lx.
            if variable == "environment.illuminance":
                unit = "klx" if "klux" in label.lower() else "lx" if re.search(r"(?<!k)lux", label, re.I) else None
            return variable, unit
    return None


def clean(value):
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


class HortiM3Adapter:
    def convert(self, ctx):
        import openpyxl
        from openpyxl.utils.cell import get_column_letter
        counts, images, exclusions = collections.Counter(), {}, []
        ctx.external_provenance(ctx.mapping["paper_evidence_ref"])
        for relative in sorted(ctx.files):
            path = Path(relative)
            if path.suffix.lower() not in {".jpg", ".png"}:
                continue
            prov = ctx.provenance(relative, "image")
            year_match = re.search(r"/(202[345])/", relative)
            year = year_match[1] if year_match else "unresolved"
            figure = path.suffix.lower() == ".png"
            subject = ctx.subject(f"camera/{year}/{path.parent.name}", "camera_view", prov, cycle=f"source-year-{year}", resolution="source_group")
            stamp = None
            match = re.fullmatch(r"(\d{1,2})_(\d{1,2})_\d+\.jpg", path.name, re.I)
            if match and year != "unresolved":
                stamp = source_time(f"{year}-{match[1]}-{match[2]}")
            times = {"collection_time": stamp} if stamp else {}
            try:
                asset = ctx.asset(relative, prov, times, scope="camera_view", modality="reference_figure" if figure else "rgb",
                                  access_class="reference_annotation" if figure else "restricted_raw",
                                  source_fields={"epoch_fields_not_interpreted": not bool(match), "plant_localization": "not_established"})
            except OSError as exc:
                # Only quarantine a decode failure after confirming unchanged
                # source bytes. Missing/changed input is an integrity failure.
                from ..common import sha256
                source = ctx.source_root / relative
                if not source.is_file() or sha256(source) != ctx.files[relative]["sha256"]:
                    raise
                env = ctx.envelope("asset", relative, [prov], processing_status="quarantined")
                ctx.add("assets", {**env, "asset_id": env["record_id"], "content_sha256": ctx.files[relative]["sha256"],
                    "media_type": "image/jpeg" if path.suffix.lower() == ".jpg" else "image/png",
                    "source_relative_path": relative, "storage_uri": str(source.relative_to(ctx.data_root)),
                    "modality": "reference_figure" if figure else "rgb", "subject_scope": "camera_view",
                    "localization_status": "group_view", "times": times, "availability": "quarantined", "decode_status": "failed",
                    "license_chain_refs": [f"{ctx.dataset}/{ctx.release}/repository-license"], "privacy_status": "not_reviewed",
                    "access_class": "restricted_raw", "source_fields": {"error_type": type(exc).__name__, "raw_bytes_preserved": True}})
                exclusions.append({"path": relative, "status": "quarantined", "reason": "verified_source_image_decode_failed", "error_type": type(exc).__name__})
                counts["images"] += 1
                counts["quarantined_images"] += 1
                continue
            images[relative] = (asset, stamp, prov)
            if not figure:
                ctx.observation(relative, subject, "visual.rgb", {"kind": "asset_ref", "value": asset}, relative, None, prov,
                                "camera_view", "visual_asset", times)
            counts["images"] += 1
        for relative in sorted(ctx.files):
            path = Path(relative)
            if path.suffix.lower() == ".csv":
                for encoding in ("utf-8-sig", "gb18030", "utf-16"):
                    try:
                        text = (ctx.source_root / relative).read_text(encoding=encoding)
                        break
                    except UnicodeError:
                        continue
                else:
                    raise ValueError("Source CSV encoding unresolved")
                reader = csv.DictReader(io.StringIO(text))
                headers = reader.fieldnames
                while True:
                    physical_row = reader.line_num + 1
                    try:
                        row = next(reader)
                    except StopIteration:
                        break
                    if None in row:
                        raise ValueError("CSV row exceeds source header")
                    prov = ctx.provenance(relative, "csv", row=physical_row)
                    self.csv_row(ctx, relative, physical_row, row, prov, images, counts)
                    counts["csv_rows"] += 1
            elif path.suffix.lower() == ".xlsx":
                book = openpyxl.load_workbook(ctx.source_root / relative, read_only=True, data_only=True)
                for sheet in book:
                    rows = sheet.iter_rows(values_only=True)
                    header = next(rows, ())
                    is_sensor = "sensor_data" in relative and header and header[0] in {"时间", "Time"}
                    for index, values in enumerate(rows, 2):
                        if not any(v is not None for v in values):
                            continue
                        prov = ctx.provenance(relative, "xlsx", sheet=sheet.title, cell=f"A{index}:{get_column_letter(len(values))}{index}")
                        if is_sensor:
                            year = re.search(r"/(202[345])/", relative)[1]
                            stamp = source_time(values[0])
                            unknown = {get_column_letter(i + 1): {"header": clean(header[i]), "value": clean(v)}
                                       for i, v in enumerate(values) if i and v is not None and not sensor_mapping(str(header[i]))}
                            if unknown:
                                document = ctx.subject("source-document/" + relative + "/" + sheet.title, "unknown", prov, resolution="unresolved")
                                annotation(ctx, f"{relative}/{sheet.title}/row-{index}/unknown", document, unknown, prov, "source_sensor_columns_unmapped")
                                counts["unmapped_sensor_cells"] += len(unknown)
                            for column, value in enumerate(values[1:], 1):
                                label = str(header[column])
                                mapping = sensor_mapping(label)
                                if not mapping:
                                    continue
                                variable, unit = mapping
                                # Full original column retains front/back and
                                # duplicated headers. No guessed plant assignment.
                                key = f"sensor/{year}/{path.parent.parent.parent}/{path.name}/column-{column}/{label}"
                                subject = ctx.subject(key, "plot", prov, cycle=f"source-year-{year}", resolution="source_group")
                                number, flags = scalar(value, unit)
                                processed = "processed_data" in relative
                                if processed:
                                    flags.append("SOURCE_PROCESSING_METHOD_UNRESOLVED")
                                ctx.observation(f"{relative}/{sheet.title}/{index}/{column}", subject, variable,
                                    {"kind": "scalar", "value": number}, clean(value), unit, prov, "plot",
                                    "source_processed_unknown" if processed else "sensor_measurement",
                                    {"event_time": stamp} if stamp else {}, flags, source_unit=unit,
                                    source_fields={"original_column": label, "uses_future_inputs": None if processed else False,
                                                   "scope_basis": "original sensor channel only; no plant/treatment crosswalk"})
                                counts["sensor_cells"] += 1
                        else:
                            subject = ctx.subject("source-document/" + relative + "/" + sheet.title, "unknown", prov, resolution="unresolved")
                            annotation(ctx, f"{relative}/{sheet.title}/row-{index}", subject,
                                       {"cells": {get_column_letter(i + 1): clean(v) for i,v in enumerate(values) if v is not None},
                                        "header": [clean(v) for v in header]}, prov, "source_tabular_row_unmapped")
                            counts["protected_auxiliary_rows"] += 1
                        counts["xlsx_rows"] += 1
                book.close()
            elif path.suffix.lower() in {".docx", ".md"}:
                prov = ctx.provenance(relative)
                subject = ctx.subject("source-document/" + relative, "knowledge_entry", prov, resolution="source_group")
                if path.suffix.lower() == ".docx":
                    import docx
                    doc = docx.Document(ctx.source_root / relative)
                    value = {"paragraphs": [p.text for p in doc.paragraphs], "tables": [[[c.text for c in r.cells] for r in t.rows] for t in doc.tables]}
                else:
                    value = (ctx.source_root / relative).read_text(encoding="utf-8-sig")
                annotation(ctx, "documentation/" + relative, subject, value, prov, "source_documentation_unmapped")
                counts["documents"] += 1
        total = sum(counts[k] for k in ("images", "csv_rows", "xlsx_rows", "documents"))
        return {"source_records_seen": total, "converted": total - counts["quarantined_images"], "preserved_unmapped": 0, "quarantined": counts["quarantined_images"], "rejected": 0,
                "source_record_definition": "one image, nonempty CSV/XLSX data row, or source document; protected unmapped fields counted separately",
                "counts": dict(counts), "source_files": len(ctx.files), "exclusions": exclusions,
                "limits": ["Auxiliary grade/phenology/yield/farming tables and source plans remain protected unmapped annotations",
                           "No invented plant crops or epoch capture times", "Sensor channel scopes remain separate from plants",
                           "Unknown illuminance units retain source values only; no klux/lux assumption"]}

    def csv_row(self, ctx, relative, index, row, prov, images, counts):
        path = Path(relative)
        year = re.search(r"/(202[345])/", relative)[1]
        key = f"{relative}/row-{index}"
        if "时间" in row:
            subject = ctx.subject(f"sensor/{year}/{path.name}", "plot", prov, cycle=f"source-year-{year}", resolution="source_group")
            stamp = source_time(row["时间"])
            unknown = {k:v for k,v in row.items() if k != "时间" and not sensor_mapping(k)}
            if unknown:
                annotation(ctx, key + "/unknown", subject, unknown, prov, "source_sensor_columns_unmapped")
                counts["unmapped_sensor_cells"] += len(unknown)
            for label, value in row.items():
                mapping = sensor_mapping(label)
                if not mapping:
                    continue
                variable, unit = mapping
                number, flags = scalar(value, unit)
                ctx.observation(key + "/" + label, subject, variable, {"kind": "scalar", "value": number}, value, unit, prov,
                                "plot", "sensor_measurement", {"event_time": stamp} if stamp else {}, flags,
                                source_fields={"original_column": label, "scope_basis": "source sensor/treatment file; no individual plant measurement"})
                counts["sensor_cells"] += 1
            return
        plant = row.get("Pid") or row.get("Number") or row.get("编号")
        if not plant:
            raise ValueError("Growth row lacks original plant token")
        subject = ctx.subject(f"plant/{year}/{plant}", "individual_plant", prov, cycle=f"source-year-{year}",
                              source_fields={"source_plant_token": plant, "prefixes_preserved": True})
        raw_date = row.get("日期") or row.get("Sheet Name")
        if not raw_date and re.fullmatch(r"\d{8}", path.stem):
            raw_date = f"{path.stem[:4]}-{path.stem[4:6]}-{path.stem[6:8]}"
        stamp = source_time(raw_date)
        for label, value in row.items():
            if label not in GROWTH:
                continue
            variable, unit = GROWTH[label]
            number, flags = scalar(value, unit)
            interpolated = "after interpolation" in relative
            processed = "processed_data" in relative or variable.startswith("spectral.")
            evidence = "source_interpolated" if interpolated else "source_processed_unknown" if processed else "manual_measurement"
            if processed:
                flags.append("SOURCE_INTERPOLATED" if interpolated else "SOURCE_PROCESSING_METHOD_UNRESOLVED")
            ctx.observation(key + "/" + label, subject, variable, {"kind": "scalar", "value": number}, value, unit, prov,
                            "individual_plant", evidence, {"event_time": stamp} if stamp else {}, flags,
                            source_fields={"original_column": label, "uses_future_inputs": None if processed else False})
            counts["growth_cells"] += 1
        extra = {k:v for k,v in row.items() if k not in GROWTH}
        annotation(ctx, key + "/source_context", subject, extra, prov, "source_row_context_unmapped")
        for order, reference in enumerate((row.get("Photo Path") or "").split(";")):
            if not reference.strip():
                continue
            base = "/".join(Path(relative).parts[:Path(relative).parts.index("processed_data")])
            target = posixpath.normpath(base + "/" + reference.replace("\\", "/"))
            counts["image_reference_edges"] += 1
            if target not in images:
                counts["quarantined_image_reference_edges" if target in ctx.files else "missing_image_reference_edges"] += 1
                continue
            asset, image_time, image_prov = images[target]
            future = None
            if stamp and image_time:
                future = image_time["value"][:10] > stamp["value"][:10]
            ctx.link(key + f"/photo-{order}", subject, asset, "source_table_references_group_image", [prov, image_prov],
                     "camera_view", future_dependence=future,
                     source_fields={"reference_string": reference, "not_individual_localization": True,
                                    "anchor_source_time": stamp, "image_source_time": image_time})
            counts["future_image_reference_edges"] += future is True
            counts["unknown_time_image_reference_edges"] += future is None
